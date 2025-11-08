import base64
from typing import Optional
from fastapi import Request
from fastapi.responses import HTMLResponse, Response
from twilio.twiml.voice_response import VoiceResponse, Connect, Gather, Say


class TwilioService:
    """
    Provides all Twilio integration logic for the application.
    
    - Generates TwiML responses for incoming calls, connecting callers to the media stream.
    - Creates Twilio-compatible messages (media, mark, clear) for the WebSocket Media Streams API.
    - Converts audio data formats between OpenAI and Twilio.
    - Extracts and interprets Twilio event payloads.
    
    This class is the main entry point for all Twilio-related operations and is used by higher-level services to interact with Twilio Voice and Media Streams.
    """
    
    # Twilio voice configuration
    TWILIO_VOICE = "Google.en-US-Chirp3-HD-Aoede"
    
    @classmethod
    def create_incoming_call_response(cls, request: Request) -> HTMLResponse:
        """
        Create TwiML response for incoming calls to connect to Media Stream.
        
        Args:
            request: FastAPI request object to get hostname
            
        Returns:
            HTMLResponse containing TwiML XML
        """
        response = VoiceResponse()
        
        # Add greeting with punctuation for better text-to-speech flow
        response.say(
            "Testing Finlumina-Vox",
            voice=cls.TWILIO_VOICE
        )
        response.pause(length=1)
        response.say(   
            "O.K. you can start talking!",
            voice=cls.TWILIO_VOICE
        )
        
        # Set up media stream connection
        host = request.url.hostname
        connect = Connect()
        connect.stream(url=f'wss://{host}/media-stream')
        response.append(connect)
        
        return HTMLResponse(content=str(response), media_type="application/xml")
    
    @staticmethod
    def create_feedback_twiml(backend_url: str) -> str:
        """
        Create TwiML for feedback collection after demo expires.
        
        Args:
            backend_url: Base URL of backend for callback
            
        Returns:
            TwiML XML string
        """
        response = VoiceResponse()
        
        # Demo expiry message
        response.say(
            "Your demo session has expired. We hope you enjoyed it!",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=1)
        
        # Sales message
        response.say(
            "To get VOX for your business, contact sales at finlumina dot com.",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=1)
        
        # 🔥 CLEAR instructions for feedback
        response.say(
            "Please rate your experience from 1 to 5, with 5 being excellent.",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=0.5)
        response.say(
            "Press a number on your phone keypad now. Press 1, 2, 3, 4, or 5.",
            voice=TwilioService.TWILIO_VOICE
        )
        
        # Gather rating
        gather = Gather(
            num_digits=1,
            timeout=10,
            action=f"{backend_url}/demo-rating",
            method="POST"
        )
        response.append(gather)
        
        # Timeout fallback
        response.say(
            "We didn't receive your rating. Thank you for trying VOX. Goodbye!",
            voice=TwilioService.TWILIO_VOICE
        )
        
        return str(response)
    
    @staticmethod
    def create_rating_response_twiml(rating: int) -> str:
        """
        Create TwiML response after receiving rating.
        
        Args:
            rating: User's rating (1-5)
            
        Returns:
            TwiML XML string
        """
        response = VoiceResponse()
        
        # Thank user
        response.say(
            f"Thank you for rating us {rating} out of 5!",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=0.5)
        response.say(
            "We appreciate your feedback.",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=0.5)
        response.say(
            "Visit finlumina dot com to learn more. Goodbye!",
            voice=TwilioService.TWILIO_VOICE
        )
        
        # Hang up
        response.hangup()
        
        return str(response)
    
    @staticmethod
    def create_invalid_rating_twiml(backend_url: str) -> str:
        """
        Create TwiML for invalid rating (not 1-5).
        
        Args:
            backend_url: Base URL of backend for callback
            
        Returns:
            TwiML XML string
        """
        response = VoiceResponse()
        
        response.say(
            "Sorry, please rate between 1 and 5 only.",
            voice=TwilioService.TWILIO_VOICE
        )
        response.pause(length=0.5)
        response.say(
            "Let's try again. Press a number from 1 to 5 on your keypad.",
            voice=TwilioService.TWILIO_VOICE
        )
        
        # Try again
        gather = Gather(
            num_digits=1,
            timeout=10,
            action=f"{backend_url}/demo-rating",
            method="POST"
        )
        response.append(gather)
        
        # Final fallback
        response.say(
            "Thank you for trying VOX. Goodbye!",
            voice=TwilioService.TWILIO_VOICE
        )
        response.hangup()
        
        return str(response)
    
    @staticmethod
    def create_media_message(stream_sid: str, audio_payload: str) -> dict:
        """
        Create a Twilio media message with audio payload.
        
        Args:
            stream_sid: Twilio stream identifier
            audio_payload: Base64 encoded audio data
            
        Returns:
            Dictionary containing Twilio media message
        """
        return {
            "event": "media",
            "streamSid": stream_sid,
            "media": {
                "payload": audio_payload
            }
        }
    
    @staticmethod
    def create_mark_message(stream_sid: str, mark_name: str = "responsePart") -> dict:
        """
        Create a Twilio mark message for audio synchronization.
        
        Args:
            stream_sid: Twilio stream identifier
            mark_name: Name of the mark for identification
            
        Returns:
            Dictionary containing Twilio mark message
        """
        return {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": mark_name}
        }
    
    @staticmethod
    def create_clear_message(stream_sid: str) -> dict:
        """
        Create a Twilio clear message to clear audio buffer.
        
        Args:
            stream_sid: Twilio stream identifier
            
        Returns:
            Dictionary containing Twilio clear message
        """
        return {
            "event": "clear",
            "streamSid": stream_sid
        }
    
    @staticmethod
    def convert_openai_audio_to_twilio(openai_audio_delta: str) -> str:
        """
        Convert OpenAI audio delta format to Twilio-compatible format.
        
        OpenAI provides base64 encoded audio, which we need to re-encode
        for Twilio's expected format.
        
        Args:
            openai_audio_delta: Base64 encoded audio from OpenAI
            
        Returns:
            Base64 encoded audio payload for Twilio
        """
        # Decode and re-encode to ensure proper format for Twilio
        return base64.b64encode(base64.b64decode(openai_audio_delta)).decode('utf-8')
    
    @staticmethod
    def extract_stream_id(start_event_data: dict) -> Optional[str]:
        """
        Extract stream ID from Twilio start event data.
        
        Args:
            start_event_data: Twilio start event data
            
        Returns:
            Stream ID if found, None otherwise
        """
        try:
            return start_event_data['start']['streamSid']
        except (KeyError, TypeError):
            return None
    
    @staticmethod
    def extract_media_payload(media_event_data: dict) -> Optional[str]:
        """
        Extract audio payload from Twilio media event data.
        
        Args:
            media_event_data: Twilio media event data
            
        Returns:
            Audio payload if found, None otherwise
        """
        try:
            return media_event_data['media']['payload']
        except (KeyError, TypeError):
            return None
    
    @staticmethod
    def extract_media_timestamp(media_event_data: dict) -> Optional[int]:
        """
        Extract timestamp from Twilio media event data.
        
        Args:
            media_event_data: Twilio media event data
            
        Returns:
            Timestamp if found, None otherwise
        """
        try:
            return int(media_event_data['media']['timestamp'])
        except (KeyError, TypeError, ValueError):
            return None
    
    @staticmethod
    def is_media_event(event_data: dict) -> bool:
        """Check if event data represents a Twilio media event."""
        return event_data.get('event') == 'media'
    
    @staticmethod
    def is_start_event(event_data: dict) -> bool:
        """Check if event data represents a Twilio start event."""
        return event_data.get('event') == 'start'
    
    @staticmethod
    def is_mark_event(event_data: dict) -> bool:
        """Check if event data represents a Twilio mark event."""
        return event_data.get('event') == 'mark'


