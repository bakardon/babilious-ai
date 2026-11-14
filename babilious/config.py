"""Central configuration.

Everything is local. Values come from (in order of priority):
1. Real environment variables
2. A ``.env`` file in the project root (optional, see ``.env.example``)
3. The defaults below

User-adjustable runtime settings (active model, temperature, ...) live in the
SQLite ``settings`` table instead, so they can be changed from the UI.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
LEGACY_MEMORY_FILE = BASE_DIR / "memory" / "adventure_log.json"
LEGACY_MISSIONS_FILE = BASE_DIR / "state" / "missions.json"
LEGACY_STATE_FILE = BASE_DIR / "state" / "user_state.json"
PERSONALITY_FILE = BASE_DIR / "personality.md"
PERSONALITY_NOTES_FILE = KNOWLEDGE_DIR / "personality_notes.md"


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines). Real env vars always win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


HOST = _env("BABILIOUS_HOST", "127.0.0.1")
PORT = int(_env("BABILIOUS_PORT", "5000"))
DB_PATH = Path(_env("BABILIOUS_DB", str(DATA_DIR / "babilious.db")))
OLLAMA_HOST = _env("OLLAMA_HOST_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = _env("BABILIOUS_DEFAULT_MODEL", "llama3.2:latest")
LOG_LEVEL = _env("BABILIOUS_LOG_LEVEL", "INFO")

# Defaults for settings stored in SQLite (editable in the UI).
DEFAULT_SETTINGS = {
    "active_model": DEFAULT_MODEL,
    "temperature": 0.7,
    # Small context keeps RAM and prompt-processing time low on CPU.
    "num_ctx": 4096,
    # How long Ollama keeps the model in RAM after the last message.
    "keep_alive": "10m",
    # How many previous messages are sent with each request.
    "history_messages": 8,
    "max_reply_tokens": 400,
    "user_name": "Abubakar",
    # Second brain
    "use_documents_in_chat": True,
    "semantic_search": False,          # needs an embedding model in Ollama
    "embed_model": "nomic-embed-text",
    # Voice (Milestone 3)
    "stt_model": "base.en",
    "tts_voice": "en_US-ryan-medium",
    "speech_rate": 1.0,
    "spoken_replies": "piper",      # piper | browser | off
    "wake_word": "Babilious",
    "wake_engine": "whisper",       # whisper (any word) | openwakeword (pretrained phrases)
    "oww_model": "hey_jarvis",
    "oww_threshold": 0.5,
    "barge_in": "voice",            # voice (talk over him) | wake (say his name) | off
    "vad_sensitivity": 11.0,        # dB above room noise that counts as speech
    "follow_up_seconds": 6,         # listen without the wake word right after he answers
    # Personal manager
    "desktop_notifications": True,
    "briefing_time": "",
    # Integrations (Milestone 6), all optional
    "spotify_client_id": "",        # your own free app at developer.spotify.com (PKCE, no secret)
    "music_pause_for_voice": True,  # pause music while you talk to him, resume after
    "calendar_ics_url": "",         # Google/Outlook calendar "secret iCal address", read-only
    "vision_model": "",             # e.g. moondream; empty = off (OCR only)
    "research_depth": "normal",     # quick (3 sources) | normal (5) | deep (7)
}


def get_secret_key() -> str:
    """Return a stable secret key, generating one on first run."""
    env_key = os.environ.get("BABILIOUS_SECRET_KEY")
    if env_key:
        return env_key
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file = DATA_DIR / ".secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    return key
