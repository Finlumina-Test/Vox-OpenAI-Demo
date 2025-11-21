# Vox AI - OpenAI Realtime Demo

Vox is Finlumina's advanced multilingual voice assistant platform, built on OpenAI's Realtime API.

## Features

- 🎯 Ultra-low latency (<500ms average response)
- 🌍 Multilingual support (15+ languages)
- 🎙️ Automatic call recording with Twilio
- 💾 Supabase integration for call storage
- 📊 Real-time dashboard for call monitoring
- 🔄 Human takeover capability

## Setup Instructions

### 1. Prerequisites

- Python 3.8+
- Twilio account with phone number
- OpenAI API key with Realtime API access
- Supabase account (optional, for call recording storage)

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Configuration

Create a `.env` file based on `.env.example`:

```bash
# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key

# Twilio Configuration
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=your_twilio_phone_number

# Supabase Configuration (Optional - for call recording storage)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_key
SUPABASE_TABLE=calls

# Email Configuration (Optional - for feedback)
RESEND_API_KEY=your_resend_api_key
FEEDBACK_EMAIL=your@email.com

# Demo Configuration
DEMO_DURATION_SECONDS=60
```

### 4. Supabase Setup (Optional)

If you want to store call recordings in Supabase:

1. Create a new Supabase project at https://supabase.com
2. Run the migration script to create the `calls` table:

```bash
# In your Supabase SQL Editor, run:
cat supabase_migration.sql
```

3. Add your Supabase credentials to `.env`

The `calls` table schema includes:
- `call_sid` - Unique Twilio call identifier
- `restaurant_id` - Restaurant/business identifier
- `phone_number` - Caller's phone number
- `audio_url` - URL to the Twilio recording
- `call_duration` - Duration in seconds
- `transcript` - JSON array of conversation turns
- `order_items` - JSON array of ordered items (for restaurant use cases)
- Other metadata fields

### 5. Run the Server

```bash
python server.py
```

The server will start on `http://localhost:5050` (or the configured port).

### 6. Configure Twilio Webhooks

In your Twilio phone number settings:

1. **Voice Configuration**
   - When a call comes in: `https://your-domain.com/incoming-call`
   - Method: POST

2. **Status Callback URL** (for call tracking)
   - URL: `https://your-domain.com/call-status`
   - Method: POST

3. **Recording Status Callback** (for Supabase integration)
   - URL: `https://your-domain.com/recording-status`
   - Method: POST

## How It Works

### Call Flow

1. User calls your Twilio number
2. Twilio sends webhook to `/incoming-call`
3. User presses any key to start the demo
4. WebSocket connection established to OpenAI Realtime API
5. Audio streams bidirectionally (Twilio ↔ OpenAI)
6. Call is automatically recorded by Twilio
7. When call ends, recording is sent to `/recording-status`
8. Recording URL and metadata are stored in Supabase

### Supabase Integration

When a call completes and the recording is ready:

1. Twilio sends a POST request to `/recording-status`
2. The backend extracts:
   - `call_sid` - Unique call identifier
   - `recording_url` - Twilio recording URL
   - `recording_duration` - Call length
   - Session metadata (phone, restaurant_id)
3. Data is inserted into the `calls` table in Supabase
4. You can later update the record with:
   - `transcript` - Conversation turns from OpenAI
   - `order_items` - Extracted order details
   - `customer_name`, `delivery_address`, etc.

## Architecture

```
Caller → Twilio → WebSocket → Server → OpenAI Realtime API
                     ↓
                 Supabase (call recordings)
                     ↓
                 Dashboard (real-time monitoring)
```

## API Endpoints

- `POST /incoming-call` - Twilio webhook for incoming calls
- `POST /demo-start` - Start the AI demo after key press
- `POST /call-status` - Twilio status callback
- `GET /call-status?callSid=xxx` - Check call status from frontend
- `POST /recording-status` - Twilio recording callback → Supabase
- `GET /api/validate-session/{id}` - Validate demo session
- `WS /media-stream` - Twilio media stream WebSocket
- `WS /dashboard` - Real-time dashboard WebSocket

## Development

### Project Structure

```
├── server.py                 # Main FastAPI server
├── config.py                 # Configuration and environment variables
├── requirements.txt          # Python dependencies
├── supabase_migration.sql    # Database schema for Supabase
├── services/
│   ├── audio_service.py      # Audio format conversion (mulaw/pcm16)
│   ├── openai_service.py     # OpenAI Realtime API integration
│   ├── twilio_service.py     # Twilio TwiML generation
│   └── log_utils.py          # Logging utilities
```

### Audio Format

- **Twilio → OpenAI**: mulaw 8kHz (phone quality)
- **OpenAI → Twilio**: mulaw 8kHz (phone quality)
- **Dashboard**: mulaw 8kHz (converted to pcm16 24kHz on frontend)

## Troubleshooting

### Supabase Connection Issues

If recordings aren't being stored:

1. Check Supabase credentials in `.env`
2. Verify the `calls` table exists (run migration)
3. Check RLS policies allow inserts
4. Review server logs for Supabase errors

### Audio Quality Issues

- Ensure Twilio webhook URLs use HTTPS
- Check network latency to OpenAI API
- Verify mulaw audio format is maintained throughout

### Call Recording Not Working

1. Verify recording callback URL is set in Twilio
2. Check that URL is publicly accessible
3. Ensure Supabase credentials are correct
4. Review logs in `/recording-status` endpoint

## License

See LICENSE file for details.

## Support

For issues or questions, contact: faizan@finlumina.com