class TwilioAudioProcessor:
    """
    Handles audio data preparation and conversion for Twilio and OpenAI.
    
    - Prepares Twilio audio payloads for OpenAI's Realtime API.
    - Converts OpenAI audio deltas into Twilio-compatible media messages.
    
    This class is typically used internally by the TwilioService or audio pipeline to ensure audio data is in the correct format for each service.
    """
    
    @staticmethod
    def prepare_audio_for_openai(twilio_payload: str) -> dict:
        """
        Prepare Twilio audio payload for OpenAI Realtime API.
        
        Args:
            twilio_payload: Audio payload from Twilio
            
        Returns:
            Dictionary formatted for OpenAI input_audio_buffer.append
        """
        return {
            "type": "input_audio_buffer.append",
            "audio": twilio_payload
        }
    
    @staticmethod
    def prepare_audio_for_twilio(openai_delta: str, stream_sid: str) -> dict:
        """
        Prepare OpenAI audio delta for Twilio media stream.
        
        Args:
            openai_delta: Audio delta from OpenAI
            stream_sid: Twilio stream identifier
            
        Returns:
            Dictionary formatted for Twilio media message
        """
        converted_payload = TwilioService.convert_openai_audio_to_twilio(openai_delta)
        return TwilioService.create_media_message(stream_sid, converted_payload)
