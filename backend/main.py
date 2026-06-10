import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
# Increase download timeout and force offline if we only want cached models
os.environ['HF_HUB_DOWNLOAD_TIMEOUT'] = '60'
os.environ['HF_HUB_OFFLINE'] = '1'

import sys
import time
import queue
import threading
import warnings
import asyncio
import json
import datetime
import hashlib
import requests
import httpx
import re
from pathlib import Path
from typing import Optional, Any, Dict

warnings.filterwarnings('ignore')

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

import config
from groq import Groq

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

interruption_flag = threading.Event()
tts_queue = queue.Queue()
audio_playback_queue = queue.Queue()


class RinggOutboundCallRequest(BaseModel):
    name: str = Field(..., description="Recipient name")
    mobile_number: str = Field(..., description="Recipient number in international format")
    custom_args_values: Optional[Dict[str, Any]] = None
    agent_id: Optional[str] = None
    from_number_id: Optional[str] = None


class RinggMissedCallFallbackRequest(BaseModel):
    caller_number: str = Field(..., description="Number of the person who tried to reach you")
    to_number: Optional[str] = Field(default=None, description="Number that was called (your number)")
    user_number: Optional[str] = Field(default=None, description="Alias for to_number from some webhook providers")
    caller_name: Optional[str] = Field(default="Missed Caller")
    call_status: Optional[str] = Field(default="missed")
    status: Optional[str] = None
    trigger_callback: bool = True
    custom_args_values: Optional[Dict[str, Any]] = None


def _ringg_headers() -> Dict[str, str]:
    api_key = (config.RINGG_API_KEY or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="RINGG_API_KEY is not configured on the backend.")
    return {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }


def _ringg_make_request(method: str, endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    base_url = (config.RINGG_BASE_URL or "https://prod-api.ringg.ai/ca/api/v0").rstrip("/")
    url = f"{base_url}{endpoint}"
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=_ringg_headers(),
            json=payload,
            timeout=25,
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Ringg API network error: {e}") from e

    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}

    if not response.ok:
        raise HTTPException(status_code=response.status_code, detail={"ringg_error": data})
    return data


def _ringg_outbound_payload(req: RinggOutboundCallRequest) -> Dict[str, Any]:
    agent_id = (req.agent_id or config.RINGG_AGENT_ID or "").strip()
    from_number_id = (req.from_number_id or config.RINGG_FROM_NUMBER_ID or "").strip()

    if not agent_id or not from_number_id:
        raise HTTPException(
            status_code=400,
            detail="Missing Ringg call routing config. Set RINGG_AGENT_ID and RINGG_FROM_NUMBER_ID or provide them in request.",
        )

    return {
        "name": req.name.strip() or "Contact",
        "mobile_number": req.mobile_number.strip(),
        "agent_id": agent_id,
        "from_number_id": from_number_id,
        "custom_args_values": req.custom_args_values or {},
    }


