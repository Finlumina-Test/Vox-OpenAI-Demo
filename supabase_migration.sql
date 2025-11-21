-- Supabase Migration for Vox AI Call Recordings
-- This migration creates the calls table for storing Twilio call recordings and metadata

-- Create calls table
CREATE TABLE IF NOT EXISTS calls (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    call_sid TEXT UNIQUE NOT NULL,
    restaurant_id TEXT NOT NULL,
    customer_name TEXT,
    phone_number TEXT,
    call_date TIMESTAMPTZ DEFAULT NOW(),
    call_duration INTEGER, -- in seconds
    audio_url TEXT, -- URL from Supabase Storage or Twilio
    transcript JSONB DEFAULT '[]'::jsonb, -- Array of {speaker, text, timestamp}
    order_items JSONB DEFAULT '[]'::jsonb, -- Array of {item, quantity, price, notes}
    total_price TEXT,
    payment_method TEXT,
    delivery_address TEXT,
    delivery_time TEXT,
    special_instructions TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_calls_restaurant_id ON calls(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_calls_call_sid ON calls(call_sid);
CREATE INDEX IF NOT EXISTS idx_calls_call_date ON calls(call_date DESC);

-- Enable Row Level Security (RLS)
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;

-- Create policy to allow all operations (adjust this based on your auth requirements)
-- For production, you should restrict this based on authenticated users
DROP POLICY IF EXISTS "Allow all operations on calls" ON calls;
CREATE POLICY "Allow all operations on calls"
    ON calls
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to auto-update updated_at
DROP TRIGGER IF EXISTS update_calls_updated_at ON calls;
CREATE TRIGGER update_calls_updated_at
    BEFORE UPDATE ON calls
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Add comments for documentation
COMMENT ON TABLE calls IS 'Stores Twilio call recordings and order details for restaurant AI assistant';
COMMENT ON COLUMN calls.call_sid IS 'Unique Twilio call identifier';
COMMENT ON COLUMN calls.audio_url IS 'URL to audio recording (Supabase Storage or Twilio)';
COMMENT ON COLUMN calls.transcript IS 'JSON array of conversation turns: [{speaker, text, timestamp}]';
COMMENT ON COLUMN calls.order_items IS 'JSON array of order items: [{item, quantity, price, notes}]';
