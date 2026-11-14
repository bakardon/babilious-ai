"""Text-to-speech with Piper (ONNX, runs faster than real time on CPU)."""
from __future__ import annotations

import io
import logging
import re
import threading
import time
import wave
from typing import Any

from . import PIPER_DIR

log = logging.getLogger(__name__)

VOICES = [
    {"name": "en_US-ryan-medium", "label": "Ryan · US · deep male (default)", "download_mb": 63},
    {"name": "en_US-joe-medium", "label": "Joe · US · male", "download_mb": 63},
    {"name": "en_GB-alan-medium", "label": "Alan · British · male (very JARVIS)", "download_mb": 63},
    {"name": "en_GB-northern_english_male-medium", "label": "Northern English · male", "download_mb": 63},
    {"name": "en_US-lessac-medium", "label": "Lessac · US · female", "download_mb": 63},
    {"name": "en_US-ryan-low", "label": "Ryan · US · low quality, fastest", "download_mb": 63},
]


def installed_voices() -> list[str]:
    if not PIPER_DIR.exists():
        return []
    return sorted(p.stem for p in PIPER_DIR.glob("*.onnx") if (p.with_suffix(".onnx.json")).exists())


def clean_for_speech(text: str) -> str:
    """Make chat Markdown sound natural when read aloud."""
    text = re.sub(r"```.*?```", " I've put the code on screen. ", text, flags=re.S)
    text = re.sub(r"`([^`]{1,40})`", r"\1", text)
    text = re.sub(r"`[^`]+`", " that ", text)
    text = re.sub(r"\s*\[\d+\]", "", text)  # research citations
    text = re.sub(r"https?://\S+", " the link on screen ", text)
    text = re.sub(r"[A-Za-z]:\\[^\s]+", " the path on screen ", text)
    text = re.sub(r"\*\*|__|[*_#>~|]", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)  # emoji
    return re.sub(r"\s+", " ", text).strip()


class TextToSpeech:
    def __init__(self, voice: str = "en_US-ryan-medium"):
        self.voice_name = voice
        self._voice = None
        self._loaded_name = ""
        self._lock = threading.Lock()
        self.last_used = 0.0
        self.downloads: dict[str, str] = {}  # name -> "downloading" | "done" | error text

    @property
    def loaded(self) -> bool:
        return self._voice is not None

    def set_voice(self, name: str) -> None:
        self.voice_name = name

    def _ensure(self, name: str | None = None):
        name = name or self.voice_name
        if self._voice is not None and self._loaded_name == name:
            return self._voice
        path = PIPER_DIR / f"{name}.onnx"
        if not path.exists():
            raise FileNotFoundError(f"Voice '{name}' isn't downloaded yet (Settings > Voice).")
        from piper import PiperVoice
        started = time.perf_counter()
        self._voice = PiperVoice.load(str(path))  # one voice in RAM at a time
        self._loaded_name = name
        log.info("Loaded Piper voice %s in %.1fs", name, time.perf_counter() - started)
        return self._voice

    def unload(self) -> None:
        with self._lock:
            self._voice = None
            self._loaded_name = ""

    def synthesize(self, text: str, rate: float = 1.0, voice_name: str | None = None) -> tuple[bytes, dict[str, Any]]:
        """Return (wav bytes, stats)."""
        from piper import SynthesisConfig
        text = clean_for_speech(text)
        if not text:
            raise ValueError("Nothing to say.")
        with self._lock:
            voice = self._ensure(voice_name)
            self.last_used = time.time()
            started = time.perf_counter()
            cfg = SynthesisConfig(length_scale=1.0 / max(0.5, min(rate, 2.0)))
            buf = io.BytesIO()
            frames = 0
            with wave.open(buf, "wb") as wav:
                first = True
                for chunk in voice.synthesize(text, syn_config=cfg):
                    if first:
                        wav.setnchannels(chunk.sample_channels)
                        wav.setsampwidth(chunk.sample_width)
                        wav.setframerate(chunk.sample_rate)
                        first = False
                    data = chunk.audio_int16_bytes
                    frames += len(data) // 2
                    wav.writeframes(data)
                rate_hz = voice.config.sample_rate
        elapsed = time.perf_counter() - started
        audio_s = frames / rate_hz if rate_hz else 0
        return buf.getvalue(), {"elapsed": round(elapsed, 3), "audio_seconds": round(audio_s, 2),
                                "rtf": round(elapsed / audio_s, 3) if audio_s else None}

    def download(self, name: str) -> None:
        """Download a voice from the official rhasspy/piper-voices repository (blocking)."""
        if name not in {v["name"] for v in VOICES} and not re.fullmatch(r"[a-z]{2}_[A-Z]{2}-[\w]+-(x_low|low|medium|high)", name):
            raise ValueError(f"Unknown voice name: {name}")
        import piper.download_voices as dv
        PIPER_DIR.mkdir(parents=True, exist_ok=True)
        self.downloads[name] = "downloading"
        try:
            dv.download_voice(name, PIPER_DIR)
            self.downloads[name] = "done"
        except Exception as exc:  # noqa: BLE001
            self.downloads[name] = f"failed: {exc}"
            for ext in (".onnx", ".onnx.json"):  # never leave half a voice behind
                (PIPER_DIR / f"{name}{ext}").unlink(missing_ok=True)
            raise