def _normalize_phone(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "91" + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    return digits


def _normalize_contact_name(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    return re.sub(r"\s+", " ", raw)


def _resolve_known_contact_number(name: str) -> Optional[str]:
    contacts = getattr(config, "RINGG_CONTACTS", {}) or {}
    if not isinstance(contacts, dict):
        return None

    target = _normalize_contact_name(name)
    if not target:
        return None

    # Exact key match first.
    if target in contacts and contacts[target]:
        return str(contacts[target]).strip()

    # Fuzzy contains match for spoken variants like "Prince bhai".
    for key, value in contacts.items():
        normalized_key = _normalize_contact_name(str(key))
        if not normalized_key or not value:
            continue
        if normalized_key in target or target in normalized_key:
            return str(value).strip()
    return None


def _pick_first(payload: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for k in keys:
        v = payload.get(k)
        if v is not None and str(v).strip() != "":
            return v
    return default


def _normalize_status(value: Any) -> str:
    if value is None:
        return ""
    status = str(value).strip().lower().replace(" ", "_")
    alias_map = {
        "no-answer": "no_answer",
        "not_answered": "no_answer",
        "missed_call": "missed",
        "not_picked": "not_picked",
        "cancelled": "cancelled",
    }
    return alias_map.get(status, status)


async def _parse_webhook_payload(request: Request) -> Dict[str, Any]:
    content_type = (request.headers.get("content-type") or "").lower()
    data: Dict[str, Any] = {}

    if "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                data.update(body)
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            data.update(dict(form))
        except Exception:
            pass

    # Keep query params as a fallback/override path for simple webhook tests.
    for k, v in request.query_params.items():
        if k not in data or str(data.get(k, "")).strip() == "":
            data[k] = v

    return data


def _build_fallback_from_provider(provider: str, raw_payload: Dict[str, Any]) -> RinggMissedCallFallbackRequest:
    provider_key = provider.strip().lower()

    if provider_key == "twilio":
        status = _normalize_status(_pick_first(raw_payload, ["CallStatus", "call_status", "status"], "no_answer"))
        caller = _pick_first(raw_payload, ["From", "from", "Caller", "caller_number"], "")
        called = _pick_first(raw_payload, ["To", "to", "to_number", "user_number"], "")
        caller_name = _pick_first(raw_payload, ["CallerName", "caller_name"], "Missed Caller")
        custom = {
            "provider": "twilio",
            "call_sid": _pick_first(raw_payload, ["CallSid", "call_sid"], ""),
            "direction": _pick_first(raw_payload, ["Direction", "direction"], ""),
        }
    elif provider_key == "exotel":
        status = _normalize_status(_pick_first(raw_payload, ["CallStatus", "call_status", "Status", "status"], "no_answer"))
        caller = _pick_first(raw_payload, ["CallFrom", "call_from", "From", "from", "caller_number"], "")
        called = _pick_first(raw_payload, ["CallTo", "call_to", "To", "to", "to_number", "user_number"], "")
        caller_name = _pick_first(raw_payload, ["CallerName", "caller_name", "name"], "Missed Caller")
        custom = {
            "provider": "exotel",
            "call_sid": _pick_first(raw_payload, ["CallSid", "call_sid", "Sid", "sid"], ""),
            "direction": _pick_first(raw_payload, ["Direction", "direction"], ""),
        }
    else:
        status = _normalize_status(_pick_first(raw_payload, ["call_status", "status"], "missed"))
        caller = _pick_first(raw_payload, ["caller_number", "from", "From"], "")
        called = _pick_first(raw_payload, ["to_number", "user_number", "to", "To"], "")
        caller_name = _pick_first(raw_payload, ["caller_name", "name", "CallerName"], "Missed Caller")
        custom = {"provider": provider_key or "generic"}

    if not str(caller).strip():
        raise HTTPException(status_code=400, detail=f"Missing caller number in {provider_key} webhook payload.")

    return RinggMissedCallFallbackRequest(
        caller_number=str(caller),
        to_number=str(called) if called is not None else None,
        caller_name=str(caller_name) if caller_name is not None else "Missed Caller",
        call_status=status,
        trigger_callback=True,
        custom_args_values=custom,
    )


def _process_missed_call_fallback(
    payload: RinggMissedCallFallbackRequest,
    x_fallback_secret: Optional[str],
) -> Dict[str, Any]:
    expected_secret = (config.RINGG_FALLBACK_WEBHOOK_SECRET or "").strip()
    if expected_secret and x_fallback_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    normalized_status = (payload.call_status or payload.status or "").strip().lower()
    miss_like_statuses = {"missed", "no_answer", "not_picked", "unanswered", "busy", "failed", "cancelled", "canceled"}
    if normalized_status and normalized_status not in miss_like_statuses:
        return {
            "ok": True,
            "triggered": False,
            "reason": f"Ignoring status '{normalized_status}'.",
        }

    if not payload.trigger_callback:
        return {"ok": True, "triggered": False, "reason": "trigger_callback is false."}

    owner_number = _normalize_phone(getattr(config, "RINGG_OWNER_NUMBER", ""))
    called_number = _normalize_phone(payload.to_number or payload.user_number)
    if owner_number and called_number and owner_number != called_number:
        return {
            "ok": True,
            "triggered": False,
            "reason": "Missed call was not for configured owner number.",
            "owner_number": owner_number,
            "called_number": called_number,
        }

    outbound = RinggOutboundCallRequest(
        name=(payload.caller_name or "Missed Caller"),
        mobile_number=payload.caller_number,
        custom_args_values={
            **(payload.custom_args_values or {}),
            "callee_name": (payload.caller_name or "Missed Caller"),
            "mobile_number": payload.caller_number,
            "source": "missed_call_fallback",
            "owner_number": owner_number,
            "called_number": called_number,
        },
    )

    body = _ringg_outbound_payload(outbound)
    data = _ringg_make_request("POST", "/calling/outbound/individual", payload=body)
    return {
        "ok": True,
        "triggered": True,
        "normalized_status": normalized_status or "missed",
        "ringg_response": data,
    }


def _extract_ringg_call_id(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        for key in ["Unique Call ID", "unique_call_id", "call_id", "callId", "id"]:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                if re.match(r"^[0-9a-fA-F-]{20,}$", value.strip()):
                    return value.strip()
        for value in payload.values():
            nested = _extract_ringg_call_id(value)
            if nested:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = _extract_ringg_call_id(item)
            if nested:
                return nested
    return None


def _ringg_history_entries(payload: Any) -> list[Dict[str, Any]]:
    entries: list[Dict[str, Any]] = []

    def walk(node: Any):
        if isinstance(node, dict):
            normalized = {str(k).lower().replace(" ", "_"): v for k, v in node.items()}
            marker_keys = {"call_status", "unique_call_id", "call_direction", "from_number", "to_number", "mobile_number", "agent_id"}
            if marker_keys.intersection(normalized.keys()):
                entries.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return entries


def _ringg_call_status(entry: Dict[str, Any]) -> str:
    for key in ["Call Status", "call_status", "status", "State", "state"]:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower().replace(" ", "_")
    return "unknown"


def _ringg_call_summary_text(entry: Dict[str, Any]) -> str:
    summary_keys = [
        "call_summary",
        "summary",
        "transcript_summary",
        "ai_summary",
        "conversation_summary",
        "outcome",
        "notes",
        "transcript",
    ]
    for key in summary_keys:
        if key in entry and entry[key]:
            return str(entry[key]).strip()

    lower_map = {str(k).lower().replace(" ", "_"): v for k, v in entry.items()}
    for key in summary_keys:
        value = lower_map.get(key)
        if value:
            return str(value).strip()
    return ""


def _ringg_transcript_text(entry: Dict[str, Any]) -> str:
    transcript_keys = [
        "transcript",
        "call_transcript",
        "conversation",
        "messages",
    ]

    value = None
    for key in transcript_keys:
        if key in entry and entry[key] not in (None, "", "[]", "{}"):
            value = entry[key]
            break

    if value is None:
        lower_map = {str(k).lower().replace(" ", "_"): v for k, v in entry.items()}
        for key in transcript_keys:
            v = lower_map.get(key)
            if v not in (None, "", "[]", "{}"):
                value = v
                break

    if value is None:
        return ""

    def _extract_dialogue_from_jsonish(raw_text: str) -> str:
        raw_text = (raw_text or "").strip()
        if not raw_text:
            return ""

        lines: list[str] = []
        # Handles logs like {"bot":"..."} / {"user":"..."} even when payload is partially malformed.
        for speaker, text in re.findall(r'"(bot|user|assistant|agent|caller|callee)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', raw_text, flags=re.IGNORECASE):
            clean_text = text.encode("utf-8").decode("unicode_escape").strip()
            if clean_text:
                lines.append(f"{speaker.lower()}: {clean_text}")

        # Fallback: capture generic text/content/message fields if no speaker-tagged lines.
        if not lines:
            for text in re.findall(r'"(?:text|content|message)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', raw_text, flags=re.IGNORECASE):
                clean_text = text.encode("utf-8").decode("unicode_escape").strip()
                if clean_text:
                    lines.append(clean_text)

        return "\n".join(lines).strip()

    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw in ("[]", "{}"):
            return ""
        try:
            parsed = json.loads(raw)
            value = parsed
        except Exception:
            jsonish_dialogue = _extract_dialogue_from_jsonish(raw)
            return jsonish_dialogue if jsonish_dialogue else raw

    lines: list[str] = []

    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                speaker = item.get("speaker") or item.get("role") or item.get("from") or ""
                text = item.get("text") or item.get("content") or item.get("message") or ""
                text = str(text).strip()
                if not text:
                    continue
                if speaker:
                    lines.append(f"{str(speaker).strip()}: {text}")
                else:
                    lines.append(text)
            elif isinstance(item, str) and item.strip():
                lines.append(item.strip())
    elif isinstance(value, dict):
        for k in ["text", "content", "message", "transcript"]:
            if value.get(k):
                lines.append(str(value.get(k)).strip())
    elif isinstance(value, str) and value.strip():
        lines.append(value.strip())

    return "\n".join(lines).strip()


def _compress_transcript_for_summary(transcript_text: str, max_chars: int = 7000) -> str:
    text = (transcript_text or "").strip()
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-(max_chars - len(head)):]
    return head + "\n...\n" + tail


def _local_transcript_fallback_summary(transcript_text: str) -> str:
    lines = [ln.strip() for ln in transcript_text.splitlines() if ln.strip()]
    if not lines:
        return "I could not extract enough transcript text to summarize this call yet."

    recent = lines[-8:]
    user_line = ""
    bot_line = ""
    for ln in reversed(recent):
        low = ln.lower()
        if not user_line and (low.startswith("user:") or low.startswith("caller:")):
            user_line = ln.split(":", 1)[1].strip() if ":" in ln else ln
        if not bot_line and (low.startswith("bot:") or low.startswith("assistant:") or low.startswith("agent:")):
            bot_line = ln.split(":", 1)[1].strip() if ":" in ln else ln
        if user_line and bot_line:
            break

    if user_line and bot_line:
        return (
            f"The call covered a quick update and response exchange. "
            f"The other person said: {user_line}. "
            f"Sellix responded with: {bot_line}."
        )

    return "The call transcript is available, but only partial dialogue was captured so far."


def _summarize_transcript_with_groq(groq_client: Any, transcript_text: str) -> str:
    compact = _compress_transcript_for_summary(transcript_text)
    if not compact:
        return ""

    try:
        completion = groq_client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You summarize phone call transcripts. Return 2-4 concise sentences: what happened, what the other person said, and the next step. Never return raw JSON, logs, call IDs, timestamps, or technical metadata."
                },
                {
                    "role": "user",
                    "content": f"Transcript:\n{compact}"
                }
            ],
            temperature=0.2,
            max_tokens=180,
            stream=False,
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _ringg_find_call_entry(history_payload: Any, call_id: Optional[str]) -> Optional[Dict[str, Any]]:
    entries = _ringg_history_entries(history_payload)
    if not entries:
        return None

    if call_id:
        for entry in entries:
            entry_id = _extract_ringg_call_id(entry)
            if entry_id and entry_id == call_id:
                return entry

    return entries[0]


def _ringg_is_live_status(status: str) -> bool:
    return status in {"registered", "ongoing", "retry", "initiated", "queued"}

def stream_pcm_chunks(audio: np.ndarray, chunk_size: int = config.TTS_STREAM_CHUNK_SAMPLES):
    """Yield small int16 PCM chunks so the browser can play them incrementally."""
    audio = np.asarray(audio, dtype=np.float32).flatten()
    if audio.size == 0:
        return

    clipped = np.clip(audio, -1.0, 1.0)
    for start in range(0, clipped.size, chunk_size):
        yield (clipped[start:start + chunk_size] * 32767).astype(np.int16).tobytes()

class VoiceAgent:
    def __init__(self):
        self.google_token = None
        self.memory_cache = {}
        self.memory_upsert_queue = []
        self.last_ringg_call_id = None
        self.last_ringg_call_report = None
        self.last_ringg_call_signature = ""
        self.last_ringg_call_at = 0.0
        self.live_ringg_summary_cache = {}
        self.speculative_cache = {"prompt": "", "response": ""}
        self.is_speculating = False
        print('[System] Initializing LLM/STT Client (Groq)...')
        # Pass an explicit HTTP client to avoid Groq 0.5.0 constructing its
        # own client with deprecated httpx kwargs on newer httpx versions.
        self.groq_client = Groq(
            api_key=config.GROQ_API_KEY,
            http_client=httpx.Client(timeout=30.0),
        )
        
        try:
            from pinecone import Pinecone
            self.pc = Pinecone(api_key=config.PINECONE_API_KEY)
            self.pinecone_index = self.pc.Index(host=config.PINECONE_HOST)
        except Exception as e:
            print(f"[Pinecone Init Error] {e}")
            self.pc = None
            self.pinecone_index = None
        
        print('[System] Fetching internal Knowledge Base from Pinecone...')
        try:
            # Generic broad queries to fetch all/most chunks from the KB
            queries = [
                "Tarkshy Consultancy Services overview what is sellix features",
                "team members creators owners pricing architecture employees details",
                "Shrey Hemangbhai Desai, Kush Kundariya, Raviprakash Patel, Mayur Patil, Prince Sikotra, Abhishek Mitra, Priyanshu"
            ]
            
            all_text_chunks = set()
            
            for q in queries:
                if not self.pc or not self.pinecone_index: break
                embed_data = self.pc.inference.embed(
                    model="multilingual-e5-large", 
                    inputs=[q],
                    parameters={"input_type": "query"}
                )
                query_vector = embed_data[0].values
                res = self.pinecone_index.query(vector=query_vector, top_k=20, include_metadata=True)
                
                for match in res.get("matches", []):
                    if "metadata" in match and "text" in match.metadata:
                        all_text_chunks.add(match.metadata["text"])
                        
            combined_kb = "\n\n".join(all_text_chunks)
            if combined_kb:
                print(f"[System] Successfully fetched {len(all_text_chunks)} sections from Pinecone.")
                system_content = config.SYSTEM_PROMPT + "\n\n--- INTERNAL COMPANY KNOWLEDGE BASE ---\n" + combined_kb
            else:
                print(f"[System] Knowledge base returned empty.")
                system_content = config.SYSTEM_PROMPT
                
        except Exception as e:
            print(f"[Warning] Failed to prefetch Knowledge Base: {e}")
            system_content = config.SYSTEM_PROMPT

        print('[System] Fetching user memories from Pinecone...')
        self.cached_user_memories = []
        try:
            if self.pc and self.pinecone_index:
                # Use a very generic broad query to fetch top 100 past user statements
                mem_embed = self.pc.inference.embed(
                    model="multilingual-e5-large", 
                    inputs=["person name preference favorite facts details history schedule"],
                    parameters={"input_type": "query"}
                )
                res = self.pinecone_index.query(
                    vector=mem_embed[0].values, 
                    top_k=100, 
                    namespace="user_memory", 
                    include_metadata=True
                )
                for m in res.get("matches", []):
                    if "metadata" in m and "text" in m.metadata:
                        self.cached_user_memories.append(m.metadata["text"])
                if self.cached_user_memories:
                    print(f"[System] Cached {len(self.cached_user_memories)} user memories.")
                    mem_block = "\n".join(self.cached_user_memories)
                    system_content += f"\n\n--- RECALLED USER MEMORIES ---\n{mem_block}"
        except Exception as e:
            print(f"[Warning] Failed to prefetch User Memories: {e}")

        current_time_str = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime('%Y-%m-%dT%H:%M:%S%z')
        calendar_prompt = f"\n\n--- CALENDAR INTEGRATION ---\nCurrent date and time with timezone offset: {current_time_str}. You have tools to check and modify the user's Google Calendar. If the user provides the title and time for an event, calculate the start_time and end_time string logically and create the event. Only ask for clarification if details are missing. Provide start_time and end_time strictly in ISO 8601 format WITH the explicit timezone offset (e.g., {current_time_str})."
        self.conversation_history = [{'role': 'system', 'content': system_content + calendar_prompt}]
                
        print('[System] Initializing TTS Engine (KittenTTS)...')
        try:
            # Configure phonemizer/espeak to use local espeakng_loader assets.
            try:
                import espeakng_loader
                from phonemizer.backend.espeak.wrapper import EspeakWrapper

                espeak_lib = espeakng_loader.get_library_path()
                espeak_data = espeakng_loader.get_data_path()

                # Keep explicit env hints for downstream wrappers.
                os.environ['PHONEMIZER_ESPEAK_LIBRARY'] = espeak_lib
                os.environ['ESPEAK_DATA_PATH'] = espeak_data

                if hasattr(EspeakWrapper, 'set_library'):
                    EspeakWrapper.set_library(espeak_lib)

                if hasattr(EspeakWrapper, 'set_data_path'):
                    EspeakWrapper.set_data_path(espeak_data)
                else:
                    # phonemizer>=3.3 removed set_data_path; keep backward-compatible API.
                    @staticmethod
                    def _compat_set_data_path(path=None):
                        if path:
                            EspeakWrapper.data_path = path

                    EspeakWrapper.set_data_path = _compat_set_data_path
                    EspeakWrapper.set_data_path(espeak_data)
            except Exception:
                pass

            local_extract = Path(__file__).resolve().parents[1] / 'kittentts_extract'
            if local_extract.exists() and str(local_extract) not in sys.path:
                # Prefer local extracted package so we can run with project-pinned fixes.
                sys.path.insert(0, str(local_extract))
            from kittentts import KittenTTS

            def _normalize_repo_id(model_name: str) -> str:
                return model_name if "/" in model_name else f"KittenML/{model_name}"

            def _resolve_local_model_dir(repo_id: str) -> Optional[Path]:
                # 1) Explicit folder override for fully offline deployments.
                explicit_dir = os.environ.get('KITTEN_MODEL_DIR')
                if explicit_dir:
                    p = Path(explicit_dir)
                    if (p / 'config.json').exists():
                        return p

                # 1a) Project-local mirror download folder.
                local_project_dir = Path(__file__).resolve().parent / 'kittentts_local_model'
                if (local_project_dir / 'config.json').exists():
                    return local_project_dir

                # 1b) Explicit file override when model assets are already present.
                explicit_model = os.environ.get('KITTEN_MODEL_PATH')
                explicit_voices = os.environ.get('KITTEN_VOICES_PATH')
                if explicit_model and explicit_voices:
                    model_path = Path(explicit_model)
                    voices_path = Path(explicit_voices)
                    if model_path.exists() and voices_path.exists():
                        synthetic_dir = model_path.parent
                        cfg_path = synthetic_dir / 'config.json'
                        if not cfg_path.exists():
                            cfg = {
                                'type': 'ONNX1',
                                'model_file': model_path.name,
                                'voices': voices_path.name,
                                'speed_priors': {},
                                'voice_aliases': {},
                            }
                            with open(cfg_path, 'w', encoding='utf-8') as f:
                                json.dump(cfg, f)
                        return synthetic_dir

                # 2) Already downloaded Hugging Face snapshot in local cache.
                try:
                    from huggingface_hub import snapshot_download
                    local_snapshot = snapshot_download(repo_id=repo_id, local_files_only=True)
                    p = Path(local_snapshot)
                    if (p / 'config.json').exists():
                        return p
                except Exception:
                    pass

                return None

            def _load_local_tts(local_dir: Path):
                from kittentts.onnx_model import KittenTTS_1_Onnx
                config_path = local_dir / 'config.json'
                with open(config_path, 'r', encoding='utf-8') as f:
                    tts_cfg = json.load(f)

                model_path = local_dir / tts_cfg['model_file']
                voices_path = local_dir / tts_cfg['voices']
                if not model_path.exists() or not voices_path.exists():
                    raise FileNotFoundError(
                        f'Missing local KittenTTS assets. model={model_path} voices={voices_path}'
                    )

                return KittenTTS_1_Onnx(
                    model_path=str(model_path),
                    voices_path=str(voices_path),
                    speed_priors=tts_cfg.get('speed_priors', {}),
                    voice_aliases=tts_cfg.get('voice_aliases', {}),
                )

            repo_id = _normalize_repo_id(config.KITTEN_MODEL)
            local_model_dir = _resolve_local_model_dir(repo_id)
            if local_model_dir:
                print(f'[System] Using local KittenTTS assets: {local_model_dir}')
                self.tts_client = _load_local_tts(local_model_dir)
            else:
                # In explicit offline mode, do not trigger Hub download attempts.
                if os.environ.get('HF_HUB_OFFLINE', '').strip() == '1':
                    print('[System] No local KittenTTS assets found while offline; using fallback TTS path.')
                    self.tts_client = None
                else:
                    # Fall back to the normal loader (downloads if needed).
                    self.tts_client = KittenTTS(config.KITTEN_MODEL)
            if self.tts_client:
                print(f'[System] KittenTTS ready with voice "{config.KITTEN_VOICE}"')
                print('[System] Warming up TTS model...')
                # Run a quick dummy inference so ONNX allocates everything into RAM before the first real request
                _ = self.tts_client.generate("Hi", voice=config.KITTEN_VOICE, speed=config.KITTEN_SPEED)
                print('[System] TTS warmup complete.')
        except Exception as e:
            print(f'[Warning] KittenTTS not found or failed to initialize: {e}')
            self.tts_client = None

        threading.Thread(target=self.tts_loop, daemon=True).start()
        threading.Thread(target=self.memory_batch_worker, daemon=True).start()

    def memory_batch_worker(self):
        import time
        while True:
            time.sleep(30)
            if not self.memory_upsert_queue:
                continue
                
            batch = self.memory_upsert_queue[:]
            self.memory_upsert_queue.clear()
            
            valid_facts = []
            for fact in batch:
                try:
                    relevance_check = self.groq_client.chat.completions.create(
                        model=config.LLM_MODEL,
                        messages=[
                            {"role": "system", "content": "Is this fact genuinely a useful long-term personal preference, habit, commitment, or personal fact worth remembering forever (like a favorite color, name, hobby, or schedule constraint)? Reply ONLY YES or NO. Skip throwaway greetings, simple commands, negative comments, or one-off questions."},
                            {"role": "user", "content": fact}
                        ],
                        temperature=0.0,
                        max_tokens=10
                    )
                    if "YES" in relevance_check.choices[0].message.content.upper():
                        valid_facts.append(fact)
                except Exception as e:
                    print(f"[Memory Filter Error] {e}")
                    
            if valid_facts and self.pc and self.pinecone_index:
                try:
                    import uuid
                    embed_data = self.pc.inference.embed(
                        model="multilingual-e5-large",
                        inputs=valid_facts,
                        parameters={"input_type": "passage"}
                    )
                    vectors = []
                    for i, data in enumerate(embed_data):
                        mem_id = "mem_" + str(uuid.uuid4())[:8]
                        vectors.append({"id": mem_id, "values": data.values, "metadata": {"text": valid_facts[i]}})
                    self.pinecone_index.upsert(vectors=vectors, namespace="user_memory")
                    print(f"[Memory Database] Saved {len(valid_facts)} useful facts from batch.")
                except Exception as e:
                    print(f"[Memory Upsert Error] {e}")

    def run_partial_stt_and_speculate(self, audio_bytes):
        if self.is_speculating:
            return
            
        self.is_speculating = True
            
        audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
        import io, wave
        audio_int16 = (np.clip(audio_np, -1.0, 1.0) * 32767).astype(np.int16)
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(config.AUDIO_RATE)
            wf.writeframes(audio_int16.tobytes())
        buffer.seek(0)
        buffer.name = "audio.wav"
        
        try:
            transcription = self.groq_client.audio.transcriptions.create(
                file=("audio.wav", buffer),
                model=config.WHISPER_MODEL,
                language="en",
                temperature=0.0
            )
            partial_text = transcription.text.strip()
            
            if len(partial_text.split()) < 3:
                self.is_speculating = False
                return
                
            messages = self.conversation_history + [{'role': 'user', 'content': partial_text}]
            
            completion = self.groq_client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                temperature=0.0,
                max_tokens=50,
                stream=False
            )
            print(f"[Speculation] Speculated response for '{partial_text}' generated.")
            self.speculative_cache["prompt"] = partial_text
            self.speculative_cache["response"] = completion.choices[0].message.content
        except Exception as e:
            pass
        finally:
            self.is_speculating = False

    def transcribe(self, audio_bytes):
        # We expect 16kHz float32 from the browser
        audio_np = np.frombuffer(audio_bytes, dtype=np.float32)
        
        import io
        import wave
        
        # Convert float32 space to int16 WAV for Groq API
        audio_int16 = (np.clip(audio_np, -1.0, 1.0) * 32767).astype(np.int16)
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(config.AUDIO_RATE)
            wf.writeframes(audio_int16.tobytes())
        
        buffer.seek(0)
        buffer.name = "audio.wav"
        
        try:
            transcription = self.groq_client.audio.transcriptions.create(
                file=("audio.wav", buffer),
                model=config.WHISPER_MODEL,
                prompt="Umbrella, Tarkshy Consultancy Services, Sellix, Shrey Hemangbhai Desai, Kush Kundariya, Raviprakash Patel, Mayur Patil, Prince Sikotra, Abhishek Mitra, Priyanshu.",
                language="en",
                temperature=0.0
            )
            text = transcription.text.strip()
        except Exception as e:
            print(f"[STT Error] {e}")
            return
        
        # Whisper Hallucination Filter
        lower_text = text.lower()
        hallucinated_phrases = ["thank you", "subtitles", "amara.org", "subscribe", "thanks for watching", "you.", "bye."]
        is_hallucination = any(phrase in lower_text for phrase in hallucinated_phrases) and len(text) < 30
        
        if len(text) > 2 and not is_hallucination:
            print(f'User: {text}')
            
            # Check Speculative Cache match
            if self.speculative_cache["prompt"] and (self.speculative_cache["prompt"].lower() in text.lower() or text.lower() in self.speculative_cache["prompt"].lower()):
                cached_resp = self.speculative_cache["response"]
                if cached_resp:
                    print(f"Sellix [Speculated]: {cached_resp}")
                    self.conversation_history.append({'role': 'user', 'content': text})
                    self.conversation_history.append({'role': 'assistant', 'content': cached_resp})
                    
                    # Queue pre-generated text to TTS
                    import re
                    sentences = re.split(r'(?<=[.!?]) +', cached_resp)
                    for s in sentences:
                        if s.strip():
                            tts_queue.put(s.strip())
                            
                    self.speculative_cache = {"prompt": "", "response": ""}
                    return
            
            self.speculative_cache = {"prompt": "", "response": ""}
            self.generate_response(text)

    def generate_response(self, user_text):
        if interruption_flag.is_set(): return
        
        if user_text:
            self.conversation_history.append({'role': 'user', 'content': user_text})
        
        import re
        import json
        
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Searches the web for real-time information. Use this whenever the user asks about current events, news, or factual information you don't know.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query"
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_events",
                    "description": "Get upcoming events from the user's Google Calendar.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "days_ahead": {
                                "type": "number",
                                "description": "Number of days ahead to look for events (default is 7)"
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "create_event",
                    "description": "Create a new event in the user's Google Calendar. Call this directly once you have the title and time/duration.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "The title or summary of the event"
                            },
                            "start_time": {
                                "type": "string",
                                "description": "Strict ISO 8601 string for start time WITH timezone offset (e.g., 2026-04-10T15:00:00+05:30)."
                            },
                            "end_time": {
                                "type": "string",
                                "description": "Strict ISO 8601 string for end time WITH timezone offset. If only duration is given, add it to start_time."
                            },
                            "description": {
                                "type": "string",
                                "description": "Optional description"
                            }
                        },
                        "required": ["title", "start_time", "end_time"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "send_whatsapp_message",
                    "description": "Send a WhatsApp message using WhatsApp Cloud API. Use this whenever user asks to send/message on WhatsApp. IMPORTANT: If recipient number is not provided, DO NOT ask for it and directly use the configured default recipient.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to": {
                                "type": "string",
                                "description": "Optional recipient phone number in international format, for example +918849835941. If omitted, backend default recipient is used."
                            },
                            "message": {
                                "type": "string",
                                "description": "Message text to send"
                            }
                        },
                        "required": ["message"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "ringg_call_contact",
                    "description": "Place a phone call via Ringg AI assistant on user's behalf. If mobile_number is omitted, resolve known contacts by name (for example Prince, Ravi).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Recipient name"
                            },
                            "mobile_number": {
                                "type": "string",
                                "description": "Optional recipient phone number in international format (e.g., +919876543210)."
                            },
                            "purpose": {
                                "type": "string",
                                "description": "Optional short context for why this call is being made."
                            }
                        },
                        "required": ["name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "ringg_get_call_report",
                    "description": "Fetch and summarize the latest Ringg call transcript. Return a clean human summary, not call logs.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "call_id": {
                                "type": "string",
                                "description": "Optional Ringg call ID. If omitted, uses the most recent call made in this session."
                            },
                            "wait_seconds": {
                                "type": "number",
                                "description": "Optional wait time before final check (0-120). Use this if user asked for after-call update."
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "save_memory",
                    "description": "Save a new persistent memory about the user's habits, preferences, or personal facts.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "fact": {
                                "type": "string",
                                "description": "The specific fact or preference to remember."
                            }
                        },
                        "required": ["fact"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "recall_memory",
                    "description": "Search the user's persistent memory for facts or preferences based on a keyword or query.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The topic or keyword to search for in memory."
                            }
                        },
                        "required": ["query"]
                    }
                }
            }
        ]
        
        # Inject fast semantic memory recall dynamically into this turn's context
        dynamic_messages = list(self.conversation_history)

        # Nudge model to avoid unnecessary clarification for WhatsApp recipient.
        dynamic_messages.append({
            "role": "system",
            "content": "For send_whatsapp_message: if the user does not provide a recipient phone number, call the tool with only message and use the backend default recipient. Do not ask for phone number in that case."
        })
        dynamic_messages.append({
            "role": "system",
            "content": "For calling on user's behalf: use ringg_call_contact when user asks to call someone. Keep purpose concise and natural (1 short sentence), avoid long scripted intros. After call initiation, use ringg_get_call_report to check outcome and summarize what happened in a natural short spoken sentence."
        })
        
        completion = self.groq_client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=dynamic_messages,
            temperature=config.TEMPERATURE,
            top_p=config.TOP_P,
            stream=config.STREAM,
            tools=tools,
            tool_choice="auto"
        )

        buffer = ''
        full_response = ''
        print('Sellix: ', end='', flush=True)

        tool_calls = []
        is_tool_call = False

        for chunk in completion:
            if interruption_flag.is_set(): break
            # Some streamed chunks can be empty heartbeats / finish markers with no choices.
            if not chunk:
                continue
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue

            first_choice = choices[0] if len(choices) > 0 else None
            if not first_choice:
                continue

            delta_obj = getattr(first_choice, "delta", None)
            if not delta_obj:
                continue

            if delta_obj.tool_calls:
                is_tool_call = True
                for tool_call in delta_obj.tool_calls:
                    if len(tool_calls) <= tool_call.index:
                        tool_calls.append({"id": tool_call.id, "type": "function", "function": {"name": tool_call.function.name, "arguments": ""}})
                    if tool_call.function.arguments:
                        tool_calls[tool_call.index]["function"]["arguments"] += tool_call.function.arguments

            delta = delta_obj.content or ''
            if delta and not is_tool_call:
                print(delta, end='', flush=True)
                buffer += delta
                full_response += delta
                
                # Flush quickly on sentence boundaries, and on long clause tails to reduce perceived latency.
                should_flush = any(buffer.endswith(punct) for punct in ['.', '?', '!', '\n'])
                if not should_flush and len(buffer) >= 120 and any(buffer.rstrip().endswith(p) for p in [',', ';', ':']):
                    should_flush = True

                if should_flush and len(buffer.strip()) > 3:
                    sentence = buffer
                    buffer = ''
                        
                    # Strip accidental conversational prefixes
                    sentence = re.sub(r'^(User|Sellix|Assistant):\s*', '', sentence.strip(), flags=re.IGNORECASE)
                    
                    # Pre-replace common acronyms that get mispronounced
                    sentence = re.sub(r'\bU\.S\.\b', 'United States', sentence)
                    
                    # Remove periods from remaining acronyms (e.g., L.L.P. -> LLP) to prevent TTS stutter/pauses
                    sentence = re.sub(r'\b([A-Z])\.', r'\1', sentence)
                    # Normalize AM/PM only when attached to a time value for natural TTS delivery.
                    sentence = re.sub(r'(?i)\b(\d{1,2}(?::\d{2})?)\s*a\.?\s*m\.?\b', r'\1 in the morning', sentence)
                    sentence = re.sub(r'(?i)\b(\d{1,2}(?::\d{2})?)\s*p\.?\s*m\.?\b', r'\1 in the evening', sentence)
                    # Explicitly replace lone US with United States to stop it saying "us"
                    sentence = re.sub(r'\bUS\b', 'United States', sentence)
                    
                    # Break up acronyms like IPL so the TTS spells them out instead of trying to read them as words
                    sentence = re.sub(r'\bIPL\b', 'I P L', sentence)
                    
                    # Keep commas and apostrophes but strip quotes since they break parsing
                    sentence = re.sub(r'[—–]', ' ', sentence)
                    # Replace $ with the word dollar so the TTS engine correctly speaks prices
                    sentence = re.sub(r'\$', ' dollars ', sentence)
                    # Expand temperature units so TTS says "degrees" instead of just "c" or "f"
                    sentence = re.sub(r'(?i)\b(-?\d+(?:\.\d+)?)\s*°?\s*c\b', r'\1 degrees Celsius', sentence)
                    sentence = re.sub(r'(?i)\b(-?\d+(?:\.\d+)?)\s*°?\s*f\b', r'\1 degrees Fahrenheit', sentence)
                    
                    # Replace smart quotes with straight quotes so words like "Today's" don't get broken apart
                    sentence = re.sub(r'[‘’]', "'", sentence)
                    sentence = re.sub(r'[“”]', '"', sentence)
                    # Remove explicit double quotes entirely as the Model treats them as word bounds, but KEEP apostrophes for contractions
                    sentence = re.sub(r'["]', '', sentence)
                    
                    # Now remove unknown characters except basic punctuation and apostrophes
                    sentence = re.sub(r'[^\w\s.,?!;:\'-]', ' ', sentence)
                    sentence = re.sub(r'\s+', ' ', sentence)
                    
                    if sentence.strip():
                        tts_queue.put(sentence.strip())

        if buffer.strip() and not interruption_flag.is_set() and not is_tool_call:
            sentence = buffer.strip()
            sentence = re.sub(r'^(User|Sellix|Assistant):\s*', '', sentence, flags=re.IGNORECASE)
            
            sentence = re.sub(r'\bU\.S\.\b', 'United States', sentence)
            sentence = re.sub(r'\b([A-Z])\.', r'\1', sentence)
            sentence = re.sub(r'(?i)\b(\d{1,2}(?::\d{2})?)\s*a\.?\s*m\.?\b', r'\1 in the morning', sentence)
            sentence = re.sub(r'(?i)\b(\d{1,2}(?::\d{2})?)\s*p\.?\s*m\.?\b', r'\1 in the evening', sentence)
            sentence = re.sub(r'\bUS\b', 'United States', sentence)
            
            # Break up acronyms like IPL so the TTS spells them out instead of trying to read them as words
            sentence = re.sub(r'\bIPL\b', 'I P L', sentence)
            
            # Same fixes for the final buffer
            sentence = re.sub(r'[—–]', ' ', sentence)
            sentence = re.sub(r'\$', ' dollars ', sentence)
            sentence = re.sub(r'(?i)\b(-?\d+(?:\.\d+)?)\s*°?\s*c\b', r'\1 degrees Celsius', sentence)
            sentence = re.sub(r'(?i)\b(-?\d+(?:\.\d+)?)\s*°?\s*f\b', r'\1 degrees Fahrenheit', sentence)
            
            sentence = re.sub(r'[‘’]', "'", sentence)
            sentence = re.sub(r'[“”]', '"', sentence)
            sentence = re.sub(r'["]', '', sentence)
            
            sentence = re.sub(r'[^\w\s.,?!;:\'-]', ' ', sentence)
            sentence = re.sub(r'\s+', ' ', sentence)
            
            if sentence.strip():
                tts_queue.put(sentence.strip())
            
        print() 
        
        if not interruption_flag.is_set():
            if is_tool_call:
                print("[Running Tools...]")
                
                is_web_search = any(tc["function"]["name"] == "web_search" for tc in tool_calls)
                is_memory = any(tc["function"]["name"] in ["save_memory", "recall_memory"] for tc in tool_calls)
                import random
                if is_web_search:
                    search_query = ""
                    for tc in tool_calls:
                        if tc["function"]["name"] == "web_search":
                            try:
                                search_query = json.loads(tc["function"]["arguments"]).get("query", "")
                                break
                            except:
                                pass
                                
                    if search_query:
                        # Clean up query for speech
                        clean_query = re.sub(r'\bU\.S\.\b', 'United States', search_query)
                        clean_query = re.sub(r'\bUS\b', 'United States', clean_query)
                        clean_query = re.sub(r'\d+', '', clean_query) # Prevent speaking arbitrary numbers like dates appended by the LLM
                        clean_query = re.sub(r'[^\w\s]', '', clean_query)
                        clean_query = re.sub(r'\s+', ' ', clean_query).strip()
                        words = clean_query.split()
                        # Shorten the query context if it's too long so it sounds like a natural phrase
                        if len(words) > 6:
                            clean_query = " ".join(words[:6])
                            
                        fillers = [
                            f"Let me search for {clean_query}.",
                            f"Looking up {clean_query} for you.",
                            f"I'll see what I can find about {clean_query}."
                        ]
                        tts_queue.put(random.choice(fillers))
                    else:
                        fillers = [
                            "Searching the web.",
                            "Looking that up online.",
                            "Pulling that up from the web."
                        ]
                        tts_queue.put(random.choice(fillers))
                    tts_queue.put("__PLAY_TUNE__")
                elif not is_memory:
                    fillers = [
                        "Just a moment.",
                        "Looking into that.",
                        "Working on it."
                    ]
                    tts_queue.put(random.choice(fillers))
                
                self.conversation_history.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls
                })
                
                for tool_call in tool_calls:
                    if tool_call["function"]["name"] == "web_search":
                        try:
                            query = json.loads(tool_call["function"]["arguments"])["query"]
                        except:
                            query = ""
                        
                        import urllib.request
                        api_key = os.environ.get("TAVILY_API_KEY")
                        if not api_key:
                            result_text = "Tavily API key not configured in system variables."
                        else:
                            try:
                                req = urllib.request.Request(
                                    'https://api.tavily.com/search',
                                    data=json.dumps({
                                        "query": query, 
                                        "api_key": api_key, 
                                        "search_depth": "advanced", 
                                        "include_answer": True,
                                        "max_results": 3,
                                        "include_raw_content": False
                                    }).encode(),
                                    headers={'Content-Type': 'application/json'}
                                )
                                with urllib.request.urlopen(req) as response:
                                    search_data = json.loads(response.read().decode())
                                    
                                    if "answer" in search_data and search_data["answer"]:
                                        result_text = search_data["answer"]
                                    else:
                                        snippets = [res["content"] for res in search_data.get("results", [])]
                                        result_text = "\n".join(snippets) if snippets else "No results found."
                            except Exception as e:
                                result_text = f"Web search failed: {e}"
                        
                        self.conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "content": result_text
                        })
                    elif tool_call["function"]["name"] == "list_events":
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                            days_ahead = args.get("days_ahead", 7)
                        except:
                            days_ahead = 7
                        
                        if not self.google_token:
                            result_text = "Error: User has not connected their Google Calendar. Ask them to click Connect Calendar in the UI settings."
                        else:
                            now = datetime.datetime.utcnow()
                            time_min = now.isoformat() + "Z"
                            target_day = now + datetime.timedelta(days=days_ahead)
                            time_max = target_day.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat() + "Z"
                            try:
                                response = requests.get(
                                    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                                    params={
                                        "timeMin": time_min,
                                        "timeMax": time_max,
                                        "singleEvents": "true",
                                        "orderBy": "startTime"
                                    },
                                    headers={"Authorization": f"Bearer {self.google_token}"}
                                )
                                data = response.json()
                                if response.ok:
                                    items = data.get("items", [])
                                    if not items:
                                        result_text = "No upcoming events."
                                    else:
                                        result_text = json.dumps([{"title": t.get("summary", "Untitled"), "time": t["start"].get("dateTime", t["start"].get("date"))} for t in items])
                                else:
                                    result_text = f"API Error: {data}"
                            except Exception as e:
                                result_text = f"Failed to list events: {e}"
                                
                        self.conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "content": result_text
                        })
                    elif tool_call["function"]["name"] == "create_event":
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                        except:
                            args = {}
                        
                        if not self.google_token:
                            result_text = "Error: User has not connected Google Calendar. Ask them to connect in settings."
                        else:
                            event_body = {
                                "summary": args.get("title", "Untitled"),
                                "description": args.get("description", ""),
                                "start": {"dateTime": args.get("start_time")},
                                "end": {"dateTime": args.get("end_time")}
                            }
                            try:
                                response = requests.post(
                                    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                                    headers={
                                        "Authorization": f"Bearer {self.google_token}",
                                        "Content-Type": "application/json"
                                    },
                                    json=event_body
                                )
                                data = response.json()
                                if response.ok:
                                    result_text = f"Successfully created event {data.get('id')}"
                                else:
                                    result_text = f"API Error: {data}"
                            except Exception as e:
                                result_text = f"Failed to create event: {e}"
                                
                        self.conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "content": result_text
                        })
                    elif tool_call["function"]["name"] == "send_whatsapp_message":
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                        except:
                            args = {}

                        default_to = getattr(config, "WHATSAPP_DEFAULT_TO", os.environ.get("WHATSAPP_DEFAULT_TO", "+91 8849835941"))
                        to_raw = str(args.get("to", "") or default_to).strip()
                        message = str(args.get("message", "")).strip()

                        token = getattr(config, "WHATSAPP_ACCESS_TOKEN", os.environ.get("WHATSAPP_ACCESS_TOKEN", ""))
                        phone_number_id = getattr(config, "WHATSAPP_PHONE_NUMBER_ID", os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""))

                        if not token or not phone_number_id:
                            result_text = "WhatsApp API is not configured. Missing WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID."
                        elif not message:
                            result_text = "Message text is required."
                        else:
                            # Normalize into digits for Meta API (no plus sign in request field).
                            normalized = re.sub(r"[^\d+]", "", to_raw)
                            if normalized.startswith("00"):
                                normalized = "+" + normalized[2:]
                            if normalized.startswith("+"):
                                normalized = normalized[1:]

                            # If user gives a local Indian number (10 digits), convert to E.164 digits.
                            if normalized.isdigit() and len(normalized) == 10:
                                normalized = "91" + normalized
                            elif normalized.isdigit() and len(normalized) == 11 and normalized.startswith("0"):
                                normalized = "91" + normalized[1:]

                            # E.164 supports up to 15 digits excluding +
                            if not normalized.isdigit() or len(normalized) < 11 or len(normalized) > 15:
                                result_text = "Invalid recipient number format. Use international format like +919876543210."
                            else:
                                try:
                                    wa_url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
                                    wa_payload = {
                                        "messaging_product": "whatsapp",
                                        "recipient_type": "individual",
                                        "to": normalized,
                                        "type": "text",
                                        "text": {
                                            "preview_url": False,
                                            "body": message[:4096],
                                        },
                                    }
                                    wa_headers = {
                                        "Authorization": f"Bearer {token}",
                                        "Content-Type": "application/json",
                                    }
                                    wa_response = requests.post(wa_url, headers=wa_headers, json=wa_payload, timeout=20)
                                    try:
                                        wa_data = wa_response.json()
                                    except Exception:
                                        wa_data = {"raw": wa_response.text}

                                    if wa_response.ok:
                                        msg_id = None
                                        if isinstance(wa_data, dict):
                                            messages = wa_data.get("messages", [])
                                            if messages and isinstance(messages, list):
                                                msg_id = messages[0].get("id")
                                        if msg_id:
                                            result_text = (
                                                f"WhatsApp API accepted the message and queued it. "
                                                f"Message id: {msg_id}. Note: acceptance does not guarantee delivery. "
                                                "If recipient did not message your business in the last 24 hours, send a template message first."
                                            )
                                        else:
                                            result_text = (
                                                "WhatsApp API accepted the message and queued it. "
                                                "Note: acceptance does not guarantee delivery; a template may be required outside the 24-hour window."
                                            )
                                    else:
                                        # Provide concise, actionable diagnostics for common Graph API failures.
                                        if isinstance(wa_data, dict):
                                            err = wa_data.get("error", {})
                                            code = err.get("code")
                                            subcode = err.get("error_subcode")
                                            message = err.get("message", "Unknown error")

                                            if code == 190 and subcode == 463:
                                                result_text = (
                                                    "WhatsApp auth token expired. Generate a new permanent System User token in Meta Business Manager, "
                                                    "update WHATSAPP_ACCESS_TOKEN, then retry."
                                                )
                                            elif code == 190:
                                                result_text = (
                                                    "WhatsApp auth failed due to invalid token. Verify WHATSAPP_ACCESS_TOKEN and app permissions, then retry."
                                                )
                                            elif code == 131047:
                                                result_text = (
                                                    "Recipient is outside the 24-hour customer service window. Send an approved template message first."
                                                )
                                            elif code == 131026:
                                                result_text = (
                                                    "Message undeliverable for this recipient. Verify the number is on WhatsApp and can receive business messages."
                                                )
                                            else:
                                                result_text = f"WhatsApp API error ({wa_response.status_code}): {message}"
                                        else:
                                            result_text = f"WhatsApp API error ({wa_response.status_code}): {wa_data}"
                                except Exception as e:
                                    result_text = f"Failed to send WhatsApp message: {e}"

                        self.conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "content": result_text
                        })
                    elif tool_call["function"]["name"] == "ringg_call_contact":
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                        except:
                            args = {}

                        name = str(args.get("name", "")).strip()
                        mobile_number = str(args.get("mobile_number", "")).strip()
                        purpose = str(args.get("purpose", "")).strip()
                        if not purpose:
                            purpose = (
                                "Quick personal follow-up on behalf of Abhishek to confirm the preferred next step. "
                                "Keep the call natural, brief, and polite."
                            )

                        if not mobile_number and name:
                            mobile_number = _resolve_known_contact_number(name) or ""

                        if not name or not mobile_number:
                            known = ", ".join(sorted(list((getattr(config, "RINGG_CONTACTS", {}) or {}).keys())))
                            if known:
                                result_text = f"Missing call details. Provide mobile_number, or use one of known contact names: {known}."
                            else:
                                result_text = "Missing call details. Provide both name and mobile_number."
                        else:
                            try:
                                normalized_target = _normalize_phone(mobile_number)
                                call_signature = f"{_normalize_contact_name(name)}|{normalized_target}"
                                now_ts = time.time()
                                if (
                                    self.last_ringg_call_signature == call_signature
                                    and (now_ts - self.last_ringg_call_at) < 75
                                ):
                                    result_text = (
                                        f"I already initiated a recent call to {name}. "
                                        "I am skipping a duplicate call request for now."
                                    )
                                    self.conversation_history.append({
                                        "role": "tool",
                                        "tool_call_id": tool_call["id"],
                                        "name": tool_call["function"]["name"],
                                        "content": result_text
                                    })
                                    continue

                                payload = RinggOutboundCallRequest(
                                    name=name,
                                    mobile_number=mobile_number,
                                    custom_args_values={
                                        "callee_name": name,
                                        "mobile_number": mobile_number,
                                        "purpose": purpose,
                                        "opening_line": (
                                            f"Hi {name}, this is Sellix calling for Abhishek from Tarkshy Consultancy Services. "
                                            "This is a quick check-in."
                                        ),
                                        "source": "voice_command",
                                        "owner_name": getattr(config, "ASSISTANT_OWNER_NAME", "Abhishek"),
                                    },
                                )
                                ringg_body = _ringg_outbound_payload(payload)
                                ringg_result = _ringg_make_request("POST", "/calling/outbound/individual", payload=ringg_body)
                                call_id = _extract_ringg_call_id(ringg_result)
                                self.last_ringg_call_id = call_id
                                self.last_ringg_call_report = ringg_result
                                self.last_ringg_call_signature = call_signature
                                self.last_ringg_call_at = now_ts
                                if call_id:
                                    result_text = f"Call initiated successfully for {name}. Call ID: {call_id}."
                                else:
                                    result_text = f"Call initiated successfully for {name}."
                            except HTTPException as e:
                                result_text = f"Ringg call failed: {e.detail}"
                            except Exception as e:
                                result_text = f"Ringg call failed: {e}"

                        self.conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "content": result_text
                        })
                    elif tool_call["function"]["name"] == "ringg_get_call_report":
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                        except:
                            args = {}

                        call_id = str(args.get("call_id", "")).strip() or self.last_ringg_call_id
                        wait_seconds = args.get("wait_seconds", 0)
                        try:
                            wait_seconds = int(float(wait_seconds))
                        except Exception:
                            wait_seconds = 0
                        wait_seconds = max(0, min(wait_seconds, 120))

                        terminal_states = {"completed", "failed", "error", "cancelled", "canceled"}
                        deadline = time.time() + wait_seconds
                        found_entry = None

                        try:
                            while True:
                                history = _ringg_make_request("GET", "/calling/history")
                                found_entry = _ringg_find_call_entry(history, call_id)
                                status = _ringg_call_status(found_entry) if found_entry else "unknown"
                                if wait_seconds <= 0 or status in terminal_states or time.time() >= deadline:
                                    break
                                time.sleep(5)

                            if not found_entry:
                                result_text = "I could not find a recent call transcript yet. Please ask again shortly."
                            else:
                                final_call_id = _extract_ringg_call_id(found_entry) or call_id or "unknown"
                                transcript_text = _ringg_transcript_text(found_entry)
                                fallback_summary = _ringg_call_summary_text(found_entry)
                                fallback_summary = fallback_summary.strip() if fallback_summary else ""
                                if fallback_summary in ("[]", "{}"):
                                    fallback_summary = ""

                                # Some Ringg fields return transcript logs as raw JSON strings in summary fields.
                                if not transcript_text and fallback_summary and any(k in fallback_summary for k in ['"bot"', '"user"', '"message_id"', '"timestamp"']):
                                    transcript_text = _ringg_transcript_text({"transcript": fallback_summary})

                                if transcript_text:
                                    llm_summary = _summarize_transcript_with_groq(self.groq_client, transcript_text)
                                    if llm_summary:
                                        result_text = llm_summary
                                    else:
                                        result_text = _local_transcript_fallback_summary(transcript_text)
                                elif fallback_summary and not any(k in fallback_summary for k in ['"bot"', '"user"', '"message_id"', '"timestamp"']):
                                    # Only use fallback summary when it is already natural text.
                                    result_text = fallback_summary[:700]
                                else:
                                    result_text = "I found the call, but transcript summary is not available yet. Please ask again in a minute."

                                self.last_ringg_call_id = final_call_id if final_call_id != "unknown" else self.last_ringg_call_id
                                self.last_ringg_call_report = found_entry
                        except HTTPException as e:
                            result_text = "I could not fetch the call transcript right now."
                        except Exception as e:
                            result_text = "I could not summarize the call transcript right now."

                        self.conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "content": result_text
                        })
                    elif tool_call["function"]["name"] == "save_memory":
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                            fact = args.get("fact")
                            if fact:
                                self.memory_upsert_queue.append(fact)
                                result_text = f"Queued memory to be saved: {fact}"
                            else:
                                result_text = "No memory fact provided."
                        except Exception as e:
                            result_text = f"Error queueing memory: {e}"
                            
                        self.conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "content": result_text
                        })
                    elif tool_call["function"]["name"] == "recall_memory":
                        try:
                            args = json.loads(tool_call["function"]["arguments"])
                            query = args.get("query", "").lower()
                            
                            mem_str = None
                            if query in self.memory_cache:
                                mem_str = self.memory_cache[query]
                            else:
                                # Fallback to local array search
                                matches = [m for m in self.cached_user_memories if query in m.lower()]
                                if matches:
                                    mem_str = " | ".join(matches[:3])
                                self.memory_cache[query] = mem_str
                                
                            if mem_str:
                                result_text = "Recalled memories: " + mem_str
                            else:
                                result_text = "No relevant memories found."
                        except Exception as e:
                            result_text = f"Error recalling memory: {e}"
                            
                        self.conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "content": result_text
                        })
                
                # Auto-resume generation after tool use (Agentic recursive call)
                self.generate_response("")
            elif full_response:
                self.conversation_history.append({'role': 'assistant', 'content': full_response})

    def tts_loop(self):
        while True:
            try:
                if interruption_flag.is_set():
                    while not tts_queue.empty(): 
                        try:
                            tts_queue.get_nowait()
                        except queue.Empty:
                            break
                    continue

                text = tts_queue.get(timeout=0.02)
                
                if text == "__PLAY_TUNE__":
                    t = np.linspace(0, 1, config.AUDIO_RATE, False)
                    # Create a simple pleasant, light chime arpeggio (C major high: C6, E6, G6)
                    # Higher pitch and lower amplitude for a "lighter" tone
                    env1 = np.exp(-6 * t) * (t < 0.33)
                    env2 = np.exp(-6 * (t - 0.33)) * ((t >= 0.33) & (t < 0.66))
                    env3 = np.exp(-6 * (t - 0.66)) * (t >= 0.66)
                    
                    part1 = np.sin(1046.50 * 2 * np.pi * t) * env1
                    part2 = np.sin(1318.51 * 2 * np.pi * t) * env2
                    part3 = np.sin(1567.98 * 2 * np.pi * t) * env3
                    
                    tune = (part1 + part2 + part3) * 0.1  # Highly lowered, gentle amplitude

                    for audio_chunk in stream_pcm_chunks(tune):
                        if interruption_flag.is_set():
                            break
                        audio_playback_queue.put(audio_chunk)
                    continue

                if getattr(config, "SELECTED_MODEL", "prisik-04-2026") == "prisik-02-2025":
                    # --- NEW: ELEVENLABS PIPELINE FOR OTHER MODEL ---
                    el_api_key = getattr(config, "ELEVENLABS_API_KEY", os.environ.get("ELEVENLABS_API_KEY", ""))
                    if not el_api_key:
                        print("[TTS Warning] ELEVENLABS_API_KEY not set in environment or config. Cannot use ElevenLabs TTS.")
                    else:
                        # You can customize your favorite ElevenLabs voice ID here (defaulted to a generic one)
                        eleven_voice_id = getattr(config, "ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJcg") 
                        url = f"https://api.elevenlabs.io/v1/text-to-speech/{eleven_voice_id}/stream?output_format=pcm_24000"
                        headers = {
                            "xi-api-key": el_api_key,
                            "Content-Type": "application/json"
                        }
                        payload = {
                            "text": text,
                            "model_id": "eleven_turbo_v2" # the fastest model for realtime streams
                        }
                        try:
                            import requests
                            response = requests.post(url, json=payload, headers=headers, stream=True)
                            if response.status_code != 200:
                                error_detail = response.text
                                print(f"[ElevenLabs Error] {response.status_code} - {error_detail}")
                            response.raise_for_status()
                            # Since we request pcm_24000, it returns raw int16 frames natively. 
                            # We chunk them out so the frontend WebAudio can play them smoothly.
                            buffer = b""
                            for chunk in response.iter_content(chunk_size=4096):
                                if interruption_flag.is_set():
                                    break
                                if chunk:
                                    buffer += chunk
                                    # Ensure we always yield even byte counts (16-bit alignment)
                                    while len(buffer) >= 8192:
                                        audio_playback_queue.put(buffer[:8192])
                                        buffer = buffer[8192:]
                                        
                            if len(buffer) >= 2:
                                remainder = len(buffer) - (len(buffer) % 2)
                                audio_playback_queue.put(buffer[:remainder])
                                
                            continue # Skip the KittenTTS step
                        except Exception as e:
                            print(f"[ElevenLabs Error] {e}")
                    # ------------------------------------------------

                if self.tts_client:
                    audio = self.tts_client.generate(
                        text=text, 
                        voice=config.KITTEN_VOICE,
                        speed=config.KITTEN_SPEED,
                        clean_text=True
                    )
                    for audio_chunk in stream_pcm_chunks(audio):
                        if interruption_flag.is_set(): break
                        audio_playback_queue.put(audio_chunk)
                else:
                    print(f'[TTS Warning] No TTS client available, skipping audio for: {text}')

            except queue.Empty:
                continue
            except Exception as e:
                print(f'[TTS Error] {e}')

