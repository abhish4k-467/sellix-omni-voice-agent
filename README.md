<p align="center">
  <img src="assets/sellix-banner.png" alt="Sellix Voice Agent" width="100%">
</p>

<h1 align="center">Sellix Voice Agent</h1>

<p align="center">
  Ultra-low-latency conversational AI voice agent powered by Faster-Whisper, Groq, and KittenTTS.
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#installation">Installation</a> •
  <a href="#running-the-agent">Run</a> •
  <a href="#ringg-ai-integration">Ringg AI</a>
</p>

---

## Features

* ⚡ Ultra-low-latency voice conversations
* 🎙️ Faster-Whisper speech recognition
* 🧠 Groq-powered streaming LLM responses
* 🔊 Real-time KittenTTS voice synthesis
* ✋ Instant interruption handling
* 🌐 Browser-based voice interaction
* 📞 Ringg AI outbound calling integration
* 🔄 Missed-call callback automation
* 🚀 Production-ready architecture

---

## Architecture

```text
Microphone
    │
    ▼
Faster-Whisper (STT)
    │
    ▼
Groq LLM Streaming
    │
    ▼
Response Aggregator
    │
    ▼
KittenTTS
    │
    ▼
Browser Audio Playback
```

### Speech-to-Text

The agent continuously monitors microphone RMS energy. When speech stops, audio is sent to a local Faster-Whisper model (`tiny.en`) for near-instant transcription.

### LLM Processing

Transcribed text is streamed to Groq using `openai/gpt-oss-20b`. Responses arrive token-by-token and are grouped into natural speech phrases.

### Text-to-Speech

Generated phrases are synthesized by KittenTTS in a dedicated thread and streamed back to the browser for immediate playback.

### Interruption Handling

Using `threading.Event`, ongoing TTS playback is immediately cancelled when the user starts speaking, creating a natural conversational experience.

---

## Installation

### Requirements

* Windows
* Python 3.10+
* Microphone
  
# Docker Deployment

## Pull Latest Image

```bash
docker pull abhish4k/sellix:tagname
```

## Stop and Remove Existing Container

```bash
docker rm -f sellix-container
```

## Run Container

```bash
docker run -d \
  --name sellix-container \
  -e GROQ_API_KEY=YOUR_GROQ_API_KEY \
  -e TAVILY_API_KEY=YOUR_TAVILY_API_KEY \
  -p 8000:8000 \
  abhish4k/sellix:v1
```

## Push New Docker Image

After building your image locally:

```bash
docker build -t abhish4k/sellix:tagname .
```

Push it to Docker Hub:

```bash
docker push abhish4k/sellix:tagname
```

## Verify Container Status

```bash
docker ps
```

## View Container Logs

```bash
docker logs -f sellix-container
```

## Access Application

Once the container is running, open:

```
http://localhost:8000
```

## Update to a New Version

```bash
docker pull abhish4k/sellix:tagname

docker rm -f sellix-container

docker run -d \
  --name sellix-container \
  -e GROQ_API_KEY=YOUR_GROQ_API_KEY \
  -e TAVILY_API_KEY=YOUR_TAVILY_API_KEY \
  -p 8000:8000 \
  abhish4k/sellix:tagname
```


### Create Virtual Environment

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### PyAudio

If installation fails:

```powershell
pipwin install pyaudio
```

or install a compatible wheel manually.

### Optional KittenTTS Configuration

```powershell
$env:KITTEN_MODEL="KittenML/kitten-tts-nano-0.8"
$env:KITTEN_VOICE="Jasper"
```

### Configure Groq API Key

```powershell
$env:GROQ_API_KEY="gsk_your_key_here"
```

---

## Running the Agent

```powershell
python main.py
```

---

## Switching LLM Providers

Locate:

```python
self.generate_response(user_text)
```

Example OpenAI replacement:

```python
import openai

self.openai_client = openai.Client()

completion = self.openai_client.chat.completions.create(
    model="gpt-4o-mini",
    messages=self.conversation_history,
    stream=True
)
```

---

# Ringg AI Integration

Sellix supports Ringg AI outbound calling and missed-call recovery workflows.

## Environment Variables

```powershell
$env:RINGG_API_KEY="your_ringg_api_key"
$env:RINGG_AGENT_ID="your_outbound_agent_id"
$env:RINGG_FROM_NUMBER_ID="your_workspace_number_id"
$env:RINGG_OWNER_NUMBER="+918849835941"
$env:RINGG_BASE_URL="https://prod-api.ringg.ai/ca/api/v0"
$env:RINGG_FALLBACK_WEBHOOK_SECRET="optional_shared_secret"
```

---

## API Endpoints

| Endpoint                                | Description                  |
| --------------------------------------- | ---------------------------- |
| `GET /api/ringg/workspace`              | Verify Ringg credentials     |
| `GET /api/ringg/call-history`           | Fetch call history           |
| `POST /api/ringg/call`                  | Start an outbound AI call    |
| `POST /api/ringg/missed-call-fallback`  | Trigger missed-call callback |
| `POST /api/webhooks/twilio/missed-call` | Twilio webhook adapter       |
| `POST /api/webhooks/exotel/missed-call` | Exotel webhook adapter       |

---

## Outbound Call Example

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

---

## Missed Call Example

```json
{
  "caller_number": "+919876543210",
  "to_number": "+918849835941",
  "caller_name": "Amit",
  "call_status": "no_answer",
  "trigger_callback": true
}
```

---

## End-to-End Setup

1. Create or select an outbound assistant in Ringg.
2. Copy:

   * `agent_id`
   * `from_number_id`
3. Configure environment variables.
4. Configure your telephony provider webhook:

   * `/api/webhooks/twilio/missed-call`
   * `/api/webhooks/exotel/missed-call`
5. Deploy and start the backend.
6. Missed calls automatically trigger AI-powered callbacks.

---

# Voice Command Calling

Users can instruct Sellix to place phone calls directly.

### Example Commands

```text
Call Rahul and tell him I'll be late.

Call Prince.

Call Ravi.

Check what happened on that call.
```

### Workflow

1. Sellix places a Ringg outbound call.
2. Ringg executes the AI conversation.
3. Sellix retrieves call history and summaries.
4. Sellix reports the outcome back within the active voice session.

---

## Tech Stack

| Component          | Technology     |
| ------------------ | -------------- |
| Speech Recognition | Faster-Whisper |
| LLM                | Groq           |
| Text-to-Speech     | KittenTTS      |
| Backend            | Python         |
| Voice Streaming    | WebSocket      |
| Telephony          | Ringg AI       |

---

## License

MIT License

---

<p align="center">
Built with ❤️ for ultra-fast AI voice conversations.
</p>
