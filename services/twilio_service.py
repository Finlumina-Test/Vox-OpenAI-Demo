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
            "To get VOX A.I. for your business, contact sales at finlumina dot com.",
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
            "We didn't receive your rating. Thank you for trying VOX A.I. Goodbye!",
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
        stars = "⭐" * rating
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
            "Thank you for trying VOX A.I. Goodbye!",
            voice=TwilioService.TWILIO_VOICE
        )
        response.hangup()
        
        return str(response)
