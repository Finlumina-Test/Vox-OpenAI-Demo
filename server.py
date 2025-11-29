# server.py - PART 1: IMPORTS AND HELPER FUNCTIONS

import os
import json
import time
import base64
import asyncio
import secrets
from typing import Set, Optional, Dict, Any
from datetime import datetime

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse, Response
from fastapi.websockets import WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from twilio.twiml.voice_response import VoiceResponse

from config import Config
from services import (
    WebSocketConnectionManager,
    TwilioService,
    OpenAIService,
    AudioService,
)
from services.order_extraction_service import OrderExtractionService
from services.transcription_service import TranscriptionService
from services.log_utils import Log
from services.silence_detection import SilenceDetector


# ===== DEMO SESSION TRACKING =====
demo_sessions = {}
demo_pending_start = {} # Sessions waiting for key press

# ===== DASHBOARD CLIENTS =====
class DashboardClient:
    def __init__(self, websocket: WebSocket, call_sid: Optional[str] = None):
        self.websocket = websocket
        self.call_sid = call_sid

active_calls: Dict[str, Dict[str, Any]] = {}
dashboard_clients: Set[DashboardClient] = set()


# ===== BROADCAST HELPER =====
async def _do_broadcast(payload: Dict[str, Any], call_sid: Optional[str] = None):
    try:
        if "timestamp" not in payload or payload["timestamp"] is None:
            payload["timestamp"] = int(time.time() * 1000)
        else:
            ts = float(payload["timestamp"])
            if ts < 32503680000:
                payload["timestamp"] = int(ts * 1000)
            else:
                payload["timestamp"] = int(ts)
    except Exception:
        payload["timestamp"] = int(time.time() * 1000)

    if call_sid and "callSid" not in payload:
        payload["callSid"] = call_sid

    text = json.dumps(payload)
    to_remove = []
    
    for client in list(dashboard_clients):
        try:
            should_send = (
                client.call_sid is None or
                client.call_sid == call_sid
            )
            if should_send:
                await client.websocket.send_text(text)
        except Exception as e:
            Log.debug(f"Failed to send to client: {e}")
            to_remove.append(client)
    
    for c in to_remove:
        dashboard_clients.discard(c)


def broadcast_to_dashboards_nonblocking(payload: Dict[str, Any], call_sid: Optional[str] = None):
    try:
        asyncio.create_task(_do_broadcast(payload, call_sid))
    except Exception as e:
        Log.error(f"[Broadcast] Failed to create broadcast task: {e}")


# ===== EMAIL HELPER =====
def send_call_summary_email(call_sid: str, session_id: str = None, phone: str = 'Unknown', duration_seconds: int = None, rating: int = None, ended_early: bool = False):
    """Send call summary email (with or without rating)."""
    Log.info("=" * 80)
    Log.info("📧 SEND_CALL_SUMMARY_EMAIL CALLED")
    Log.info("=" * 80)
    Log.info(f"  call_sid: {call_sid}")
    Log.info(f"  session_id: {session_id}")
    Log.info(f"  phone: {phone}")
    Log.info(f"  duration_seconds: {duration_seconds}")
    Log.info(f"  rating: {rating}")
    Log.info(f"  ended_early: {ended_early}")
    
    try:
        if not Config.has_email_configured():
            Log.warning("📧 Resend not configured - skipping email")
            Log.warning(f"  RESEND_API_KEY present: {bool(Config.RESEND_API_KEY)}")
            return
        
        Log.info("✅ Email is configured - proceeding...")
        
        import resend
        resend.api_key = Config.RESEND_API_KEY
        
        Log.info(f"✅ Resend API key set: {Config.RESEND_API_KEY[:10]}...")
        
        # Build subject
        if rating:
            subject = f"VOX Demo Rating: {rating}/5 {'⭐' * rating}"
        elif ended_early:
            subject = f"VOX Demo - Call Ended Early - {call_sid[:8]}"
        else:
            subject = f"VOX Demo Call Summary - {call_sid[:8]}"
        
        Log.info(f"📧 Email subject: {subject}")
        
        # Build duration string
        duration_str = "Unknown"
        if duration_seconds is not None:
            if duration_seconds < 60:
                duration_str = f"{duration_seconds}s"
            else:
                mins = duration_seconds // 60
                secs = duration_seconds % 60
                duration_str = f"{mins}m {secs}s"
        
        # Build feedback section
        if rating:
            feedback_html = f'<p><strong>⭐ Rating:</strong> {rating}/5 {"⭐" * rating}</p>'
            feedback_status = f"User rated: {rating}/5"
        elif ended_early:
            feedback_html = '<p><strong>❌ Feedback:</strong> <span style="color: #ff6b6b;">No feedback available - User ended call early</span></p>'
            feedback_status = "No feedback - ended early"
        else:
            feedback_html = '<p><strong>ℹ️ Feedback:</strong> Call completed without rating</p>'
            feedback_status = "No rating collected"
        
        # Build HTML body
        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #2c3e50;">{'🎉 New VOX Demo Feedback!' if rating else '📞 VOX Demo Call Summary'}</h2>
            
            <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                {feedback_html}
                <p><strong>📱 Phone:</strong> {phone}</p>
                <p><strong>🆔 Call SID:</strong> <code style="background: #e9ecef; padding: 2px 6px; border-radius: 3px;">{call_sid}</code></p>
                <p><strong>🔑 Session ID:</strong> {session_id or 'N/A'}</p>
                <p><strong>⏱️ Duration:</strong> {duration_str}</p>
                <p><strong>🕐 Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </div>
            
            {f'<p style="text-align: center; margin: 20px 0;"><a href="https://vox.finlumina.com/demo/{session_id}" style="background: #3498db; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">📊 View Dashboard</a></p>' if session_id else ''}
            
            <hr style="border: none; border-top: 1px solid #dee2e6; margin: 30px 0;">
            
            <p style="color: #6c757d; font-size: 12px; text-align: center;">
                <strong>Finlumina VOX Demo System</strong><br>
                Status: {feedback_status}
            </p>
        </div>
        """
        
        params = {
            "from": "VOX Demo <onboarding@resend.dev>",
            "to": [Config.FEEDBACK_EMAIL],
            "subject": subject,
            "html": html_body,
        }
        
        Log.info(f"📧 Sending email to: {Config.FEEDBACK_EMAIL}")
        Log.info(f"📧 Email params: from={params['from']}, to={params['to']}, subject={params['subject']}")
        
        result = resend.Emails.send(params)
        
        Log.info(f"✅ Resend API response: {result}")
        Log.info(f"✅ Call summary email sent to {Config.FEEDBACK_EMAIL} ({feedback_status})")
        
    except Exception as e:
        Log.error(f"📧 Could not send call summary email: {e}")
        import traceback
        Log.error(f"Traceback: {traceback.format_exc()}")


# ===== FASTAPI APP =====
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== ENDPOINTS =====
@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call - generate session and speak URL via TwiML."""
    try:
        form_data = await request.form()
        call_sid = form_data.get('CallSid')
        from_phone = form_data.get('From')
        
        restaurant_id = request.query_params.get('restaurant_id') or request.headers.get('X-Restaurant-ID', 'default')
        
        # Generate session ID (short and easy)
        session_id = secrets.token_urlsafe(6)
        
        demo_pending_start[session_id] = {
            'call_sid': call_sid,
            'phone': from_phone,
            'created_at': time.time(),
            'restaurant_id': restaurant_id
        }
        
        Log.info(f"📞 Incoming call: {call_sid} for restaurant: {restaurant_id}")
        Log.info(f"🎯 Session ID: {session_id}")
        Log.info(f"📊 Dashboard: https://vox.finlumina.com/demo/{session_id}")
        
        # Build backend URL
        backend_url = f"https://{request.url.hostname}"
        
        # 🔥 Create response with status callback
        from twilio.twiml.voice_response import VoiceResponse as TwilioVoiceResponse
        
        response = TwilioVoiceResponse()
        
        # 🔥 Set status callback to track hangups
        status_callback_url = f"{backend_url}/call-status"
        
        # Get intro TwiML
        intro_twiml_str = TwilioService.create_demo_intro_twiml(session_id, backend_url)
        
        # Parse and merge TwiML with status callback
        # We need to add statusCallback to the root Response element
        intro_twiml_str = intro_twiml_str.replace(
            '<Response>',
            f'<Response statusCallback="{status_callback_url}" statusCallbackMethod="POST" statusCallbackEvent="completed failed">'
        )
        
        return Response(content=intro_twiml_str, media_type="application/xml")
        
    except Exception as e:
        Log.error(f"Error handling incoming call: {e}")
        import traceback
        Log.error(f"Traceback: {traceback.format_exc()}")
        
        from twilio.twiml.voice_response import VoiceResponse as TwilioVoiceResponse
        response = TwilioVoiceResponse()
        response.say("Error starting demo. Please try again.", voice=TwilioService.TWILIO_VOICE)
        return Response(content=str(response), media_type="application/xml")