global_agent = None

@app.on_event('startup')
async def startup_event():
    global global_agent
    if not config.GROQ_API_KEY:
        print('ERROR: GROQ_API_KEY environment variable not set.')
        sys.exit(1)
    global_agent = VoiceAgent()

def is_silent(data, threshold=config.SILENCE_THRESHOLD):
    if len(data) == 0: return True
    rms = np.sqrt(np.mean(np.square(data.astype(np.float32))))
    return rms < threshold


@app.get('/api/ringg/workspace')
def ringg_workspace_info():
    """Quick auth check against Ringg API using configured X-API-KEY."""
    data = _ringg_make_request("GET", "/workspace")
    return {"ok": True, "workspace": data}


@app.get('/api/ringg/call-history')
def ringg_call_history():
    data = _ringg_make_request("GET", "/calling/history")
    return {"ok": True, "history": data}


@app.get('/api/ringg/live-transcript-summary')
def ringg_live_transcript_summary(call_id: Optional[str] = None):
    """
    Returns a text-only real-time view for web UI:
    - call status indicator
    - transcript-aware live summary (when available)
    """
    history = _ringg_make_request("GET", "/calling/history")
    active_call_id = call_id or (global_agent.last_ringg_call_id if global_agent else None)
    entry = _ringg_find_call_entry(history, active_call_id)

    if not entry:
        return {
            "ok": True,
            "has_call": False,
            "status": "none",
            "is_live": False,
            "summary": "No recent call found yet.",
        }

    final_call_id = _extract_ringg_call_id(entry) or active_call_id or "unknown"
    status = _ringg_call_status(entry)
    is_live = _ringg_is_live_status(status)
    transcript_text = _ringg_transcript_text(entry)
    fallback_summary = _ringg_call_summary_text(entry)
    if fallback_summary in ("[]", "{}"):
        fallback_summary = ""

    if not transcript_text and fallback_summary and any(k in fallback_summary for k in ['"bot"', '"user"', '"message_id"', '"timestamp"']):
        transcript_text = _ringg_transcript_text({"transcript": fallback_summary})

    summary = ""
    transcript_available = bool(transcript_text.strip())

    if transcript_available and global_agent:
        transcript_hash = hashlib.sha1(transcript_text.encode("utf-8")).hexdigest()
        cache_entry = global_agent.live_ringg_summary_cache.get(final_call_id)
        if cache_entry and cache_entry.get("transcript_hash") == transcript_hash and cache_entry.get("summary"):
            summary = str(cache_entry["summary"])
        else:
            summary = _summarize_transcript_with_groq(global_agent.groq_client, transcript_text)
            if not summary:
                summary = _local_transcript_fallback_summary(transcript_text)

            if summary:
                global_agent.live_ringg_summary_cache[final_call_id] = {
                    "transcript_hash": transcript_hash,
                    "summary": summary,
                    "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
                }

    if not summary:
        if fallback_summary and not any(k in fallback_summary for k in ['"bot"', '"user"', '"message_id"', '"timestamp"']):
            summary = str(fallback_summary).strip()[:700]
        elif is_live:
            summary = "Call is in progress. Waiting for transcript content to generate a live summary."
        else:
            summary = "Call is finished, but transcript summary is not available yet."

    return {
        "ok": True,
        "has_call": True,
        "call_id": final_call_id,
        "status": status,
        "is_live": is_live,
        "transcript_available": transcript_available,
        "summary": summary,
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
    }


