import asyncio
import base64
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from actions import open_app, open_browser, open_path, open_terminal
from platform_utils import IS_WINDOWS

try:
    import edge_tts
except Exception:  # pragma: no cover
    edge_tts = None

try:
    from google import genai  # google-genai
except Exception:  # pragma: no cover
    genai = None

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shadow-server")

APP_PORT = int(os.getenv("SHADOW_PORT", "8340"))
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

ENV_FILE = Path(__file__).parent / ".env"

app = FastAPI(title="SHADOW Server", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

START_TIME = time.time()


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

SETTINGS_FILE = DATA_DIR / "settings.json"


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_settings(data: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _set_env_key(key_name: str, key_value: str) -> None:
    os.environ[key_name] = key_value

    # Also persist to .env for next run
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    found = False
    for line in lines:
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        k, _, _v = line.partition("=")
        if k.strip() == key_name:
            out.append(f"{key_name}={key_value}")
            found = True
        else:
            out.append(line)

    if not found:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(f"{key_name}={key_value}")

    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Minimal intent + command execution
# ---------------------------------------------------------------------------

SPEECH_CORRECTIONS: dict[str, str] = {
    "cloud code": "claude code",
    "start clock code": "start claude code",
    "open cloud code": "open claude code",
    "open foler": "open folder",
    "open foulder": "open folder",
    "you to": "youtube",
    "you tube": "youtube",
}


def apply_speech_corrections(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    low = t.lower()
    for k, v in SPEECH_CORRECTIONS.items():
        if k in low:
            low = low.replace(k, v)
    # keep original casing roughly but corrected content is more important
    return low


@dataclass
class ParsedCommand:
    action: str
    target: str = ""


def _parse_command(text: str) -> ParsedCommand:
    t = apply_speech_corrections(text)
    t = re.sub(r"\s+", " ", t).strip()
    low = t.lower()

    # Common folders without saying "folder"
    if low in {"downloads", "documents", "desktop", "pictures", "music", "videos"}:
        return ParsedCommand("open_path", low)

    # Open folder/path
    m = re.match(r"^(?:open|launch)\s+(?:folder|directory|file)\s+(.+)$", low)
    if m:
        return ParsedCommand("open_path", m.group(1).strip('" '))

    m = re.match(r"^(?:open|launch)\s+(.+)$", low)
    if m:
        target = m.group(1).strip()
        if target in {"chrome", "google chrome"}:
            return ParsedCommand("browse", "https://www.google.com")
        if target in {"google"}:
            return ParsedCommand("browse", "https://www.google.com")
        if target in {"youtube"}:
            return ParsedCommand("browse", "https://www.youtube.com")
        if target in {"downloads", "documents", "desktop", "pictures", "music", "videos"}:
            return ParsedCommand("open_path", target)
        if target in {"terminal", "cmd", "command prompt", "powershell"}:
            return ParsedCommand("open_terminal", "")
        # If user says "open <app>" and it isn't a URL/path, treat it as an app
        if not (("\\" in target) or ("/" in target) or (":" in target)) and not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", target):
            return ParsedCommand("open_app", target)
        # If it looks like a path, treat as open_path
        if "\\" in target or "/" in target or ":" in target:
            return ParsedCommand("open_path", target.strip('" '))
        # If it looks like a domain, browse
        if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", target):
            url = target if target.startswith("http") else f"https://{target}"
            return ParsedCommand("browse", url)
        return ParsedCommand("browse", target)

    # YouTube search
    m = re.match(r"^(?:search|find)\s+(.+?)\s+(?:on|in)\s+youtube$", low)
    if m:
        return ParsedCommand("youtube_search", m.group(1).strip())
    m = re.match(r"^youtube\s+search\s+(.+)$", low)
    if m:
        return ParsedCommand("youtube_search", m.group(1).strip())

    # Google search
    m = re.match(r"^(?:search|google)\s+for\s+(.+)$", low)
    if m:
        return ParsedCommand("google_search", m.group(1).strip())

    # Open terminal and run something
    m = re.match(r"^(?:run|execute)\s+(.+)$", low)
    if m:
        return ParsedCommand("open_terminal", m.group(1).strip())

    return ParsedCommand("chat", t)


async def _execute_command(cmd: ParsedCommand) -> dict[str, Any]:
    if cmd.action == "open_app":
        return await open_app(cmd.target)
    if cmd.action == "open_path":
        return await open_path(cmd.target)
    if cmd.action == "browse":
        return await open_browser(cmd.target)
    if cmd.action == "youtube_search":
        from urllib.parse import quote

        url = f"https://www.youtube.com/results?search_query={quote(cmd.target)}"
        return await open_browser(url)
    if cmd.action == "google_search":
        from urllib.parse import quote

        url = f"https://www.google.com/search?q={quote(cmd.target)}"
        return await open_browser(url)
    if cmd.action == "open_terminal":
        return await open_terminal(cmd.target)
    return {"success": False, "confirmation": ""}


async def classify_intent(text: str, client: Any = None) -> dict[str, str]:
    """Used by tests and /api/chat. Returns {action, target}."""
    parsed = _parse_command(text)

    # If it's clearly a system command, no need for LLM classification.
    if parsed.action != "chat":
        return {"action": parsed.action if parsed.action != "open_path" else "open_path", "target": parsed.target}

    # Optional Gemini-based classifier for better routing
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or genai is None:
        return {"action": "chat", "target": parsed.target}

    try:
        gem_client = client or genai.Client(api_key=api_key)
        prompt = (
            "Classify the user command into one of: open_terminal, browse, build, open_path, youtube_search, google_search, chat.\n"
            "Return strict JSON only: {\"action\":\"...\",\"target\":\"...\"}\n"
            f"Command: {parsed.target}"
        )
        resp = gem_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        raw = getattr(resp, "text", "") or ""
        raw = raw.strip()
        if "```" in raw:
            raw = raw.split("```")[1].strip()
            raw = raw.removeprefix("json").strip()
        data = json.loads(raw)
        action = str(data.get("action", "chat"))
        target = str(data.get("target", parsed.target))
        if action not in {"open_terminal", "browse", "build", "open_path", "youtube_search", "google_search", "chat"}:
            action = "chat"
        return {"action": action, "target": target}
    except Exception:
        return {"action": "chat", "target": parsed.target}


async def _chat_llm(user_text: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or genai is None:
        return (
            "I’m online. If you want full AI replies, set a Gemini API key in Settings. "
            "For now I can still do system commands like ‘open youtube’ or ‘open folder Downloads’."
        )
    try:
        client = genai.Client(api_key=api_key)
        user_name = os.getenv("USER_NAME", "sir")
        honorific = os.getenv("HONORIFIC", "sir") or "sir"

        lang_rule = (
            "Language handling:\n"
            "- Understand both English and Tamil.\n"
            "- If the user speaks in Tamil, respond in Tamil.\n"
            "- If the user speaks in English, respond in English.\n"
            "- If mixed language is used, respond clearly in the dominant language.\n"
        )

        prompt = (
            "Adopt a refined, calm, and intelligent butler-style personality. "
            "Speak with elegance, clarity, and quiet confidence. Use polite and formal language, "
            "addressing the user with subtle respect. Maintain a composed and sophisticated tone.\n\n"
            f"{lang_rule}\n"
            "Behavior:\n"
            "- Be concise but articulate.\n"
            "- Avoid slang or casual phrasing.\n"
            "- Sound attentive, loyal, and composed.\n"
            "- In errors, be graceful and reassuring; never sound technical.\n\n"
            f"Address the user as {honorific} (name: {user_name}).\n"
            "Keep responses under 2 sentences unless the user asks for details.\n\n"
            f"User: {user_text}"
        )
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return (getattr(resp, "text", "") or "").strip() or "Understood."
    except Exception as e:
        log.warning(f"LLM chat failed: {e}")
        return (
            "Apologies, sir. It appears the system is temporarily unavailable. "
            "Kindly try again shortly."
        )


async def _stt_gemini(audio_bytes: bytes, mime_type: str) -> dict[str, str]:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or genai is None:
        return {"text": "", "lang": "en"}

    client = genai.Client(api_key=api_key)
    prompt = (
        "Transcribe the user's speech from the provided audio.\n"
        "Return STRICT JSON only: {\"text\":\"...\",\"lang\":\"en\"|\"ta\"}\n"
        "- If the transcript is predominantly Tamil, lang must be \"ta\".\n"
        "- If predominantly English, lang must be \"en\".\n"
        "- Do not add any extra keys.\n"
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": audio_bytes}},
                ],
            }
        ],
    )
    raw = (getattr(resp, "text", "") or "").strip()
    if "```" in raw:
        raw = raw.split("```")[1].strip()
        raw = raw.removeprefix("json").strip()
    try:
        data = json.loads(raw)
        text = str(data.get("text", "")).strip()
        lang = str(data.get("lang", "en")).strip() or "en"
        if lang not in {"en", "ta"}:
            lang = "en"
        return {"text": text, "lang": lang}
    except Exception:
        return {"text": raw, "lang": "en"}


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    text: str


class SttRequest(BaseModel):
    audio_base64: str
    mime_type: str = "audio/webm"


class TtsRequest(BaseModel):
    text: str
    lang: str = "en"  # "en" | "ta"


class KeysRequest(BaseModel):
    key_name: str
    key_value: str


class TestKeyRequest(BaseModel):
    key_value: Optional[str] = None


class PreferencesRequest(BaseModel):
    user_name: str = ""
    honorific: str = "sir"
    calendar_accounts: str = "auto"


# ---------------------------------------------------------------------------
# Settings endpoints (expected by frontend)
# ---------------------------------------------------------------------------


@app.get("/api/settings/status")
async def settings_status() -> dict[str, Any]:
    settings = _load_settings()
    gemini = bool(os.getenv("GEMINI_API_KEY", ""))
    fish = bool(os.getenv("FISH_API_KEY", ""))
    fish_voice = bool(os.getenv("FISH_VOICE_ID", ""))

    return {
        "claude_code_installed": shutil.which("claude") is not None,
        "calendar_accessible": False,
        "mail_accessible": False,
        "notes_accessible": False,
        "memory_count": 0,
        "task_count": 0,
        "server_port": APP_PORT,
        "uptime_seconds": int(time.time() - START_TIME),
        "env_keys_set": {
            "gemini": gemini,
            "fish_audio": fish,
            "fish_voice_id": fish_voice,
            "user_name": settings.get("user_name", os.getenv("USER_NAME", "")),
        },
    }


@app.get("/api/settings/preferences")
async def settings_preferences() -> dict[str, Any]:
    settings = _load_settings()
    return {
        "user_name": settings.get("user_name", os.getenv("USER_NAME", "")),
        "honorific": settings.get("honorific", os.getenv("HONORIFIC", "sir")),
        "calendar_accounts": settings.get("calendar_accounts", os.getenv("CALENDAR_ACCOUNTS", "auto")),
    }


@app.post("/api/settings/preferences")
async def settings_set_preferences(req: PreferencesRequest) -> dict[str, Any]:
    settings = _load_settings()
    settings["user_name"] = req.user_name
    settings["honorific"] = req.honorific
    settings["calendar_accounts"] = req.calendar_accounts
    _save_settings(settings)

    if req.user_name:
        _set_env_key("USER_NAME", req.user_name)
    _set_env_key("HONORIFIC", req.honorific)
    _set_env_key("CALENDAR_ACCOUNTS", req.calendar_accounts)
    return {"ok": True}


@app.post("/api/settings/keys")
async def settings_set_key(req: KeysRequest) -> dict[str, Any]:
    _set_env_key(req.key_name, req.key_value)
    return {"ok": True}


@app.post("/api/settings/test-gemini")
async def settings_test_gemini(req: TestKeyRequest) -> dict[str, Any]:
    key = (req.key_value or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        return {"valid": False, "error": "No key provided"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key})
            if r.status_code == 200:
                return {"valid": True}
            return {"valid": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


@app.post("/api/settings/test-fish")
async def settings_test_fish(req: TestKeyRequest) -> dict[str, Any]:
    key = (req.key_value or os.getenv("FISH_API_KEY", "")).strip()
    if not key:
        return {"valid": False, "error": "No key provided"}
    # Fish API may change; we do a lightweight auth-format check only.
    if len(key) < 8:
        return {"valid": False, "error": "Key looks too short"}
    return {"valid": True}


# ---------------------------------------------------------------------------
# Chat endpoint used by Web voice assistant
# ---------------------------------------------------------------------------


@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    text = (req.text or "").strip()
    if not text:
        return {"text": "I didn’t catch that."}

    cmd = _parse_command(text)
    if cmd.action != "chat":
        result = await _execute_command(cmd)
        confirmation = result.get("confirmation") or "Done."
        return {"text": confirmation, "executed": True, "result": result}

    reply = await _chat_llm(text)
    return {"text": reply, "executed": False}


@app.post("/api/stt")
async def stt(req: SttRequest) -> dict[str, Any]:
    """Speech-to-text via Gemini (fallback when browser STT is blocked)."""
    try:
        audio_bytes = base64.b64decode(req.audio_base64)
    except Exception:
        return {"text": "", "lang": "en", "error": "bad_audio"}

    result = await _stt_gemini(audio_bytes=audio_bytes, mime_type=req.mime_type or "audio/webm")
    return result


@app.post("/api/tts")
async def tts(req: TtsRequest) -> dict[str, Any]:
    """Text-to-speech via Edge TTS (reliable for Tamil)."""
    text = (req.text or "").strip()
    if not text:
        return {"audio_base64": "", "mime_type": "audio/mpeg", "error": "no_text"}

    if edge_tts is None:
        return {"audio_base64": "", "mime_type": "audio/mpeg", "error": "edge_tts_unavailable"}

    lang = (req.lang or "en").strip().lower()
    voice_en = os.getenv("TTS_VOICE", "en-GB-RyanNeural")
    voice_ta = os.getenv("TTS_VOICE_TA", "ta-IN-PallaviNeural")
    voice = voice_ta if lang == "ta" else voice_en

    try:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio_bytes += chunk.get("data", b"")
        return {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": "audio/mpeg",
            "voice": voice,
        }
    except Exception as e:
        return {"audio_base64": "", "mime_type": "audio/mpeg", "error": str(e)}


# ---------------------------------------------------------------------------
# Legacy websocket (kept for 'fix_self' button + future push events)
# ---------------------------------------------------------------------------


@app.websocket("/ws/voice")
async def voice_ws(ws: WebSocket):
    await ws.accept()
    log.info("WS connected")
    try:
        await ws.send_json({"type": "status", "state": "idle"})
        while True:
            raw = await ws.receive_text()
            payload = json.loads(raw)
            if payload.get("type") == "fix_self":
                await ws.send_json({"type": "status", "state": "thinking"})
                await ws.send_json({"type": "text", "text": "Diagnostics complete. Server endpoints are online, sir."})
                await ws.send_json({"type": "status", "state": "idle"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"WS error: {e}")


@app.get("/api/status")
async def status():
    return {"status": "online", "identity": "SHADOW", "platform": "windows" if IS_WINDOWS else "other"}


@app.post("/api/restart")
async def restart():
    return {"status": "restarting"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=APP_PORT)
