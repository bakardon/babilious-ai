"""Energy-based voice activity detection with an adaptive noise floor.

Cheap enough to run on every 20 ms frame without noticeable CPU. Whisper does
the hard work only on the segments this finds.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from . import SAMPLE_RATE

FRAME = 320  # 20 ms at 16 kHz


def frame_db(frame: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(frame.astype(np.float32) / 32768.0)))) if frame.size else 0.0
    return 20 * np.log10(max(rms, 1e-6))


@dataclass
class Segment:
    audio: np.ndarray          # int16 samples
    truncated: bool = False    # hit the maximum length

    @property
    def seconds(self) -> float:
        return self.audio.size / SAMPLE_RATE


@dataclass
class EnergyVAD:
    """Feed int16 audio; get back finished speech segments.

    sensitivity: dB above the noise floor that counts as speech (lower = more sensitive).
    """
    sensitivity: float = 11.0
    min_speech_db: float = -50.0
    start_ms: int = 80
    end_ms: int = 700
    max_ms: int = 20_000
    preroll_ms: int = 300
    noise_db: float = -60.0
    in_speech: bool = False
    _pending: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int16))
    _speech_run: int = 0
    _silence_run: int = 0
    _buf: list = field(default_factory=list)
    _pre: deque = field(default_factory=deque)

    def __post_init__(self):
        self._pre = deque(maxlen=max(1, self.preroll_ms // 20))

    @property
    def threshold_db(self) -> float:
        return max(self.noise_db + self.sensitivity, self.min_speech_db)

    def reset(self, keep_noise: bool = True) -> None:
        noise = self.noise_db
        self.in_speech = False
        self._pending = np.zeros(0, dtype=np.int16)
        self._speech_run = self._silence_run = 0
        self._buf = []
        self._pre.clear()
        if keep_noise:
            self.noise_db = noise

    def seed(self, audio: np.ndarray) -> None:
        """Start a segment from already-heard audio (used for barge-in pre-roll)."""
        self.reset()
        self.in_speech = True
        self._buf = [audio.astype(np.int16)]

    def feed(self, samples: np.ndarray) -> list[Segment]:
        out: list[Segment] = []
        data = np.concatenate([self._pending, samples.astype(np.int16)])
        n = data.size // FRAME
        self._pending = data[n * FRAME:]
        for i in range(n):
            frame = data[i * FRAME:(i + 1) * FRAME]
            db = frame_db(frame)
            loud = db > self.threshold_db
            if not self.in_speech:
                # Track the noise floor: fall fast, rise slowly.
                self.noise_db = db if db < self.noise_db else self.noise_db * 0.995 + db * 0.005
                self._pre.append(frame)
                self._speech_run = self._speech_run + 1 if loud else 0
                if self._speech_run * 20 >= self.start_ms:
                    self.in_speech = True
                    self._buf = list(self._pre)
                    self._silence_run = 0
            else:
                self._buf.append(frame)
                self._silence_run = 0 if loud else self._silence_run + 1
                total_ms = len(self._buf) * 20
                if self._silence_run * 20 >= self.end_ms or total_ms >= self.max_ms:
                    audio = np.concatenate(self._buf)
                    out.append(Segment(audio, truncated=total_ms >= self.max_ms))
                    self.in_speech = False
                    self._buf = []
                    self._speech_run = 0
                    self._pre.clear()
        return out

    def partial(self) -> np.ndarray:
        return np.concatenate(self._buf) if self._buf else np.zeros(0, dtype=np.int16)