@app.post('/api/ringg/call')
def ringg_make_outbound_call(payload: RinggOutboundCallRequest):
    if not payload.mobile_number.strip():
        raise HTTPException(status_code=400, detail="mobile_number is required")

    body = _ringg_outbound_payload(payload)
    data = _ringg_make_request("POST", "/calling/outbound/individual", payload=body)
    return {"ok": True, "ringg_response": data}


@app.post('/api/ringg/missed-call-fallback')
def ringg_missed_call_fallback(
    payload: RinggMissedCallFallbackRequest,
    x_fallback_secret: Optional[str] = Header(default=None, alias="X-FALLBACK-SECRET"),
):
    """
    Trigger an AI callback when your personal call is missed.
    Hook this endpoint to your telephony webhook when call_status=no_answer/missed.
    """
    return _process_missed_call_fallback(payload, x_fallback_secret)


@app.post('/api/webhooks/twilio/missed-call')
async def twilio_missed_call_webhook(
    request: Request,
    x_fallback_secret: Optional[str] = Header(default=None, alias="X-FALLBACK-SECRET"),
):
    raw = await _parse_webhook_payload(request)
    payload = _build_fallback_from_provider("twilio", raw)
    result = _process_missed_call_fallback(payload, x_fallback_secret)
    return {
        "ok": True,
        "provider": "twilio",
        "mapped": {
            "caller_number": payload.caller_number,
            "to_number": payload.to_number,
            "call_status": payload.call_status,
        },
        **result,
    }