@app.api_route("/demo-start", methods=["POST", "GET"])
async def handle_demo_start(request: Request):
    """Handle key press to start demo."""
    try:
        if request.method == "POST":
            form_data = await request.form()
            call_sid = form_data.get('CallSid')
            digits = form_data.get('Digits', 'auto')
        else:
            call_sid = request.query_params.get('CallSid')
            digits = request.query_params.get('auto', 'auto')
        
        skipped = digits != 'auto'
        
        Log.info(f"🎬 Demo start requested for {call_sid} (pressed: {digits}, skipped: {skipped})")
        
        session_id = None
        for sid, data in demo_pending_start.items():
            if data['call_sid'] == call_sid:
                session_id = sid
                break
        
        if session_id:
            demo_sessions[session_id] = demo_pending_start.pop(session_id)
            demo_sessions[session_id]['started_at'] = time.time()
            demo_sessions[session_id]['demo_active'] = True
            Log.info(f"✅ Demo activated for session: {session_id} (restaurant: {demo_sessions[session_id].get('restaurant_id')})")
        
        backend_host = request.url.hostname
        twiml = TwilioService.create_demo_start_twiml(backend_host, skipped=skipped)
        
        return Response(content=twiml, media_type="application/xml")
        
    except Exception as e:
        Log.error(f"Error starting demo: {e}")
        backend_host = request.url.hostname
        twiml = TwilioService.create_demo_start_twiml(backend_host, skipped=False)
        return Response(content=twiml, media_type="application/xml")

@app.api_route("/demo-rating", methods=["POST"])
async def demo_rating(request: Request):
    """Handle feedback rating from keypad."""
    try:
        form_data = await request.form()
        digits = form_data.get('Digits', '')
        call_sid = form_data.get('CallSid', '')
        from_phone = form_data.get('From', 'Unknown')
        
        Log.info(f"📊 Received rating: {digits} from {call_sid}")
        
        # Validate rating
        try:
            rating = int(digits)
            if rating < 1 or rating > 5:
                backend_url = f"https://{request.url.hostname}"
                twiml = TwilioService.create_invalid_rating_twiml(backend_url)
                return Response(content=twiml, media_type="application/xml")
        except:
            backend_url = f"https://{request.url.hostname}"
            twiml = TwilioService.create_invalid_rating_twiml(backend_url)
            return Response(content=twiml, media_type="application/xml")
        
        # Find session for this call
        session_id = None
        session_data = None
        for sid, data in demo_sessions.items():
            if data.get('call_sid') == call_sid:
                session_id = sid
                session_data = data
                break
        
        # Calculate duration
        duration = None
        if session_data and session_data.get('started_at'):
            duration = int(time.time() - session_data['started_at'])
        
        # 🔥 Send email with rating (NOT ended_early)
        send_call_summary_email(
            call_sid=call_sid,
            session_id=session_id,
            phone=from_phone,
            duration_seconds=duration,
            rating=rating,
            ended_early=False  # 🔥 Has rating, so not early
        )
        
        # Thank user and end call
        twiml = TwilioService.create_rating_response_twiml(rating)
        return Response(content=twiml, media_type="application/xml")
        
    except Exception as e:
        Log.error(f"Rating handler error: {e}")
        from twilio.twiml.voice_response import VoiceResponse as TwilioVoiceResponse
        response = TwilioVoiceResponse()
        response.say("Thank you. Goodbye!", voice=TwilioService.TWILIO_VOICE)
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

@app.api_route("/call-status", methods=["GET", "POST"])
async def handle_call_status(request: Request):
    """
    Handle call status:
    - GET: Frontend check if call has ended (returns JSON)
    - POST: Twilio status callback (hangup tracking)
    """
    try:
        # GET request from frontend - check if call has ended
        if request.method == "GET":
            call_sid = request.query_params.get('callSid')

            if not call_sid:
                return JSONResponse({"error": "Missing callSid parameter"}, status_code=400)

            # Check if call is still active in active_calls
            is_active = call_sid in active_calls

            # Check if call ended (exists in demo_sessions but not in active_calls)
            session_data = None
            for sid, data in demo_sessions.items():
                if data.get('call_sid') == call_sid:
                    session_data = data
                    break

            # Also check pending sessions
            if not session_data:
                for sid, data in demo_pending_start.items():
                    if data.get('call_sid') == call_sid:
                        session_data = data
                        break

            return JSONResponse({
                "callSid": call_sid,
                "isActive": is_active,
                "hasEnded": not is_active and session_data is not None,
                "exists": session_data is not None
            })

        # POST request from Twilio - status callback
        Log.info("=" * 80)
        Log.info("🔥 CALL STATUS CALLBACK RECEIVED")
        Log.info("=" * 80)
        
        form_data = await request.form()
        
        # Log ALL form data
        Log.info(f"📋 All form data: {dict(form_data)}")
        
        call_sid = form_data.get('CallSid')
        call_status = form_data.get('CallStatus')
        from_phone = form_data.get('From', 'Unknown')
        call_duration = form_data.get('CallDuration', '0')
        
        Log.info(f"📞 [StatusCallback] CallSid: {call_sid}")
        Log.info(f"📞 [StatusCallback] Status: {call_status}")
        Log.info(f"📞 [StatusCallback] From: {from_phone}")
        Log.info(f"📞 [StatusCallback] Duration: {call_duration}s")
        
        # Only process completed/failed calls
        if call_status in ['completed', 'failed', 'busy', 'no-answer', 'canceled']:
            Log.info(f"✅ Status matches - processing...")

            # Send WebSocket notification to frontend for auto-save
            from datetime import datetime
            broadcast_to_dashboards_nonblocking({
                "messageType": "callEnded",
                "callId": call_sid,
                "status": call_status,
                "timestamp": datetime.utcnow().isoformat()
            }, call_sid)
            Log.info(f"📨 Sent callEnded WebSocket message to frontend for {call_sid}")

            # Find session for this call
            session_id = None
            phone = from_phone
            
            # Check active sessions first
            Log.info(f"🔍 Checking active sessions: {list(demo_sessions.keys())}")
            for sid, data in demo_sessions.items():
                if data.get('call_sid') == call_sid:
                    session_id = sid
                    phone = data.get('phone', from_phone)
                    Log.info(f"✅ Found in active sessions: {session_id}")
                    break
            
            # Check pending sessions (hung up before pressing key)
            if not session_id:
                Log.info(f"🔍 Checking pending sessions: {list(demo_pending_start.keys())}")
                for sid, data in demo_pending_start.items():
                    if data.get('call_sid') == call_sid:
                        session_id = sid
                        phone = data.get('phone', from_phone)
                        Log.info(f"✅ Found in pending sessions: {session_id}")
                        break
            
            if not session_id:
                Log.warning(f"⚠️ Session not found for call {call_sid}")
            
            # Send email for early hangups
            try:
                duration_int = int(call_duration) if call_duration else 0
            except:
                duration_int = 0
            
            Log.info(f"⏱️ Call duration: {duration_int}s")
            
            # 🔥 Only send if call was very short (< 55 seconds = ended before demo timer)
            if duration_int < 55:
                Log.info(f"📧 Call duration < 55s - sending early hangup email...")
                send_call_summary_email(
                    call_sid=call_sid,
                    session_id=session_id,
                    phone=phone,
                    duration_seconds=duration_int,
                    rating=None,
                    ended_early=True  # 🔥 Flag as early hangup
                )
                Log.info(f"✅ Email sent for early hangup: {call_sid} ({duration_int}s)")
            else:
                Log.info(f"ℹ️ Call duration >= 55s - skipping email (will be sent by /media-stream cleanup)")
        else:
            Log.info(f"ℹ️ Status '{call_status}' not in target list - skipping")
        
        Log.info("=" * 80)
        return Response(content="OK", status_code=200)

    except Exception as e:
        Log.error(f"[StatusCallback] Error: {e}")
        import traceback
        Log.error(f"Traceback: {traceback.format_exc()}")
        return Response(content="ERROR", status_code=200)


async def notify_frontend_audio_upload(call_sid: str, audio_url: str, retry_count: int = 0) -> bool:
    """
    Notify frontend that audio URL is available with retry handling for race conditions.

    Args:
        call_sid: Twilio call SID
        audio_url: Public URL to the audio file in Supabase Storage
        retry_count: Current retry attempt (max 9 retries = 10 total attempts)

    Returns:
        True if notification successful, False otherwise
    """
    if not Config.FRONTEND_URL:
        return False

    try:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{Config.FRONTEND_URL}/api/calls/save",
                json={
                    "call_id": call_sid,  # Frontend expects call_id
                    "audio_url": audio_url,
                    "update_audio_only": True,  # Only update audio URL, don't create full record
                    "retry_count": retry_count
                },
                timeout=10.0
            )

            # Handle successful update
            if response.status_code == 200:
                Log.info(f"✅ Audio URL updated: {call_sid}")
                return True

            # Handle 404 with retry flag (race condition - call not saved yet)
            if response.status_code == 404:
                try:
                    data = response.json()
                    if data.get("retry") and retry_count < 9:  # Max 10 attempts (0-9)
                        retry_after_ms = data.get("retry_after", 1000)  # Default 1 second
                        retry_after_sec = retry_after_ms / 1000
                        Log.info(f"⏳ Call not found yet (attempt {retry_count + 1}/10), waiting {retry_after_sec}s...")
                        await asyncio.sleep(retry_after_sec)
                        return await notify_frontend_audio_upload(call_sid, audio_url, retry_count + 1)
                    else:
                        Log.error(f"❌ Failed to update audio URL: Call not found after {retry_count + 1} attempts - {call_sid}")
                        return False
                except Exception as parse_error:
                    Log.error(f"❌ Failed to parse 404 response: {parse_error}")
                    return False

            # Handle other error responses
            try:
                data = response.json()
                Log.warning(f"⚠️ Failed to update audio URL: {response.status_code} - {data}")
            except:
                Log.warning(f"⚠️ Frontend returned {response.status_code}: {response.text}")
            return False

    except Exception as e:
        Log.warning(f"⚠️ Failed to notify frontend: {e}")
        return False


