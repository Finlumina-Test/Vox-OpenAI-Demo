import base64
from typing import Optional
from fastapi import Request
from fastapi.responses import HTMLResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Gather


class TwilioService:
    """Twilio integration for VOX demo system."""
    
    TWILIO_VOICE = "Google.en-US-Chirp3-HD-Aoede"
    
    @staticmethod
    def create_demo_intro_twiml(session_id: str, backend_url: str) -> str:
        """
        TwiML that speaks dashboard URL and waits for key press to start demo.
        🔥 User can press any key to skip the URL announcement
        """
        response = VoiceResponse()
        
        # 🔥 Gather wraps EVERYTHING so user can skip anytime
        gather = Gather(
            num_digits=1,
            timeout=60,
            action=f"{backend_url}/demo-start",
            method="POST",
            input="dtmf",  # 🔥 Only listen for keypad (faster interrupt)
            finish_on_key=""  # 🔥 ANY key finishes immediately
        )
        
        # Welcome - NO PAUSE (faster)
        gather.say(
            "Welcome to vox by Finlumina. Your live demo dashboard is ready.",
            voice=TwilioService.TWILIO_VOICE
        )
        gather.pause(length=0.3)  # 🔥 Reduced from 1s
        
        # Speak URL
        gather.say(
            "To watch this call in real time, visit: vox dot finlumina dot com slash demo slash",
            voice=TwilioService.TWILIO_VOICE
        )
        gather.pause(length=0.3)  # 🔥 Reduced from 0.5s
        
        # 🔥 Keep session ID slow (0.4s between chars)
        for char in session_id:
            if char.isdigit():
                gather.say(char, voice=TwilioService.TWILIO_VOICE)
            else:
                gather.say(char.upper(), voice=TwilioService.TWILIO_VOICE)
            gather.pause(length=0.4)  # Keep this as is
        
        gather.pause(length=0.5)  # 🔥 Reduced from 1s
        
        # Repeat
        gather.say(
            "Again, that's vox dot finlumina dot com slash demo slash",
            voice=TwilioService.TWILIO_VOICE
        )
        gather.pause(length=0.3)  # 🔥 Reduced from 0.5s
        
        for char in session_id:
            if char.isdigit():
                gather.say(char, voice=TwilioService.TWILIO_VOICE)
            else:
                gather.say(char.upper(), voice=TwilioService.TWILIO_VOICE)
            gather.pause(length=0.4)  # Keep this as is
        
        gather.pause(length=0.5)  # 🔥 Reduced from 1s
        
        # Instruction
        gather.say(
            "Press any key on your keypad when you are ready to start your one minute demo.",
            voice=TwilioService.TWILIO_VOICE
        )
        
        response.append(gather)
        
        # Timeout fallback (if no key pressed after 60s)
        response.say(
            "Starting demo now.",
            voice=TwilioService.TWILIO_VOICE
        )
        response.redirect(f"{backend_url}/demo-start?auto=true")
        
        return str(response)
    
    @staticmethod
    def create_demo_start_twiml(backend_host: str, skipped: bool = False) -> str:
        """
        TwiML to start OpenAI media stream after key press.
        🔥 Different message if user skipped the intro
        """
        response = VoiceResponse()
        
        if skipped:
            # 🔥 Ultra short message for instant connect
            response.say(
                "Connecting now.",
                voice=TwilioService.TWILIO_VOICE
            )
        else:
            response.say(
                "Great! Starting your demo now. You have one minute.",
                voice=TwilioService.TWILIO_VOICE
            )
        
        # Connect to media stream
        connect = Connect()
        connect.stream(url=f'wss://{backend_host}/media-stream')
        response.append(connect)
        
        return str(response)
    
    @staticmethod
    def create_feedback_twiml(backend_url: str) -> str:
        """TwiML for feedback collection after demo expires."""
        response = VoiceResponse()
        
        response.say(
            "Your demo session has expired. We hope you enjoyed it!",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=0.3)  # 🔥 Reduced from 1s
        
        response.say(
            "To get Vox for your business, contact sales at vox dot finlumina dot com.",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=0.5)  # 🔥 Reduced from 1s
        
        response.say(
            "Please rate your experience from 1 to 5, with 5 being excellent. Press a number on your phone keypad now.",
            voice=TwilioService.TWILIO_VOICE
        )
        
        gather = Gather(
            num_digits=1,
            timeout=10,
            action=f"{backend_url}/demo-rating",
            method="POST"
        )
        response.append(gather)
        
        response.say(
            "We didn't receive your rating. Thank you for trying vox. Goodbye!",
            voice=TwilioService.TWILIO_VOICE
        )
        
        return str(response)
    
    @staticmethod
    def create_rating_response_twiml(rating: int) -> str:
        """TwiML response after receiving rating."""
        response = VoiceResponse()
        
        response.say(
            f"Thank you for rating us {rating} out of 5!",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=0.3)  # 🔥 Reduced from 0.5s
        response.say(
            "We appreciate your feedback. Visit finlumina dot com to learn more. Goodbye!",
            voice=TwilioService.TWILIO_VOICE
        )
        
        # 🔥 NEW: Hang up immediately after rating
        response.hangup()
        return str(response)
    
    @staticmethod
    def create_invalid_rating_twiml(backend_url: str) -> str:
        """TwiML for invalid rating (not 1-5)."""
        response = VoiceResponse()
        
        response.say(
            "Sorry, please rate between 1 and 5 only.",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=0.3)  # 🔥 Reduced from 0.5s
        response.say(
            "Let's try again. Press a number from 1 to 5 on your keypad.",
            voice=TwilioService.TWILIO_VOICE
        )
        
        gather = Gather(
            num_digits=1,
            timeout=10,
            action=f"{backend_url}/demo-rating",
            method="POST"
        )
        response.append(gather)
        
        response.say(
            "Thank you for trying VOX. Goodbye!",
            voice=TwilioService.TWILIO_VOICE
        )
        response.hangup()
        
        return str(response)
    
    @staticmethod
    def create_media_message(stream_sid: str, audio_payload: str) -> dict:
        return {
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": audio_payload}
        }
    
    @staticmethod
    def create_mark_message(stream_sid: str, mark_name: str = "responsePart") -> dict:
        return {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": mark_name}
        }
    
    @staticmethod
    def create_clear_message(stream_sid: str) -> dict:
        return {
            "event": "clear",
            "streamSid": stream_sid
        }
    
    @staticmethod
    def convert_openai_audio_to_twilio(openai_audio_delta: str) -> str:
        return base64.b64encode(base64.b64decode(openai_audio_delta)).decode('utf-8')
    
    @staticmethod
    def extract_stream_id(start_event_data: dict) -> Optional[str]:
        try:
            return start_event_data['start']['streamSid']
        except (KeyError, TypeError):
            return None
    
    @staticmethod
    def extract_media_payload(media_event_data: dict) -> Optional[str]:
        try:
            return media_event_data['media']['payload']
        except (KeyError, TypeError):
            return None
    
    @staticmethod
    def extract_media_timestamp(media_event_data: dict) -> Optional[int]:
        try:
            return int(media_event_data['media']['timestamp'])
        except (KeyError, TypeError, ValueError):
            return None
    
    @staticmethod
    def is_media_event(event_data: dict) -> bool:
        return event_data.get('event') == 'media'
    
    @staticmethod
    def is_start_event(event_data: dict) -> bool:
        return event_data.get('event') == 'start'
    
    @staticmethod
    def is_mark_event(event_data: dict) -> bool:
        return event_data.get('event') == 'mark'


class TwilioAudioProcessor:
    """Audio data preparation for Twilio and OpenAI."""
    
    @staticmethod
    def prepare_audio_for_openai(twilio_payload: str) -> dict:
        return {
            "type": "input_audio_buffer.append",
            "audio": twilio_payload
        }
    
    @staticmethod
    def prepare_audio_for_twilio(openai_delta: str, stream_sid: str) -> dict:
        converted_payload = TwilioService.convert_openai_audio_to_twilio(openai_delta)
        return TwilioService.create_media_message(stream_sid, converted_payload)
