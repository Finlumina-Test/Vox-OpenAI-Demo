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

@app.api_route("/call-status", methods=["POST"])
async def handle_call_status(request: Request):
    """Handle Twilio call status callbacks (hangup tracking)."""
    try:
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
        if call_status in ['completed', 'failed', 'busy', 'no-answer']:
            Log.info(f"✅ Status matches - processing email...")
            
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
            
            try:
                await connection_manager.send_to_openai({
                    "type": "response.cancel"
                })
                Log.info(f"[Takeover] Cancelled AI response")
            except Exception:
                Log.debug(f"[Takeover] No active response to cancel (normal)")
            
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
                                audio_message = audio_service.process_incoming_audio(data)
                                if audio_message:
                                    await connection_manager.send_to_openai(audio_message)
                                    Log.debug(f"[media] 🎤 Sent caller audio to OpenAI")
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
            try:
                if openai_service.is_human_in_control():
                    return
                
                audio_data = openai_service.extract_audio_response_data(response) or {}
                delta = audio_data.get("delta")
                
                if delta:
                    Log.debug(f"[audio-delta] 🔊 Received AI audio delta")
                    should_send_to_dashboard = True
                    
                    if getattr(connection_manager.state, "stream_sid", None):
                        try:
                            audio_message = audio_service.process_outgoing_audio(
                                response, connection_manager.state.stream_sid
                            )
                            if audio_message:
                                await connection_manager.send_to_twilio(audio_message)
                                mark_msg = audio_service.create_mark_message(
                                    connection_manager.state.stream_sid
                                )
                                await connection_manager.send_to_twilio(mark_msg)
                                Log.debug(f"[audio-delta] 📞 Sent AI audio to Twilio")
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
            event_type = response.get('type', '')
            
            # Log every event from OpenAI
            Log.info(f"[OpenAI Event] {event_type}")
            
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
            try:
                audio_service.handle_mark_event()
            except Exception:
                pass

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