@app.post('/api/webhooks/exotel/missed-call')
async def exotel_missed_call_webhook(
    request: Request,
    x_fallback_secret: Optional[str] = Header(default=None, alias="X-FALLBACK-SECRET"),
):
    raw = await _parse_webhook_payload(request)
    payload = _build_fallback_from_provider("exotel", raw)
    result = _process_missed_call_fallback(payload, x_fallback_secret)
    return {
        "ok": True,
        "provider": "exotel",
        "mapped": {
            "caller_number": payload.caller_number,
            "to_number": payload.to_number,
            "call_status": payload.call_status,
        },
        **result,
    }

@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print('[WebSocket] Client connected')

    frames = []
    pre_speech_frames = []
    is_speaking = False
    silent_chunks = 0
    active_chunks = 0
    # Browser usually sends ~100ms Float32 chunks.
    # Set VAD patience appropriately based on 1024 sample chunks at 16000Hz (64ms per chunk)
    patience = int(config.SILENCE_DURATION / 0.064) 
    activation_threshold = 2  # Faster pickup for softer voices while still filtering random pops.

    async def playback_sender():
        interruption_notified = False

        while True:
            if interruption_flag.is_set():
                while not audio_playback_queue.empty():
                    try:
                        audio_playback_queue.get_nowait()
                    except queue.Empty:
                        break
                if not interruption_notified:
                    await websocket.send_text('interrupt')
                    interruption_notified = True
                await asyncio.sleep(0.01)
                continue

            interruption_notified = False
            
            try:
                chunk = await asyncio.to_thread(audio_playback_queue.get, True, 0.02)
                await websocket.send_bytes(chunk)
            except queue.Empty:
                continue
            except Exception as e:
                print(f'[Sender Error] {e}')
                break

    sender_task = asyncio.create_task(playback_sender())

    try:
        while True:
            data = await websocket.receive()
            
            if "text" in data:
                try:
                    payload = json.loads(data["text"])
                    if payload.get("type") == "config":
                        if payload.get("voice"):
                            config.KITTEN_VOICE = payload["voice"]
                            print(f'[System] Voice changed to: {config.KITTEN_VOICE}')
                        if payload.get("model"):
                            config.SELECTED_MODEL = payload["model"]
                            print(f'[System] Model changed to: {config.SELECTED_MODEL}')
                        if payload.get("google_token"):
                            global_agent.google_token = payload["google_token"]
                            print(f'[System] Received Google Token')
                        if payload.get("location"):
                            loc = payload["location"]
                            print(f'[System] Received Location: {loc}')
                            # Inject location into system prompt
                            if global_agent and global_agent.conversation_history:
                                for msg in global_agent.conversation_history:
                                    if msg.get("role") == "system":
                                        import re
                                        base_content = re.sub(r'\n\n--- USER LOCATION ---.*', '', msg["content"], flags=re.DOTALL)
                                        msg["content"] = base_content + f"\n\n--- USER LOCATION ---\nThe user's current city/location is: {loc}. IMPORTANT: Assume this location for 'weather here', nearest places, or local queries. ALWAYS explicitly state which city or area you are referring to when answering location-based questions."
                                        break
                    elif payload.get("type") == "interrupt":
                        print('[WebSocket] Manual interrupt from frontend UI.')
                        interruption_flag.set()
                        is_speaking = False
                        frames.clear()
                        pre_speech_frames.clear()
                        active_chunks = 0
                        silent_chunks = 0
                        await websocket.send_text('interrupt')
                except Exception as e:
                    print(f"[WebSocket Config Error] {e}")
                continue

            if "bytes" not in data:
                continue

            audio_np = np.frombuffer(data["bytes"], dtype=np.float32)
            
            if len(audio_np) < 512:
                continue

            int16_scaled = (audio_np * 32767).astype(np.int16)
            silent = is_silent(int16_scaled)
            
            # Keep debug logging sparse to reduce console overhead on tight loops
            if np.random.random() < 0.005:
                rms_val = np.sqrt(np.mean(np.square(int16_scaled.astype(np.float32))))
                print(f"[Debug] RMS: {rms_val:.2f} | Silent: {silent} | Speaking: {is_speaking}")
            
            if not silent:
                active_chunks += 1
                silent_chunks = 0
                if active_chunks >= activation_threshold and not is_speaking:
                    if not interruption_flag.is_set():
                        print('[WebSocket] Interrupted by user.')
                        interruption_flag.set()
                        await websocket.send_text('interrupt')
                    is_speaking = True
                    frames = list(pre_speech_frames)
                
                if is_speaking:
                    frames.append(data["bytes"])
                    pre_speech_frames.clear()
                else:
                    pre_speech_frames.append(data["bytes"])
                    # Limit to 20 frames (~1.2s) to prevent catching the agent's echoed voice before you interrupt
                    if len(pre_speech_frames) > 20:
                        pre_speech_frames.pop(0)
                        
            elif is_speaking:
                active_chunks = 0
                silent_chunks += 1
                frames.append(data["bytes"])
                
                # --- Speculative Pre-Generation Trigger ---
                if len(frames) % 15 == 0 and len(frames) > 0:
                    partial_audio = b''.join(frames)
                    threading.Thread(
                        target=global_agent.run_partial_stt_and_speculate,
                        args=(partial_audio,),
                        daemon=True
                    ).start()
                # ------------------------------------------

                if silent_chunks > patience:
                    is_speaking = False
                    interruption_flag.clear()
                    active_chunks = 0
                    
                    audio_data = b''.join(frames)
                    threading.Thread(target=global_agent.transcribe, args=(audio_data,), daemon=True).start()
                    frames = []
            else:
                active_chunks = 0
                pre_speech_frames.append(data["bytes"])
                if len(pre_speech_frames) > 20:
                    pre_speech_frames.pop(0)
    except WebSocketDisconnect:
        print('[WebSocket] Client disconnected')
    finally:
        sender_task.cancel()

if __name__ == '__main__':
    uvicorn.run('main:app', host='0.0.0.0', port=8000, reload=False)
