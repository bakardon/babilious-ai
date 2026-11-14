"""Local voice: faster-whisper (speech-to-text), Piper (text-to-speech), wake words.

Everything runs on this PC. Audio arrives from the browser over a localhost
WebSocket, lives only in RAM while it is processed, and is never written to disk.
"""
from __future__ import annotations

import importlib.util

from .. import config

MODELS_DIR = config.DATA_DIR / "models"
WHISPER_DIR = MODELS_DIR / "whisper"
PIPER_DIR = MODELS_DIR / "piper"
OWW_DIR = MODELS_DIR / "openwakeword"
SAMPLE_RATE = 16_000


def has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def available() -> dict[str, bool]:
    return {
        "stt": has("faster_whisper"),
        "tts": has("piper"),
        "openwakeword": has("openwakeword"),
        "websocket": has("flask_sock"),
    }
