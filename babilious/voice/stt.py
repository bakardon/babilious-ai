"""Speech-to-text with faster-whisper (CTranslate2, int8 on CPU)."""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import numpy as np

from . import SAMPLE_RATE, WHISPER_DIR

log = logging.getLogger(__name__)

MODELS = [
    {"name": "tiny.en", "label": "Tiny (fastest)", "download_mb": 75, "ram_mb": 150,
     "notes": "Near-instant; misses more words."},
    {"name": "base.en", "label": "Base (recommended)", "download_mb": 145, "ram_mb": 250,
     "notes": "Good accuracy, about 0.5 s for a short command on this CPU."},
    {"name": "small.en", "label": "Small (accurate, slow)", "download_mb": 480, "ram_mb": 700,
     "notes": "Noticeably better with accents/noise; 2-3x slower."},
]

# Whisper's classic hallucinations on silence/noise.
HALLUCINATIONS = {
    "you", "thank you", "thanks for watching", "thank you for watching", "bye", "okay", "so",
    "please subscribe", "subtitles by the amara.org community", ".", "",
}


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9' ]+", "", text.lower()).strip()


class SpeechToText:
    def __init__(self, model_name: str = "base.en"):
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()
        self.last_used = 0.0
        self.loading = False
        self.error = ""

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def is_downloaded(self, name: str | None = None) -> bool:
        name = name or self.model_name
        return any(WHISPER_DIR.glob(f"models--Systran--faster-whisper-{name}/snapshots/*/model.bin"))

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel
            self.loading, self.error = True, ""
            started = time.perf_counter()
            try:
                WHISPER_DIR.mkdir(parents=True, exist_ok=True)
                # int8 keeps RAM low and is the fastest option on a CPU without AVX-512 VNNI tricks.
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                           cpu_threads=4, download_root=str(WHISPER_DIR))
                log.info("Loaded whisper %s in %.1fs", self.model_name, time.perf_counter() - started)
            except Exception as exc:
                self.error = str(exc)
                raise
            finally:
                self.loading = False

    def unload(self) -> None:
        with self._lock:
            self._model = None

    def set_model(self, name: str) -> None:
        if name != self.model_name:
            self.unload()
            self.model_name = name

    def transcribe(self, audio: np.ndarray, hotwords: str | None = None) -> dict[str, Any]:
        """audio: int16 or float32 mono at 16 kHz."""
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        if audio.size < SAMPLE_RATE * 0.25:
            return {"text": "", "seconds": 0.0, "elapsed": 0.0}
        self.load()
        self.last_used = time.time()
        started = time.perf_counter()
        segments, info = self._model.transcribe(
            audio, language="en", beam_size=1, best_of=1, temperature=0.0,
            condition_on_previous_text=False, without_timestamps=True, vad_filter=False,
            hotwords=hotwords or None, no_speech_threshold=0.6,
        )
        parts, confident = [], False
        for seg in segments:
            parts.append(seg.text)
            if seg.no_speech_prob < 0.6 and seg.avg_logprob > -1.0:
                confident = True
        text = " ".join(p.strip() for p in parts).strip()
        if _normalise(text) in HALLUCINATIONS and not confident:
            text = ""
        elapsed = time.perf_counter() - started
        return {"text": text, "seconds": round(audio.size / SAMPLE_RATE, 2), "elapsed": round(elapsed, 2)}
