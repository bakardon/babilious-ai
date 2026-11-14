"""Wake words.

Two engines:
- "whisper" (default): speech bursts are transcribed by the already-loaded
  whisper model and fuzzy-matched against any word you like, so "Babilious"
  works out of the box. It also catches "Babilious, what's on my list?" in one go.
- "openwakeword": always-on neural detector for its pretrained phrases
  (hey_jarvis, hey_mycroft, alexa...) or a custom .onnx you trained. Lighter
  on CPU in noisy rooms, but it can't learn "Babilious" without training.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading

import numpy as np

from . import OWW_DIR

log = logging.getLogger(__name__)

OWW_PRETRAINED = ["hey_jarvis", "hey_mycroft", "hey_rhasspy", "alexa"]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def wake_match(transcript: str, wake_word: str, threshold: float = 0.72) -> tuple[bool, str]:
    """Fuzzy-find the wake word near the start. Returns (matched, the rest of the sentence).

    Whisper spells unusual names creatively ("Babylicious", "Bobby Lious"), so
    single words and joined pairs/triples of the first words are compared.
    """
    target = re.sub(r"[^a-z0-9]", "", wake_word.lower())
    words = _words(transcript)
    if not target or not words:
        return False, ""
    best, best_end = 0.0, -1
    for start in range(min(4, len(words))):
        for size in (1, 2, 3):
            if start + size > len(words):
                break
            cand = "".join(words[start:start + size])
            if abs(len(cand) - len(target)) > max(3, len(target) // 2):
                continue
            score = difflib.SequenceMatcher(None, cand, target).ratio()
            if score > best:
                best, best_end = score, start + size
    if best < threshold:
        return False, ""
    # Keep the original casing/punctuation of the remainder.
    raw = transcript.strip()
    tokens = re.findall(r"\S+", raw)
    rest = " ".join(tokens[best_end:]) if best_end < len(tokens) else ""
    return True, rest.lstrip(",.!?:;- ").strip()


class OpenWakeWord:
    """Thin wrapper over openwakeword.Model fed with 16 kHz int16 audio."""

    CHUNK = 1280  # 80 ms, what openWakeWord expects

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5):
        self.model_name = model
        self.threshold = threshold
        self._model = None
        self._buf = np.zeros(0, dtype=np.int16)
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            from openwakeword.model import Model
            from openwakeword.utils import download_models
            OWW_DIR.mkdir(parents=True, exist_ok=True)
            path = self.model_name
            if self.model_name in OWW_PRETRAINED:
                onnx = OWW_DIR / f"{self.model_name}_v0.1.onnx"
                if not onnx.exists():
                    download_models([self.model_name], target_directory=str(OWW_DIR))
                path = str(onnx)
            self._model = Model(wakeword_models=[path], inference_framework="onnx",
                                melspec_model_path=str(OWW_DIR / "melspectrogram.onnx"),
                                embedding_model_path=str(OWW_DIR / "embedding_model.onnx"))
        return self._model

    def feed(self, samples: np.ndarray) -> float:
        """Returns the highest score seen in this batch (0..1)."""
        with self._lock:
            model = self._ensure()
            self._buf = np.concatenate([self._buf, samples.astype(np.int16)])
            best = 0.0
            while self._buf.size >= self.CHUNK:
                chunk, self._buf = self._buf[:self.CHUNK], self._buf[self.CHUNK:]
                scores = model.predict(chunk)
                best = max(best, max(scores.values()) if scores else 0.0)
            return best

    def reset(self) -> None:
        with self._lock:
            if self._model is not None:
                self._model.reset()
            self._buf = np.zeros(0, dtype=np.int16)