@app.api_route("/recording-status", methods=["POST"])
async def handle_recording_status(request: Request):
    """
    Handle Twilio recording status callback.
    Stores recording URL in Supabase when recording is completed.
    """
    try:
        form_data = await request.form()

        Log.info("=" * 80)
        Log.info("🎙️ RECORDING STATUS CALLBACK RECEIVED")
        Log.info("=" * 80)

        # Log all form data for debugging
        Log.info(f"📋 Recording callback data: {dict(form_data)}")

        # Extract recording data
        recording_sid = form_data.get('RecordingSid')
        recording_url = form_data.get('RecordingUrl')
        recording_status = form_data.get('RecordingStatus')
        call_sid = form_data.get('CallSid')
        recording_duration = form_data.get('RecordingDuration', '0')

        Log.info(f"🎙️ RecordingSid: {recording_sid}")
        Log.info(f"🎙️ RecordingUrl: {recording_url}")
        Log.info(f"🎙️ Status: {recording_status}")
        Log.info(f"🎙️ CallSid: {call_sid}")
        Log.info(f"🎙️ Duration: {recording_duration}s")

        # Only process completed recordings
        if recording_status == 'completed' and recording_url:
            # Add .mp3 extension to URL for direct download
            full_recording_url = f"{recording_url}.mp3"

            Log.info(f"✅ Recording completed: {full_recording_url}")

            # Find session data for this call
            session_id = None
            session_data = None

            for sid, data in demo_sessions.items():
                if data.get('call_sid') == call_sid:
                    session_id = sid
                    session_data = data
                    break

            if not session_data:
                for sid, data in demo_pending_start.items():
                    if data.get('call_sid') == call_sid:
                        session_id = sid
                        session_data = data
                        break

            # Download from Twilio and upload to Supabase Storage
            if Config.has_supabase_configured():
                try:
                    import httpx
                    from supabase import create_client

                    supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

                    # Step 1: Download audio from Twilio
                    Log.info(f"📥 Downloading audio from Twilio: {full_recording_url}")

                    async with httpx.AsyncClient() as client:
                        # Use basic auth with Twilio credentials
                        auth = (Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
                        response = await client.get(full_recording_url, auth=auth, timeout=30.0)
                        response.raise_for_status()
                        audio_bytes = response.content

                    Log.info(f"✅ Downloaded {len(audio_bytes)} bytes from Twilio")

                    # Step 2: Upload to Supabase Storage
                    # Use recording_sid to avoid duplicates if multiple recordings per call
                    file_name = f"{call_sid}_{recording_sid}.mp3"
                    Log.info(f"📤 Uploading to Supabase Storage: {Config.SUPABASE_BUCKET}/{file_name}")

                    storage_response = supabase.storage.from_(Config.SUPABASE_BUCKET).upload(
                        path=file_name,
                        file=audio_bytes,
                        file_options={
                            "content-type": "audio/mpeg",
                            "upsert": "true"  # Overwrite if exists (shouldn't happen but just in case)
                        }
                    )

                    Log.info(f"✅ Uploaded to Supabase Storage: {file_name}")

                    # Step 3: Get public URL
                    public_url = supabase.storage.from_(Config.SUPABASE_BUCKET).get_public_url(file_name)
                    Log.info(f"🔗 Public URL: {public_url}")

                    # Step 4: Update only audio_url in database (don't touch other fields)
                    from datetime import datetime

                    result = supabase.table(Config.SUPABASE_TABLE).update({
                        'audio_url': public_url,
                        'updated_at': datetime.utcnow().isoformat()
                    }).eq('call_sid', call_sid).execute()

                    Log.info(f"✅ Recording audio URL updated in database: {recording_sid}")
                    Log.info(f"📊 Database response: {result}")

                    # Notify frontend that audio URL is available (with retry handling)
                    await notify_frontend_audio_upload(call_sid, public_url)

                except Exception as e:
                    Log.error(f"❌ Failed to download/upload recording: {e}")
                    import traceback
                    Log.error(f"Traceback: {traceback.format_exc()}")
            else:
                Log.warning("⚠️ Supabase not configured - recording URL not stored")
                Log.warning(f"💡 Recording URL: {full_recording_url}")

        Log.info("=" * 80)
        return Response(content="OK", status_code=200)

    except Exception as e:
        Log.error(f"[RecordingCallback] Error: {e}")
        import traceback
        Log.error(f"Traceback: {traceback.format_exc()}")
        return Response(content="ERROR", status_code=200)


@app.get("/api/validate-session/{session_id}")
async def validate_session(session_id: str):
    """Validate if a demo session exists and is active (case-insensitive)."""
    try:
        Log.info(f"🔍 Validating session: {session_id}")
        
        session_id_lower = session_id.lower()
        
        for sid, data in demo_pending_start.items():
            if sid.lower() == session_id_lower:
                Log.info(f"✅ Found pending session: {sid}")
                return JSONResponse({
                    "valid": True,
                    "status": "pending",
                    "sessionId": sid,
                    "callSid": data.get('call_sid'),
                    "restaurantId": data.get('restaurant_id', 'demo'),
                    "createdAt": data.get('created_at')
                })
        
        for sid, data in demo_sessions.items():
            if sid.lower() == session_id_lower:
                Log.info(f"✅ Found active session: {sid}")
                return JSONResponse({
                    "valid": True,
                    "status": "active",
                    "sessionId": sid,
                    "callSid": data.get('call_sid'),
                    "restaurantId": data.get('restaurant_id', 'demo'),
                    "startedAt": data.get('started_at')
                })
        
        Log.warning(f"⚠️ Session not found: {session_id}")
        Log.info(f"📋 Available pending sessions: {list(demo_pending_start.keys())}")
        Log.info(f"📋 Available active sessions: {list(demo_sessions.keys())}")
        return JSONResponse({
            "valid": False,
            "error": "Session not found or expired"
        }, status_code=404)
        
    except Exception as e:
        Log.error(f"❌ Session validation error: {e}")
        return JSONResponse({
            "valid": False,
            "error": "Validation failed"
        }, status_code=500)
        

@app.websocket("/dashboard-stream")
async def dashboard_stream(websocket: WebSocket):
    await websocket.accept()
    DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN")
    client_call_id: Optional[str] = None
    client_session_id: Optional[str] = None

    if DASHBOARD_TOKEN:
        provided = websocket.query_params.get("token") or websocket.headers.get("x-dashboard-token")
        if provided != DASHBOARD_TOKEN:
            await websocket.close(code=4003)
            return

    try:
        msg = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        data = json.loads(msg)
        client_call_id = data.get("callId")
        client_session_id = data.get("sessionId")
        
        if client_session_id and not client_call_id:
            if client_session_id in demo_sessions:
                client_call_id = demo_sessions[client_session_id].get('call_sid')
                Log.info(f"📡 Dashboard subscribed to session {client_session_id} → call {client_call_id}")
            elif client_session_id in demo_pending_start:
                client_call_id = demo_pending_start[client_session_id].get('call_sid')
                Log.info(f"📡 Dashboard subscribed to pending session {client_session_id} → call {client_call_id}")
            else:
                Log.warning(f"⚠️ Session {client_session_id} not found")
        
        if client_call_id:
            Log.info(f"Dashboard client subscribed to call: {client_call_id}")
        else:
            Log.info("Dashboard client subscribed to ALL calls")
            
    except (asyncio.TimeoutError, json.JSONDecodeError, KeyError):
        Log.info("Dashboard client subscribed to ALL calls")
        client_call_id = None

    client = DashboardClient(websocket, client_call_id)
    dashboard_clients.add(client)
    Log.info(f"Dashboard connected. Total clients: {len(dashboard_clients)}")
    
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=20.0)
            except asyncio.TimeoutError:
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        dashboard_clients.discard(client)
        Log.info(f"Dashboard disconnected. Total clients: {len(dashboard_clients)}")
        

@app.websocket("/human-audio/{call_sid}")
async def human_audio_stream(websocket: WebSocket, call_sid: str):
    await websocket.accept()
    
    Log.info(f"[HumanAudio] Connected for call {call_sid}")
    
    if call_sid not in active_calls:
        Log.error(f"[HumanAudio] Call {call_sid} not found in active_calls")
        await websocket.close(code=4004, reason="Call not found")
        return
    
    openai_service = active_calls[call_sid].get("openai_service")
    connection_manager = active_calls[call_sid].get("connection_manager")
    
    if not openai_service or not connection_manager:
        Log.error(f"[HumanAudio] Services not available for call {call_sid}")
        await websocket.close(code=4005, reason="Services not available")
        return
    
    active_calls[call_sid]["human_audio_ws"] = websocket
    
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            
            if data.get("type") == "audio":
                audio_base64 = data.get("audio")
                
                if audio_base64 and openai_service.is_human_in_control():
                    stream_sid = getattr(connection_manager.state, 'stream_sid', None)
                    if stream_sid:
                        twilio_message = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_base64
                            }
                        }
                        await connection_manager.send_to_twilio(twilio_message)
                    
                    await openai_service.send_human_audio_to_openai(
                        audio_base64,
                        connection_manager
                    )
                    
    except WebSocketDisconnect:
        Log.info(f"[HumanAudio] Disconnected for call {call_sid}")
    except Exception as e:
        Log.error(f"[HumanAudio] Error: {e}")
    finally:
        if call_sid in active_calls and "human_audio_ws" in active_calls[call_sid]:
            del active_calls[call_sid]["human_audio_ws"]
        
        if openai_service and openai_service.is_human_in_control():
            openai_service.disable_human_takeover()
            
            try:
                await connection_manager.send_to_openai({
                    "type": "input_audio_buffer.clear"
                })
            except Exception:
                pass
            
            broadcast_to_dashboards_nonblocking({
                "messageType": "takeoverStatus",
                "active": False,
                "callSid": call_sid
            }, call_sid)


