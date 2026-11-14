"""Shared voice models: loaded lazily, unloaded after a quiet spell to give RAM back."""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import numpy as np

from .. import activity, db
from . import available
from .stt import MODELS as STT_MODELS
from .stt import SpeechToText
from .tts import VOICES, TextToSpeech, installed_voices
from .wake import OpenWakeWord, wake_match

log = logging.getLogger(__name__)

IDLE_UNLOAD_SECONDS = 15 * 60
# Distinctive tail: if whisper echoes the prompt back verbatim it is hallucinating, not hearing.
PROMPT_TAIL = "Abubakar's assistant"


class VoiceEngine:
    def __init__(self):
        s = db.all_settings()
        self.stt = SpeechToText(str(s["stt_model"]))
        self.tts = TextToSpeech(str(s["tts_voice"]))
        self._oww: OpenWakeWord | None = None
        self._lock = threading.Lock()
        self.preparing = False
        self.prepare_error = ""
        threading.Thread(target=self._reaper, daemon=True, name="voice-reaper").start()

    # ------------------------------------------------------------ settings
    def sync_settings(self) -> dict[str, Any]:
        s = db.all_settings()
        self.stt.set_model(str(s["stt_model"]))
        self.tts.set_voice(str(s["tts_voice"]))
        if self._oww and self._oww.model_name != s["oww_model"]:
            self._oww = None
        return s

    # ------------------------------------------------------------ speech to text
    def transcribe(self, audio: np.ndarray, wake_word: str = "") -> dict[str, Any]:
        wake = wake_word or str(db.get_setting("wake_word"))
        result = self.stt.transcribe(audio, hotwords=None) if not wake else self._transcribe_prompted(audio, wake)
        text = result["text"]
        if PROMPT_TAIL.lower() in text.lower():
            text = ""  # prompt echo = hallucination
        result["text"] = text
        return result

    def _transcribe_prompted(self, audio: np.ndarray, wake: str) -> dict[str, Any]:
        """Bias whisper toward the assistant's (unusual) name, which it otherwise misspells."""
        stt = self.stt
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        if audio.size < 4000:
            return {"text": "", "seconds": 0.0, "elapsed": 0.0}
        stt.load()
        stt.last_used = time.time()
        started = time.perf_counter()
        segments, _ = stt._model.transcribe(  # noqa: SLF001 - same model, extra option
            audio, language="en", beam_size=1, temperature=0.0, without_timestamps=True,
            condition_on_previous_text=False, vad_filter=False, no_speech_threshold=0.6,
            initial_prompt=f"{wake}, {PROMPT_TAIL}.")
        parts = [s.text.strip() for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        text = " ".join(parts).strip()
        from .stt import HALLUCINATIONS, _normalise
        if _normalise(text) in HALLUCINATIONS:
            text = ""
        return {"text": text, "seconds": round(audio.size / 16000, 2),
                "elapsed": round(time.perf_counter() - started, 2)}

    def spot_wake(self, audio: np.ndarray) -> tuple[bool, str, str]:
        """Whisper-based wake word. Returns (woke, command after the name, full transcript)."""
        wake = str(db.get_setting("wake_word"))
        result = self.transcribe(audio[: 16000 * 6], wake)
        woke, rest = wake_match(result["text"], wake)
        return woke, rest, result["text"]

    # ------------------------------------------------------------ openWakeWord
    def oww(self) -> OpenWakeWord:
        with self._lock:
            name = str(db.get_setting("oww_model"))
            if self._oww is None or self._oww.model_name != name:
                self._oww = OpenWakeWord(name, threshold=float(db.get_setting("oww_threshold")))
            return self._oww

    # ------------------------------------------------------------ text to speech
    def speak(self, text: str, rate: float | None = None, voice: str | None = None) -> tuple[bytes, dict[str, Any]]:
        self.sync_settings()
        return self.tts.synthesize(text, rate or float(db.get_setting("speech_rate")), voice)

    # ------------------------------------------------------------ lifecycle
    def prepare(self) -> None:
        """Load (and on first use download) the speech models in the background."""
        if self.preparing:
            return

        def run():
            self.preparing, self.prepare_error = True, ""
            try:
                self.sync_settings()
                self.stt.load()
                self.stt.transcribe(np.zeros(8000, dtype=np.int16))  # warm-up
                if self.tts.voice_name in installed_voices():
                    self.tts.synthesize("Ready.")
                activity.record("voice", f"Voice ready: whisper {self.stt.model_name}, voice {self.tts.voice_name}")
            except Exception as exc:  # noqa: BLE001
                self.prepare_error = str(exc)
                log.exception("Voice prepare failed")
                activity.record("voice", f"Voice setup failed: {exc}", "failed")
            finally:
                self.preparing = False
        threading.Thread(target=run, daemon=True, name="voice-prepare").start()

    def _reaper(self) -> None:
        while True:
            time.sleep(60)
            now = time.time()
            if self.stt.loaded and now - self.stt.last_used > IDLE_UNLOAD_SECONDS and not _active_sessions:
                self.stt.unload()
                log.info("Unloaded whisper after inactivity")
            if self.tts.loaded and now - self.tts.last_used > IDLE_UNLOAD_SECONDS:
                self.tts.unload()

    def download_voice(self, name: str) -> None:
        threading.Thread(target=self._download_voice, args=(name,), daemon=True).start()

    def _download_voice(self, name: str) -> None:
        try:
            self.tts.download(name)
            activity.record("voice", f"Downloaded voice {name}")
        except Exception as exc:  # noqa: BLE001
            activity.record("voice", f"Voice download failed ({name}): {exc}", "failed")

    def status(self) -> dict[str, Any]:
        s = db.all_settings()
        voices = installed_voices()
        return {
            "available": available(),
            "stt": {"model": self.stt.model_name, "loaded": self.stt.loaded, "loading": self.stt.loading,
                    "downloaded": self.stt.is_downloaded(), "error": self.stt.error,
                    "models": [{**m, "downloaded": self.stt.is_downloaded(m["name"])} for m in STT_MODELS]},
            "tts": {"voice": self.tts.voice_name, "loaded": self.tts.loaded,
                    "installed": voices, "ready": self.tts.voice_name in voices,
                    "catalog": [{**v, "installed": v["name"] in voices,
                                 "download": self.tts.downloads.get(v["name"], "")} for v in VOICES]},
            "preparing": self.preparing, "prepare_error": self.prepare_error,
            "sessions": len(_active_sessions),
            "settings": {k: s[k] for k in VOICE_SETTINGS},
        }


VOICE_SETTINGS = ("stt_model", "tts_voice", "speech_rate", "wake_word", "wake_engine", "oww_model",
                  "oww_threshold", "barge_in", "vad_sensitivity", "follow_up_seconds", "spoken_replies")
_active_sessions: set = set()
_engine: VoiceEngine | None = None
_engine_lock = threading.Lock()


def engine() -> VoiceEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = VoiceEngine()
        return _engine


def valid_wake_word(word: str) -> str:
    word = word.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z' -]{2,30}", word):
        raise ValueError("Wake word: 3-30 letters (spaces, hyphens and apostrophes allowed).")
    return word


def is_created() -> bool:
    return _engine is not None
