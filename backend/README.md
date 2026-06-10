# Sellix Voice Agent

Ultra-low-latency voice conversational agent for Python.

## Architecture

This voice agent operates with three core steps optimized for speed:

1. **STT (Faster-Whisper):** Continuously monitors your microphone level (RMS energy). When speaking stops, it pushes the small audio chunk to local CPU-optimized Whisper (`tiny.en`) for near-instant transcription.
2. **LLM (Groq API):** Instantly submits text to Groq API (`openai/gpt-oss-20b`) with stream mode. As the text chunks stream back continuously, they are aggregated tightly into phrases.
3. **TTS (KittenTTS):** Runs in a separate thread, synthesizes each phrase with KittenTTS, and streams the generated PCM chunks back to the browser for immediate playback.
4. **Interruption Handling:** Using `threading.Event`, if you begin speaking while the TTS is playing, all queues are immediately flushed, enabling ultra-fast conversational latency without talking over each other. 

## Setup Instructions (Windows)

1. Ensure Python 3.10+ is installed.
2. In powershell or command prompt run:
```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

3. Ensure PyAudio installs correctly on Windows. You may need to download the appropriate `.whl` if `pip install pyaudio` fails, or run `pipwin install pyaudio`.

4. **KittenTTS Installation**: Install the backend dependencies, including the KittenTTS wheel:
```powershell
pip install -r requirements.txt
```

Optionally override the default KittenTTS model and voice with environment variables:
```powershell
$env:KITTEN_MODEL="KittenML/kitten-tts-nano-0.8"
$env:KITTEN_VOICE="Jasper"
```

5. **Environment Variable**: Set your Groq API key:
```powershell
$env:GROQ_API_KEY="gsk_your_key_here"
```

## Running the Agent

```powershell
python main.py
```

## Plugging a different LLM Later

Inside `main.py`, locate `self.generate_response(user_text)`.

To plug in another provider (e.g. OpenAI):
```python
# Change self.groq_client to self.openai_client
import openai
self.openai_client = openai.Client()

# Then swap the completion call:
completion = self.openai_client.chat.completions.create(
    model="gpt-4o-mini",
    messages=self.conversation_history,
    stream=True
)
```

## Ringg AI Missed-Call Fallback Integration

This backend now supports Ringg API integration so your AI agent can call people back when you miss their call.

Set these environment variables before starting the backend:

```powershell
$env:RINGG_API_KEY="your_ringg_api_key"
$env:RINGG_AGENT_ID="your_outbound_agent_id"
$env:RINGG_FROM_NUMBER_ID="your_workspace_number_id"
$env:RINGG_OWNER_NUMBER="+918849835941"
$env:RINGG_BASE_URL="https://prod-api.ringg.ai/ca/api/v0"
$env:RINGG_FALLBACK_WEBHOOK_SECRET="optional_shared_secret"
```

### Endpoints Added

- GET /api/ringg/workspace
    - Verifies X-API-KEY auth by reading workspace info.

- GET /api/ringg/call-history
    - Pulls call history from Ringg.

- POST /api/ringg/call
    - Manually initiate an outbound AI call.
    - Body example:

```json
{
    "name": "John Doe",
    "mobile_number": "+919876543210",
    "custom_args_values": {
        "callee_name": "John",
        "source": "manual"
    }
}
```

- POST /api/ringg/missed-call-fallback
    - Use this as webhook target from your telephony provider when a call is missed.
    - If status is missed/no_answer/unanswered, backend triggers Ringg outbound callback automatically.
    - Include header X-FALLBACK-SECRET if RINGG_FALLBACK_WEBHOOK_SECRET is set.
    - Body example:

```json
{
    "caller_number": "+919876543210",
    "to_number": "+918849835941",
    "caller_name": "Amit",
    "call_status": "no_answer",
    "trigger_callback": true,
    "custom_args_values": {
        "reason": "user_missed_call"
    }
}
```

- POST /api/webhooks/twilio/missed-call
    - Twilio-compatible adapter endpoint (accepts form-urlencoded or JSON payloads like CallStatus, From, To).
    - Automatically maps Twilio fields into the fallback payload and triggers callback for missed/no-answer style statuses.

- POST /api/webhooks/exotel/missed-call
    - Exotel-compatible adapter endpoint (accepts form-urlencoded or JSON payloads like CallStatus, CallFrom, CallTo).
    - Automatically maps Exotel fields into the fallback payload and triggers callback for missed/no-answer style statuses.

### How to Wire It End-to-End

1. In Ringg dashboard, create/select an outbound assistant and copy:
     - agent_id
     - from_number_id
2. Put them in backend env vars.
3. In your phone system (Twilio, Exotel, etc.), configure "missed/no-answer" webhook to POST to:
    - http://your-server:8000/api/webhooks/twilio/missed-call (for Twilio)
    - http://your-server:8000/api/webhooks/exotel/missed-call (for Exotel)
    - or keep using http://your-server:8000/api/ringg/missed-call-fallback (generic)
4. Pass caller number and status in webhook payload.
5. Include called number as to_number (or user_number). Backend will only trigger callback when it matches RINGG_OWNER_NUMBER.
6. Backend auto-initiates Ringg callback using your AI assistant.

## Ringg AI Command-Driven Calling (Web App Voice)

You can ask Sellix directly in the web app to call someone on your behalf.
Example voice commands:

- "Call Rahul at +9198xxxxxx10 and tell him I will be late."
- "Call Prince" or "Call Ravi".
- "Check what happened on that call."

Backend behavior:

1. Sellix uses `ringg_call_contact` to place an outbound Ringg call.
2. Sellix uses `ringg_get_call_report` to fetch call status and available summary/transcript from Ringg history.
3. Sellix speaks back a short after-call update in the same voice session.

Requirements:

- `RINGG_API_KEY`, `RINGG_AGENT_ID`, and `RINGG_FROM_NUMBER_ID` must be set and valid.
- Ringg call history should contain status/summary fields for best reporting quality.

Named contacts configured by default:

- Prince: +918320696909
- Ravi: +919925554337