@app.api_route("/takeover", methods=["POST"])
async def handle_takeover(request: Request):
    try:
        data = await request.json()
        call_sid = data.get("callSid")
        action = data.get("action")
        restaurant_id = data.get("restaurantId")
        
        Log.info(f"[Takeover] Request: {action} for call {call_sid} (restaurant: {restaurant_id})")
        
        if not call_sid or action not in ["enable", "disable"]:
            return JSONResponse({"error": "Invalid request"}, status_code=400)
        
        if call_sid not in active_calls:
            Log.error(f"[Takeover] Call {call_sid} not found in active_calls")
            Log.error(f"[Takeover] Available calls: {list(active_calls.keys())}")
            return JSONResponse({"error": "Call not found"}, status_code=404)
        
        call_data = active_calls[call_sid]
        stored_restaurant_id = call_data.get("restaurant_id", "default")
        
        # 🔥 FIX: Allow 'demo' to match 'default' for demo calls
        if restaurant_id and restaurant_id != stored_restaurant_id:
            if not (restaurant_id == "demo" and stored_restaurant_id == "default"):
                Log.error(f"[Takeover] Restaurant ID mismatch: expected {stored_restaurant_id}, got {restaurant_id}")
                return JSONResponse({"error": "Restaurant ID mismatch"}, status_code=403)
        
        openai_service = call_data.get("openai_service")
        connection_manager = call_data.get("connection_manager")
        
        if not openai_service or not connection_manager:
            return JSONResponse({"error": "Service not available"}, status_code=500)
        
        if action == "enable":
            openai_service.enable_human_takeover()

            # 🔥 CRITICAL: Clear Twilio audio buffer IMMEDIATELY
            try:
                stream_sid = getattr(connection_manager.state, 'stream_sid', None)
                if stream_sid:
                    clear_message = {
                        "event": "clear",
                        "streamSid": stream_sid
                    }
                    await connection_manager.send_to_twilio(clear_message)
                    Log.info(f"[Takeover] 🔇 Cleared Twilio audio buffer (dropped AI audio)")
            except Exception as e:
                Log.error(f"[Takeover] Failed to clear Twilio buffer: {e}")

            # Cancel any ongoing AI response
            try:
                await connection_manager.send_to_openai({
                    "type": "response.cancel"
                })
                Log.info(f"[Takeover] Cancelled AI response")
            except Exception:
                Log.debug(f"[Takeover] No active response to cancel (normal)")

            # Clear input audio buffer
            try:
                await connection_manager.send_to_openai({
                    "type": "input_audio_buffer.clear"
                })
            except Exception:
                pass
            
            Log.info(f"[Takeover] ✅ ENABLED for call {call_sid}")
            
            broadcast_to_dashboards_nonblocking({
                "messageType": "takeoverStatus",
                "active": True,
                "callSid": call_sid
            }, call_sid)
            
            return JSONResponse({"success": True, "message": "Takeover enabled"})
        else:
            openai_service.disable_human_takeover()
            
            try:
                await connection_manager.send_to_openai({
                    "type": "response.cancel"
                })
            except Exception:
                pass
            
            try:
                await connection_manager.send_to_openai({
                    "type": "input_audio_buffer.clear"
                })
            except Exception:
                pass
            
            await asyncio.sleep(0.3)
            
            try:
                await connection_manager.send_to_openai({
                    "type": "input_audio_buffer.commit"
                })
            except Exception:
                pass
            
            Log.info(f"[Takeover] ✅ DISABLED for call {call_sid}")
            
            broadcast_to_dashboards_nonblocking({
                "messageType": "takeoverStatus",
                "active": False,
                "callSid": call_sid
            }, call_sid)
            
            return JSONResponse({"success": True, "message": "Takeover disabled"})
            
    except Exception as e:
        Log.error(f"[Takeover] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
        

@app.api_route("/end-call", methods=["POST"])
async def handle_end_call(request: Request):
    try:
        data = await request.json()
        call_sid = data.get("callSid")
        restaurant_id = data.get("restaurantId")
        
        Log.info(f"[EndCall] Request to end call {call_sid} (restaurant: {restaurant_id})")
        
        if not call_sid:
            return JSONResponse({"error": "Invalid request"}, status_code=400)
        
        if call_sid not in active_calls:
            Log.warning(f"[EndCall] Call {call_sid} not in active_calls (might have ended)")
        else:
            call_data = active_calls[call_sid]
            stored_restaurant_id = call_data.get("restaurant_id", "default")
            
            # 🔥 FIX: Allow 'demo' to match 'default' for demo calls
            if restaurant_id and restaurant_id != stored_restaurant_id:
                if not (restaurant_id == "demo" and stored_restaurant_id == "default"):
                    Log.error(f"[EndCall] Restaurant ID mismatch: expected {stored_restaurant_id}, got {restaurant_id}")
                    return JSONResponse({"error": "Restaurant ID mismatch"}, status_code=403)
            
            openai_service = call_data.get("openai_service")
            if openai_service and openai_service.is_human_in_control():
                openai_service.disable_human_takeover()
        
        if Config.has_twilio_credentials():
            try:
                from twilio.rest import Client
                from twilio.base.exceptions import TwilioRestException
                
                client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
                client.calls(call_sid).update(status='completed')
                
                Log.info(f"[EndCall] ✅ Call {call_sid} ended successfully")
                
                broadcast_to_dashboards_nonblocking({
                    "messageType": "callEnded",
                    "callSid": call_sid,
                    "timestamp": int(time.time() * 1000)
                }, call_sid)
                
                return JSONResponse({
                    "success": True, 
                    "message": "Call ended successfully"
                })
                
            except TwilioRestException as e:
                if e.status == 404:
                    Log.info(f"[EndCall] Call already ended - this is fine")
                    
                    broadcast_to_dashboards_nonblocking({
                        "messageType": "callEnded",
                        "callSid": call_sid,
                        "timestamp": int(time.time() * 1000)
                    }, call_sid)
                    
                    return JSONResponse({
                        "success": True, 
                        "message": "Call already ended"
                    })
                else:
                    Log.error(f"[EndCall] Twilio error: {e}")
                    return JSONResponse({
                        "error": f"Twilio error: {str(e)}"
                    }, status_code=500)
            except Exception as e:
                Log.error(f"[EndCall] Error: {e}")
                return JSONResponse({
                    "error": str(e)
                }, status_code=500)
        else:
            return JSONResponse({
                "error": "Twilio credentials not configured"
            }, status_code=500)
            
    except Exception as e:
        Log.error(f"[EndCall] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    Log.header("=" * 80)
    Log.header("🔌 NEW MEDIA STREAM CONNECTION")
    Log.header("=" * 80)
    await websocket.accept()
    Log.info("✅ WebSocket accepted")

    connection_manager = WebSocketConnectionManager(websocket)
    openai_service = OpenAIService()
    audio_service = AudioService()
    order_extractor = OrderExtractionService()
    transcription_service = TranscriptionService()
    
    caller_silence_detector = SilenceDetector()
    ai_silence_detector = SilenceDetector()
    
    current_call_sid: Optional[str] = None
    restaurant_id: Optional[str] = None
    
    demo_session_id: Optional[str] = None
    demo_start_time: Optional[float] = None
    demo_ended = False
    
    ai_audio_queue = asyncio.Queue()
    ai_stream_task = None
    shutdown_flag = False
    
    ai_currently_speaking = False
    last_speech_started_time = 0
    
    # 🔥 Event to wait for Twilio connection
    twilio_connected = asyncio.Event()

    # 🔥 DEMO TIMER
    async def check_demo_timer():
        """Check if 60 seconds elapsed since OpenAI started."""
        nonlocal demo_ended
        
        if not demo_start_time:
            return
        
        while not shutdown_flag and not demo_ended:
            elapsed = time.time() - demo_start_time
            
            if elapsed >= Config.DEMO_DURATION_SECONDS:
                demo_ended = True
                Log.info("⏱️ Demo time expired - ending OpenAI, starting feedback")
                
                # Close OpenAI connection
                try:
                    await connection_manager.close_openai_connection()
                except Exception as e:
                    Log.error(f"Failed to close OpenAI: {e}")
                
                # Redirect to feedback TwiML
                if current_call_sid and Config.has_twilio_credentials():
                    try:
                        from twilio.rest import Client
                        client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
                        
                        backend_url = os.getenv('BACKEND_URL', f"https://{websocket.url.hostname}")
                        feedback_twiml = TwilioService.create_feedback_twiml(backend_url)
                        
                        client.calls(current_call_sid).update(twiml=feedback_twiml)
                        Log.info("✅ Redirected to feedback flow")
                    except Exception as e:
                        Log.error(f"Failed to redirect to feedback: {e}")
                
                break
            
            await asyncio.sleep(1)

    async def ai_audio_streamer():
        nonlocal ai_currently_speaking
        Log.info("[AI Streamer] 🎵 Started")

        while not shutdown_flag:
            try:
                audio_data = await ai_audio_queue.get()

                if audio_data is None:
                    break

                # 🔥 CRITICAL: Drop AI audio if human has taken over
                if openai_service.is_human_in_control():
                    ai_audio_queue.task_done()
                    continue

                audio_b64 = audio_data.get("audio", "")
                try:
                    audio_bytes = base64.b64decode(audio_b64)
                    # 📞 mulaw at 8kHz: 8000 samples/sec * 1 byte/sample = 8000 bytes/sec
                    duration_seconds = len(audio_bytes) / 8000.0
                except Exception as e:
                    duration_seconds = 0.02

                ai_currently_speaking = True

                if current_call_sid:
                    broadcast_to_dashboards_nonblocking({
                        "messageType": "audio",
                        "speaker": "AI",
                        "audio": audio_b64,
                        "format": "mulaw",      # 📞 Mulaw from OpenAI (frontend upsamples)
                        "sampleRate": 8000,     # 📞 8kHz
                        "timestamp": audio_data.get("timestamp", int(time.time() * 1000)),
                        "callSid": current_call_sid,
                    }, current_call_sid)
                
                await asyncio.sleep(duration_seconds)
                ai_audio_queue.task_done()
                
            except Exception as e:
                Log.error(f"[AI Streamer] Error: {e}")
                await asyncio.sleep(0.01)
        
        Log.info("[AI Streamer] 🛑 Stopped")
    
    async def handle_ai_audio(audio_data: Dict[str, Any]):
        await ai_audio_queue.put(audio_data)

    async def handle_openai_transcript(transcription_data: Dict[str, Any]):
        if not transcription_data or not isinstance(transcription_data, dict):
            return
        
        speaker = transcription_data.get("speaker")
        text = transcription_data.get("text")
        if not speaker or not text:
            return
        
        if speaker == "AI" and openai_service.is_human_in_control():
            return
        
        payload = {
            "messageType": "transcription",
            "speaker": speaker,
            "text": text,
            "timestamp": transcription_data.get("timestamp") or int(time.time() * 1000),
            "callSid": current_call_sid,
        }
        broadcast_to_dashboards_nonblocking(payload, current_call_sid)

        try:
            order_extractor.add_transcript(speaker, text)
        except Exception as e:
            Log.error(f"[OrderExtraction] Error: {e}")

    openai_service.caller_transcript_callback = handle_openai_transcript
    openai_service.ai_transcript_callback = handle_openai_transcript

    try:
        Log.info("🔗 Step 1: Connecting to OpenAI...")
        try:
            await connection_manager.connect_to_openai()
            Log.info("✅ Step 1 COMPLETE: OpenAI WebSocket connected")
        except Exception as e:
            Log.error(f"❌ Step 1 FAILED: OpenAI connection error: {e}")
            import traceback
            Log.error(f"📍 Traceback:\n{traceback.format_exc()}")
            await connection_manager.close_openai_connection()
            return

        Log.info("🎬 Step 2: Initializing OpenAI session...")
        try:
            await openai_service.initialize_session(connection_manager)
            Log.info("✅ Step 2 COMPLETE: OpenAI session initialized & greeting sent")
        except Exception as e:
            Log.error(f"❌ Step 2 FAILED: OpenAI session init error: {e}")
            import traceback
            Log.error(f"📍 Traceback:\n{traceback.format_exc()}")
            await connection_manager.close_openai_connection()
            return

        Log.info("🎯 Step 3: Waiting for Twilio stream to start...")

        async def handle_media_event(data: dict):
            import time

            # 🔥 TIMESTAMP: Track when we FIRST receive audio from Twilio
            if data.get("event") == "media":
                current_time = time.time()

                if not hasattr(connection_manager.state, 'first_media_received_time'):
                    connection_manager.state.first_media_received_time = current_time
                    connection_manager.state.packet_count = 0
                    connection_manager.state.packet_times = []
                    Log.info(f"📥 [LATENCY] First audio RECEIVED from Twilio (caller started speaking)")

                # Track packet arrival patterns for network jitter analysis
                connection_manager.state.packet_count += 1
                if hasattr(connection_manager.state, 'last_packet_time'):
                    packet_interval = (current_time - connection_manager.state.last_packet_time) * 1000
                    connection_manager.state.packet_times.append(packet_interval)

                    # Log jitter stats every 50 packets
                    if connection_manager.state.packet_count % 50 == 0:
                        if len(connection_manager.state.packet_times) > 0:
                            import statistics
                            avg_interval = statistics.mean(connection_manager.state.packet_times[-50:])
                            jitter = statistics.stdev(connection_manager.state.packet_times[-50:]) if len(connection_manager.state.packet_times[-50:]) > 1 else 0
                            min_interval = min(connection_manager.state.packet_times[-50:])
                            max_interval = max(connection_manager.state.packet_times[-50:])

                            Log.info("=" * 70)
                            Log.info(f"📡 NETWORK JITTER ANALYSIS (Last 50 packets):")
                            Log.info(f"  📊 Packet count: {connection_manager.state.packet_count}")
                            Log.info(f"  ⏱️  Average interval: {avg_interval:.2f}ms")
                            Log.info(f"  📈 Jitter (std dev): {jitter:.2f}ms")
                            Log.info(f"  ⬇️  Min interval: {min_interval:.2f}ms")
                            Log.info(f"  ⬆️  Max interval: {max_interval:.2f}ms")

                            # Determine network quality
                            if jitter < 10:
                                quality = "EXCELLENT (very stable)"
                            elif jitter < 30:
                                quality = "GOOD (stable)"
                            elif jitter < 50:
                                quality = "FAIR (some variation)"
                            else:
                                quality = "POOR (high jitter)"
                            Log.info(f"  🎯 Network quality: {quality}")
                            Log.info("=" * 70)

                connection_manager.state.last_packet_time = current_time

            if data.get("event") == "media":
                media = data.get("media") or {}
                payload_b64 = media.get("payload")
                if payload_b64:
                    should_send_to_dashboard = True
                    
                    if openai_service.is_human_in_control():
                        if current_call_sid and current_call_sid in active_calls:
                            human_ws = active_calls[current_call_sid].get("human_audio_ws")
                            if human_ws:
                                try:
                                    await human_ws.send_text(json.dumps({
                                        "type": "caller_audio",
                                        "audio": payload_b64,
                                        "timestamp": int(time.time() * 1000)
                                    }))
                                except Exception as e:
                                    Log.error(f"[media] Failed to send to human: {e}")
                        
                        if should_send_to_dashboard:
                            broadcast_to_dashboards_nonblocking({
                                "messageType": "audio",
                                "speaker": "Caller",
                                "audio": payload_b64,
                                "format": "mulaw",      # 📞 Phone quality mulaw from Twilio
                                "sampleRate": 8000,     # 📞 8kHz (phone line limit)
                                "timestamp": int(time.time() * 1000),
                                "callSid": current_call_sid
                            }, current_call_sid)
                    else:
                        if connection_manager.is_openai_connected():
                            try:
                                import time
                                # 🕐 TIMESTAMP: Received from Twilio
                                t_received_from_twilio = time.time()

                                # 🕐 TIMESTAMP: Before converting to OpenAI format
                                t_before_convert = time.time()

                                audio_message = audio_service.process_incoming_audio(data)

                                # 🕐 TIMESTAMP: After converting, before sending to OpenAI
                                t_after_convert = time.time()

                                if audio_message:
                                    await connection_manager.send_to_openai(audio_message)

                                    # 🕐 TIMESTAMP: After sending to OpenAI
                                    t_after_send = time.time()

                                    # Track first audio sent to OpenAI
                                    if not hasattr(connection_manager.state, 'first_audio_to_openai_time'):
                                        connection_manager.state.first_audio_to_openai_time = t_after_send
                                        convert_time = (t_after_convert - t_before_convert) * 1000
                                        send_time = (t_after_send - t_after_convert) * 1000
                                        total_processing = (t_after_send - t_received_from_twilio) * 1000

                                        # 📊 DETAILED REQUEST PATH BREAKDOWN
                                        Log.info("=" * 70)
                                        Log.info("📊 COMPLETE SERVER PROCESSING BREAKDOWN (Request Path):")
                                        Log.info(f"  🎯 TOTAL: Receive from Twilio → Send to OpenAI: {total_processing:.2f}ms")
                                        Log.info("")
                                        Log.info(f"  Step 1️⃣  Convert Twilio → OpenAI format: {convert_time:.2f}ms")
                                        Log.info(f"           (mulaw → PCM conversion)")
                                        Log.info(f"  Step 2️⃣  Send to OpenAI: {send_time:.2f}ms")
                                        Log.info(f"           (WebSocket transmission)")
                                        Log.info("=" * 70)

                            except Exception as e:
                                Log.error(f"[media] failed to send to OpenAI: {e}")
                        
                        if should_send_to_dashboard:
                            broadcast_to_dashboards_nonblocking({
                                "messageType": "audio",
                                "speaker": "Caller",
                                "audio": payload_b64,
                                "format": "mulaw",      # 📞 Phone quality mulaw from Twilio
                                "sampleRate": 8000,     # 📞 8kHz (phone line limit)
                                "timestamp": int(time.time() * 1000),
                                "callSid": current_call_sid
                            }, current_call_sid)

        async def handle_audio_delta(response: dict):
            import time  # Import at function level
            try:
                if openai_service.is_human_in_control():
                    return

                # 🕐 TIMESTAMP: Received response from OpenAI
                t_received_from_openai = time.time()

                audio_data = openai_service.extract_audio_response_data(response) or {}
                delta = audio_data.get("delta")

                if delta:
                    should_send_to_dashboard = True

                    if getattr(connection_manager.state, "stream_sid", None):
                        try:
                            # 🕐 TIMESTAMP: Before converting OpenAI response to Twilio format
                            t_before_convert_response = time.time()

                            audio_message = audio_service.process_outgoing_audio(
                                response, connection_manager.state.stream_sid
                            )

                            # 🕐 TIMESTAMP: After converting, before sending to Twilio
                            t_after_convert_response = time.time()

                            if audio_message:
                                await connection_manager.send_to_twilio(audio_message)

                                # 🕐 TIMESTAMP: After sending to Twilio
                                t_after_send_to_twilio = time.time()

                                # 🔥 Track when FIRST audio is sent to Twilio
                                if hasattr(connection_manager.state, 'speech_stopped_time') and not hasattr(connection_manager.state, 'first_audio_sent'):
                                    total_to_twilio = (time.time() - connection_manager.state.speech_stopped_time) * 1000

                                    # Calculate detailed timestamps for the response path
                                    receive_time = (t_received_from_openai - connection_manager.state.speech_stopped_time) * 1000
                                    convert_response_time = (t_after_convert_response - t_before_convert_response) * 1000
                                    send_to_twilio_time = (t_after_send_to_twilio - t_after_convert_response) * 1000
                                    # Calculate audio chunk size
                                    import base64
                                    try:
                                        audio_bytes = base64.b64decode(audio_message['media']['payload'])
                                        chunk_size = len(audio_bytes)
                                        # 8kHz mulaw: 8000 bytes/sec, so bytes/8 = milliseconds of audio
                                        audio_duration_ms = (chunk_size / 8000) * 1000

                                        # 📊 DETAILED SERVER PROCESSING BREAKDOWN
                                        Log.info("=" * 70)
                                        Log.info("📊 COMPLETE SERVER PROCESSING BREAKDOWN (Response Path):")
                                        Log.info(f"  🎯 TOTAL: Speech stopped → First audio sent to Twilio: {total_to_twilio:.2f}ms")
                                        Log.info("")
                                        Log.info(f"  Step 1️⃣  Received FIRST response from OpenAI: {receive_time:.2f}ms")
                                        Log.info(f"           (VAD commit → OpenAI processes → Server receives)")
                                        Log.info(f"  Step 2️⃣  Convert OpenAI → Twilio format: {convert_response_time:.2f}ms")
                                        Log.info(f"           (PCM/G.711 conversion)")
                                        Log.info(f"  Step 3️⃣  Send to Twilio: {send_to_twilio_time:.2f}ms")
                                        Log.info(f"           (WebSocket transmission)")
                                        Log.info("")
                                        Log.info(f"  📦 Audio chunk: {chunk_size} bytes ({audio_duration_ms:.1f}ms of audio)")
                                        Log.info("=" * 70)
                                    except Exception as e:
                                        Log.info(f"📞 [LATENCY] First audio SENT TO TWILIO in {total_to_twilio:.0f}ms from speech stopped")
                                        Log.info(f"  ⏱️  Received from OpenAI: {receive_time:.2f}ms")
                                        Log.info(f"  ⏱️  Convert to Twilio: {convert_response_time:.2f}ms")
                                        Log.info(f"  ⏱️  Send to Twilio: {send_to_twilio_time:.2f}ms")
                                    connection_manager.state.first_audio_sent = True
                                    connection_manager.state.first_audio_sent_ms = total_to_twilio  # Save for dashboard metrics
                                    connection_manager.state.audio_chunk_count = 1
                                    connection_manager.state.outbound_chunk_times = []
                                    connection_manager.state.last_outbound_chunk_time = time.time()
                                elif hasattr(connection_manager.state, 'audio_chunk_count'):
                                    connection_manager.state.audio_chunk_count += 1

                                    # Track outbound streaming pattern
                                    current_chunk_time = time.time()
                                    if hasattr(connection_manager.state, 'last_outbound_chunk_time'):
                                        chunk_interval = (current_chunk_time - connection_manager.state.last_outbound_chunk_time) * 1000
                                        if not hasattr(connection_manager.state, 'outbound_chunk_times'):
                                            connection_manager.state.outbound_chunk_times = []
                                        connection_manager.state.outbound_chunk_times.append(chunk_interval)
                                    connection_manager.state.last_outbound_chunk_time = current_chunk_time

                                    # Log streaming stats every 20 chunks
                                    if connection_manager.state.audio_chunk_count % 20 == 0:
                                        elapsed = (time.time() - connection_manager.state.speech_stopped_time) * 1000

                                        if len(connection_manager.state.outbound_chunk_times) > 0:
                                            import statistics
                                            avg_interval = statistics.mean(connection_manager.state.outbound_chunk_times[-20:])
                                            jitter = statistics.stdev(connection_manager.state.outbound_chunk_times[-20:]) if len(connection_manager.state.outbound_chunk_times[-20:]) > 1 else 0

                                            Log.info("=" * 70)
                                            Log.info(f"📤 OUTBOUND STREAMING ANALYSIS (Chunks {connection_manager.state.audio_chunk_count-19}-{connection_manager.state.audio_chunk_count}):")
                                            Log.info(f"  ⏱️  Total elapsed: {elapsed:.0f}ms")
                                            Log.info(f"  📊 Average chunk interval: {avg_interval:.2f}ms")
                                            Log.info(f"  📈 Streaming jitter: {jitter:.2f}ms")

                                            # Calculate expected vs actual streaming rate
                                            # At 8kHz mulaw, typical chunk is 20ms of audio
                                            if avg_interval < 25:
                                                streaming_health = "EXCELLENT (smooth, real-time)"
                                            elif avg_interval < 40:
                                                streaming_health = "GOOD (minor buffering)"
                                            else:
                                                streaming_health = "SLOW (may cause delays)"
                                            Log.info(f"  🎯 Streaming health: {streaming_health}")
                                            Log.info("=" * 70)
                                        else:
                                            Log.info(f"📊 [LATENCY] Sent {connection_manager.state.audio_chunk_count} chunks in {elapsed:.0f}ms")

                                mark_msg = audio_service.create_mark_message(
                                    connection_manager.state.stream_sid
                                )
                                await connection_manager.send_to_twilio(mark_msg)

                                # 🔥 Track when we SEND the mark to Twilio (to measure round-trip)
                                if hasattr(connection_manager.state, 'speech_stopped_time') and not hasattr(connection_manager.state, 'first_mark_sent_time'):
                                    connection_manager.state.first_mark_sent_time = time.time()
                                    Log.info(f"📤 [LATENCY] First mark SENT to Twilio")
                        except Exception as e:
                            Log.error(f"[audio->twilio] failed: {e}")

                    if should_send_to_dashboard:
                        await handle_ai_audio({
                            "audio": delta,
                            "timestamp": int(time.time() * 1000)
                        })

            except Exception as e:
                Log.error(f"[audio-delta] failed: {e}")

        async def handle_speech_started():
            nonlocal ai_currently_speaking, last_speech_started_time
            
            try:
                if not openai_service.is_human_in_control():
                    Log.info("🛑 [Interruption] USER SPEAKING - cancelling AI")
                    
                    last_speech_started_time = time.time()
                    
                    try:
                        stream_sid = getattr(connection_manager.state, 'stream_sid', None)
                        if stream_sid:
                            clear_message = {
                                "event": "clear",
                                "streamSid": stream_sid
                            }
                            await connection_manager.send_to_twilio(clear_message)
                    except Exception:
                        pass
                    
                    try:
                        await connection_manager.send_to_openai({
                            "type": "response.cancel"
                        })
                    except Exception:
                        pass
                    
                    cleared_count = 0
                    while not ai_audio_queue.empty():
                        try:
                            ai_audio_queue.get_nowait()
                            ai_audio_queue.task_done()
                            cleared_count += 1
                        except:
                            break
                    
                    ai_currently_speaking = False
                    
                    await connection_manager.send_mark_to_twilio()
                    
            except Exception as e:
                Log.error(f"[Interruption] Error: {e}")

        async def handle_other_openai_event(response: dict):
            import time  # Import at function level to avoid scoping issues
            event_type = response.get('type', '')

            # Filter out spammy/repetitive events from logs
            spammy_events = {
                'response.output_audio_transcript.delta',
                'response.output_audio_transcript.done',
                'conversation.item.added',
                'conversation.item.done',
                'response.audio.delta',
                'response.audio_transcript.delta',
                'response.audio_transcript.done',
                'conversation.item.input_audio_transcription.completed',
                'response.done'
            }

            # Only log important events (not spammy ones)
            if event_type not in spammy_events:
                Log.info(f"[OpenAI Event] {event_type}")

            # 🔥 END-TO-END LATENCY TRACKING
            if event_type == 'input_audio_buffer.speech_stopped':
                nonlocal connection_manager
                connection_manager.state.speech_stopped_time = time.time()

                # Calculate time from first audio received to speech stopped
                if hasattr(connection_manager.state, 'first_media_received_time'):
                    time_since_first_audio = (time.time() - connection_manager.state.first_media_received_time) * 1000
                    Log.info(f"🔇 [LATENCY] Speech stopped detected {time_since_first_audio:.0f}ms after first audio received")
                else:
                    Log.info("🔇 [LATENCY] User stopped speaking - VAD detecting silence...")
            elif event_type == 'input_audio_buffer.committed':
                connection_manager.state.vad_commit_time = time.time()
                if hasattr(connection_manager.state, 'speech_stopped_time'):
                    delay = (time.time() - connection_manager.state.speech_stopped_time) * 1000
                    Log.info(f"⏱️ [LATENCY] VAD committed in {delay:.0f}ms after speech stopped")
                else:
                    Log.info("⏱️ [LATENCY] VAD committed buffer - waiting for response...")

            elif event_type == 'response.created':
                connection_manager.state.response_created_time = time.time()
                if hasattr(connection_manager.state, 'vad_commit_time'):
                    delay = (time.time() - connection_manager.state.vad_commit_time) * 1000

                    # This includes: network to OpenAI + OpenAI processing + network back
                    Log.info("=" * 70)
                    Log.info("🤖 OPENAI PROCESSING BREAKDOWN:")
                    Log.info(f"  ⏱️  VAD commit → response.created: {delay:.2f}ms")
                    Log.info(f"      (Network to OpenAI + AI processing + Network back)")

                    # Estimate network vs processing (rough approximation)
                    # Assuming ~50-100ms network round-trip, rest is processing
                    estimated_network = 75  # ms (rough estimate for round-trip)
                    estimated_processing = max(0, delay - estimated_network)
                    Log.info(f"  📡 Estimated network latency: ~{estimated_network}ms (round-trip)")
                    Log.info(f"  🧠 Estimated OpenAI processing: ~{estimated_processing:.0f}ms")
                    Log.info("=" * 70)

            elif event_type == 'response.audio.delta':
                # Track FIRST audio delta separately
                if not hasattr(connection_manager.state, 'first_audio_delta_time'):
                    connection_manager.state.first_audio_delta_time = time.time()

                    if hasattr(connection_manager.state, 'response_created_time'):
                        streaming_delay = (time.time() - connection_manager.state.response_created_time) * 1000
                        Log.info(f"🎵 [LATENCY] First audio delta: {streaming_delay:.2f}ms after response.created")
                        Log.info(f"           (OpenAI streaming delay)")

                    if hasattr(connection_manager.state, 'vad_commit_time'):
                        vad_delay = (time.time() - connection_manager.state.vad_commit_time) * 1000
                        Log.info(f"🔥 [LATENCY] Total VAD → First audio: {vad_delay:.2f}ms")

                        # Calculate total end-to-end latency
                        if hasattr(connection_manager.state, 'speech_stopped_time'):
                            total_delay = (time.time() - connection_manager.state.speech_stopped_time) * 1000
                            Log.info(f"✅ [LATENCY] END-TO-END: {total_delay:.0f}ms from speech stopped to first audio")

            if event_type == 'session.created':
                Log.info("✅ [OpenAI] Session created successfully")
            elif event_type == 'session.updated':
                Log.info("✅ [OpenAI] Session updated successfully")
            elif event_type == 'response.audio_transcript.delta':
                Log.debug(f"[OpenAI] 📝 AI transcript delta received")
            elif event_type == 'response.audio_transcript.done':
                Log.debug(f"[OpenAI] ✅ AI transcript complete")
            elif event_type == 'conversation.item.input_audio_transcription.completed':
                Log.debug(f"[OpenAI] 📝 Caller transcript received")
            elif event_type == 'response.done':
                Log.debug(f"[OpenAI] ✅ Response complete")
            elif event_type == 'error':
                Log.error(f"[OpenAI] ❌ Error event: {response}")

            openai_service.process_event_for_logging(response)
            await openai_service.extract_caller_transcript(response)

            if not openai_service.is_human_in_control():
                await openai_service.extract_ai_transcript(response)

        async def openai_receiver():
            Log.info("[OpenAI Receiver] 🎧 Started listening for OpenAI events...")
            await connection_manager.receive_from_openai(
                handle_audio_delta,
                handle_speech_started,
                handle_other_openai_event,
            )

        async def renew_openai_session():
            while True:
                await asyncio.sleep(getattr(Config, "REALTIME_SESSION_RENEW_SECONDS", 1200))
                try:
                    Log.info("Renewing OpenAI session…")
                    await connection_manager.close_openai_connection()
                    await connection_manager.connect_to_openai()
                    await openai_service.initialize_session(connection_manager)
                    Log.info("Session renewed successfully.")
                except Exception as e:
                    Log.error(f"Session renewal failed: {e}")

        async def on_start_cb(stream_sid: str):
            nonlocal current_call_sid, ai_stream_task, demo_session_id, demo_start_time, restaurant_id
            
            Log.header("=" * 80)
            Log.header("📞 TWILIO STREAM STARTED")
            Log.header("=" * 80)
            
            current_call_sid = getattr(connection_manager.state, 'call_sid', stream_sid)
            Log.event("Twilio Start", {"streamSid": stream_sid, "callSid": current_call_sid})

            # 🎙️ Start call recording via Twilio REST API (once per call)
            if Config.has_twilio_credentials():
                try:
                    from twilio.rest import Client
                    client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)

                    # Start recording the call
                    recording = client.calls(current_call_sid).recordings.create(
                        recording_status_callback=f'https://{websocket.url.hostname}/recording-status',
                        recording_status_callback_method='POST',
                        recording_status_callback_event=['completed']
                    )

                    Log.info(f"🎙️ Started call recording via API: {recording.sid}")
                except Exception as e:
                    Log.error(f"❌ Failed to start call recording: {e}")

            # Find demo session AND restaurant_id
            for sid, data in demo_sessions.items():
                if data.get('call_sid') == current_call_sid:
                    demo_session_id = sid
                    demo_start_time = time.time()
                    restaurant_id = data.get('restaurant_id', 'default')
                    Log.info(f"🎯 Found demo session: {demo_session_id}")
                    Log.info(f"🏪 Restaurant ID: {restaurant_id}")
                    Log.info(f"⏱️ Demo timer started - expires in {Config.DEMO_DURATION_SECONDS}s")
                    
                    broadcast_to_dashboards_nonblocking({
                        "messageType": "callStarted",
                        "callSid": current_call_sid,
                        "sessionId": demo_session_id,
                        "phoneNumber": data.get('phone'),  # Caller's phone number
                        "restaurantId": restaurant_id,
                        "timestamp": int(time.time() * 1000)
                    }, current_call_sid)
                    
                    break
            
            if not demo_session_id:
                Log.warning("⚠️ No demo session found for this call")
                Log.info(f"📋 Available demo sessions: {list(demo_sessions.keys())}")
                Log.info(f"📋 Call SID searching for: {current_call_sid}")
            
            if demo_session_id and demo_start_time:
                asyncio.create_task(check_demo_timer())
            
            caller_silence_detector.reset()
            ai_silence_detector.reset()
            
            if ai_stream_task is None or ai_stream_task.done():
                ai_stream_task = asyncio.create_task(ai_audio_streamer())
                Log.info("[AI Streamer] Task started")
            
            active_calls[current_call_sid] = {
                "restaurant_id": restaurant_id,
                "openai_service": openai_service,
                "connection_manager": connection_manager,
                "audio_service": audio_service,
                "transcription_service": transcription_service,
                "order_extractor": order_extractor,
                "human_audio_ws": None
            }
            Log.info(f"[ActiveCalls] ✅ Registered call {current_call_sid} for restaurant {restaurant_id}")
            Log.info("🎧 Waiting for caller audio...")

            async def send_order_update(order_data: Dict[str, Any]):
                payload = {
                    "messageType": "orderUpdate",
                    "orderData": order_data,
                    "timestamp": int(time.time() * 1000),
                    "callSid": current_call_sid,
                }
                broadcast_to_dashboards_nonblocking(payload, current_call_sid)
            
            order_extractor.set_update_callback(send_order_update)
            
            # 🔥 CRITICAL: Signal that Twilio is connected
            twilio_connected.set()

        async def on_mark_cb():
            import time
            try:
                # 🔥 Track when Twilio RETURNS the mark (means audio was played!)
                if hasattr(connection_manager.state, 'first_mark_sent_time') and not hasattr(connection_manager.state, 'first_mark_received'):
                    mark_roundtrip = (time.time() - connection_manager.state.first_mark_sent_time) * 1000
                    total_from_speech = (time.time() - connection_manager.state.speech_stopped_time) * 1000

                    # Calculate component times
                    ai_processing_ms = getattr(connection_manager.state, 'first_audio_sent_ms', 0)
                    network_delay_ms = total_from_speech - ai_processing_ms

                    Log.info(f"📥 [LATENCY] First mark RECEIVED from Twilio after {mark_roundtrip:.0f}ms")
                    Log.info(f"🎯 [LATENCY] TOTAL from speech stopped to Twilio playback: {total_from_speech:.0f}ms")

                    # 📊 COMPREHENSIVE LATENCY BREAKDOWN
                    if hasattr(connection_manager.state, 'first_media_received_time'):
                        total_from_first_audio = (time.time() - connection_manager.state.first_media_received_time) * 1000
                        caller_to_vad = total_from_first_audio - total_from_speech

                        Log.info("=" * 80)
                        Log.info("📊 COMPLETE END-TO-END LATENCY BREAKDOWN:")
                        Log.info("")
                        Log.info("🔵 MEASURED COMPONENTS (from logs):")
                        Log.info(f"  1️⃣  Caller → Server (inbound network): ~{caller_to_vad:.0f}ms")
                        Log.info(f"      • Caller phone → Twilio ingress → Server WebSocket")
                        Log.info(f"      • Includes: Phone network + Twilio routing + Internet")
                        Log.info("")
                        Log.info(f"  2️⃣  Server processing: {ai_processing_ms:.0f}ms")
                        Log.info(f"      • Breakdown available in 'Server Processing Breakdown' logs above")
                        Log.info(f"      • Includes: Format conversion + OpenAI network + AI processing")
                        Log.info("")
                        Log.info(f"  3️⃣  Twilio jitter buffer & egress: {mark_roundtrip:.0f}ms")
                        Log.info(f"      • Server → Twilio WebSocket → Jitter buffer → Playback starts")

                        # Break down the mark roundtrip
                        estimated_websocket_latency = min(50, mark_roundtrip * 0.2)  # ~20% or max 50ms
                        estimated_jitter_buffer = mark_roundtrip - estimated_websocket_latency

                        Log.info(f"      • Estimated WebSocket latency: ~{estimated_websocket_latency:.0f}ms")
                        Log.info(f"      • Estimated Twilio jitter buffer: ~{estimated_jitter_buffer:.0f}ms")
                        Log.info(f"        (Jitter buffer smooths audio, required for quality)")
                        Log.info("")
                        Log.info(f"  🎯 TOTAL MEASURED: {total_from_first_audio:.0f}ms")
                        Log.info(f"      (From first audio received to Twilio playback confirmation)")
                        Log.info("")
                        Log.info("🟡 ESTIMATED COMPONENTS (cannot measure directly):")

                        # Estimate based on region
                        if caller_to_vad < 300:
                            inbound_estimate = "100-200ms"
                            outbound_estimate = "300-500ms"
                            total_estimate = "400-700ms"
                            region_name = "LOCAL/SAME DATACENTER"
                        elif caller_to_vad < 600:
                            inbound_estimate = "200-400ms"
                            outbound_estimate = "500-800ms"
                            total_estimate = "700-1200ms"
                            region_name = "DOMESTIC (US)"
                        else:
                            inbound_estimate = "500-800ms"
                            outbound_estimate = "1000-1800ms"
                            total_estimate = "1500-2600ms"
                            region_name = "INTERNATIONAL"

                        Log.info(f"  4️⃣  Caller audio → Server (before we receive): ~{inbound_estimate}")
                        Log.info(f"      • Time from caller starts speaking to server receives")
                        Log.info(f"      • Cannot measure (we don't know when caller started)")
                        Log.info("")
                        Log.info(f"  5️⃣  Twilio → Caller's ear (return path): ~{outbound_estimate}")
                        Log.info(f"      • Twilio egress → Phone network → Caller hears it")
                        Log.info(f"      • Typically 1.2-1.5x inbound latency (asymmetric routing)")
                        Log.info("")
                        Log.info(f"  🌍 DETECTED REGION: {region_name}")
                        Log.info(f"  📊 ESTIMATED TOTAL END-TO-END: ~{total_estimate}")
                        Log.info(f"      (What the caller actually experiences)")
                        Log.info("=" * 80)

                    connection_manager.state.first_mark_received = True

                    # 📊 Broadcast latency metrics to dashboard
                    try:
                        # Determine performance rating based on server processing
                        if ai_processing_ms < 500:
                            performance_rating = "excellent"
                        elif ai_processing_ms < 800:
                            performance_rating = "good"
                        else:
                            performance_rating = "fair"

                        # Calculate additional metrics if we have first_media_received_time
                        caller_to_server_ms = 0
                        total_measured_ms = round(total_from_speech)
                        estimated_return_path_ms = 0

                        if hasattr(connection_manager.state, 'first_media_received_time'):
                            total_from_first_audio = (time.time() - connection_manager.state.first_media_received_time) * 1000
                            caller_to_server_ms = round(total_from_first_audio - total_from_speech)
                            total_measured_ms = round(total_from_first_audio)

                            # Estimate return path based on caller_to_server (rough approximation)
                            # International calls typically have symmetric latency
                            estimated_return_path_ms = round(caller_to_server_ms * 1.2)  # Add 20% for asymmetry

                        # Determine region based on caller_to_server latency
                        if caller_to_server_ms < 300:
                            region = "local"
                        elif caller_to_server_ms < 600:
                            region = "domestic"
                        else:
                            region = "international"

                        latency_metrics = {
                            "messageType": "latencyMetrics",
                            "callSid": current_call_sid,
                            "timestamp": int(time.time() * 1000),
                            "metrics": {
                                # What we control
                                "serverProcessingMs": round(ai_processing_ms),  # Server + OpenAI + network between them

                                # What Twilio controls
                                "twilioBufferMs": round(mark_roundtrip),  # Jitter buffer

                                # What nobody controls (telecom infrastructure)
                                "inboundNetworkMs": caller_to_server_ms,  # Caller → Server (measured)
                                "estimatedOutboundNetworkMs": estimated_return_path_ms,  # Server → Caller (estimated)

                                # Totals
                                "totalMeasuredMs": total_measured_ms,  # Everything we can measure
                                "estimatedTotalMs": total_measured_ms + estimated_return_path_ms,  # Full end-to-end estimate

                                # Legacy fields (for backward compatibility)
                                "aiProcessingMs": round(ai_processing_ms),  # Deprecated: Use serverProcessingMs
                                "networkDelayMs": round(network_delay_ms),  # Deprecated: Use twilioBufferMs

                                # Metadata
                                "performanceRating": performance_rating,
                                "region": region
                            }
                        }

                        broadcast_to_dashboards_nonblocking(latency_metrics, current_call_sid)
                        Log.info(f"📊 [Dashboard] Sent latency metrics: AI={ai_processing_ms:.0f}ms, Network={network_delay_ms:.0f}ms, Total={total_from_speech:.0f}ms")
                    except Exception as e:
                        Log.error(f"[Dashboard] Failed to broadcast latency metrics: {e}")

                audio_service.handle_mark_event()
            except Exception as e:
                Log.error(f"[Mark] Error: {e}")

        # 🔥 CRITICAL FIX: Start Twilio receiver FIRST
        Log.info("🚀 Starting Twilio receiver...")
        twilio_task = asyncio.create_task(
            connection_manager.receive_from_twilio(handle_media_event, on_start_cb, on_mark_cb)
        )
        
        # Wait for Twilio to actually connect
        Log.info("⏳ Waiting for Twilio to connect...")
        await twilio_connected.wait()
        Log.info("✅ Twilio connected! Starting OpenAI receiver and session renewal...")
        
        # NOW start the other loops
        await asyncio.gather(
            twilio_task,
            openai_receiver(),
            renew_openai_session(),
        )

    except Exception as e:
        Log.error(f"❌ CRITICAL ERROR in media stream handler: {e}")
        import traceback
        Log.error(f"📍 Traceback:\n{traceback.format_exc()}")

    finally:
        Log.info("🧹 Cleaning up media stream...")
        shutdown_flag = True
        
        # 🔥 Send call summary email (call ended during demo, not early)
        if current_call_sid:
            try:
                # Find session info
                session_info = None
                for sid, data in demo_sessions.items():
                    if data.get('call_sid') == current_call_sid:
                        session_info = {'session_id': sid, **data}
                        break
                
                # Get call duration
                call_duration = None
                if demo_start_time:
                    call_duration = int(time.time() - demo_start_time)
                
                # Send summary email (NOT early - they made it to the demo)
                send_call_summary_email(
                    call_sid=current_call_sid,
                    session_id=session_info.get('session_id') if session_info else None,
                    phone=session_info.get('phone') if session_info else 'Unknown',
                    duration_seconds=call_duration,
                    rating=None,
                    ended_early=False  # 🔥 They reached the demo, so not "early"
                )
            except Exception as e:
                Log.error(f"Failed to send call summary email: {e}")
        
        try:
            await ai_audio_queue.put(None)
            if ai_stream_task and not ai_stream_task.done():
                await asyncio.wait([ai_stream_task], timeout=2.0)
        except Exception:
            pass
        
        try:
            final_summary = order_extractor.get_order_summary()
            Log.info(f"\n{final_summary}")
            final_order = order_extractor.get_current_order()
            if any(final_order.values()):
                broadcast_to_dashboards_nonblocking({
                    "messageType": "orderComplete",
                    "orderData": final_order,
                    "summary": final_summary,
                    "timestamp": int(time.time() * 1000),
                    "callSid": current_call_sid,
                }, current_call_sid)
        except Exception:
            pass

        if current_call_sid and current_call_sid in active_calls:
            del active_calls[current_call_sid]
            Log.info(f"[ActiveCalls] ❌ Removed call {current_call_sid}")

        try:
            await transcription_service.shutdown()
        except Exception:
            pass

        try:
            await order_extractor.shutdown()
        except Exception:
            pass

        try:
            await connection_manager.close_openai_connection()
        except Exception:
            pass
        
        Log.info("✅ Media stream cleanup complete")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", getattr(Config, "PORT", 8000))),
        log_level="info",
        reload=False,
    )
