"""One voice conversation over a WebSocket.

The browser streams 16 kHz int16 mic audio only while the mic is open
(push-to-talk held, or hands-free switched on, both clearly shown in the UI).
Audio sits in RAM: a 2-second rolling buffer plus the current utterance, and it is
dropped as soon as it has been transcribed. Nothing is saved.

States:  idle → ptt (button held) → transcribing → idle
         wake (hands-free, waiting for the name) → listening → transcribing → wake
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Any, Callable

import numpy as np

from .. import db
from . import SAMPLE_RATE
from .engine import _active_sessions, engine
from .vad import EnergyVAD

log = logging.getLogger(__name__)

RING_SECONDS = 2.0
MAX_PTT_SECONDS = 60


class VoiceSession:
    def __init__(self, send: Callable[[dict[str, Any]], None], eng=None):
        self._send_raw = send
        self._send_lock = threading.Lock()
        self._lock = threading.RLock()
        self.eng = eng or engine()
        self.mode = "ptt"
        self.state = "idle"
        self.vad = EnergyVAD()
        self.ring: deque[np.ndarray] = deque()
        self.ring_samples = 0
        self.ptt_buf: list[np.ndarray] = []
        self.speaking = False
        self.listen_deadline: float | None = None
        self.generation = 0          # bumps on cancel so stale results are ignored
        self.spotting = False        # a wake check is running (bounded CPU: one at a time)
        self.jobs: queue.Queue = queue.Queue()
        self.closed = False
        self._apply_settings()
        self.worker = threading.Thread(target=self._work, daemon=True, name="voice-session")
        self.worker.start()
        _active_sessions.add(self)

    # ------------------------------------------------------------ plumbing
    def send(self, msg: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            with self._send_lock:
                self._send_raw(msg)
        except Exception:  # socket gone
            self.closed = True

    def _set_state(self, state: str, **extra: Any) -> None:
        self.state = state
        self.send({"type": "state", "state": state, **extra})

    def _base_state(self) -> str:
        return "wake" if self.mode == "handsfree" else "idle"

    def _apply_settings(self) -> None:
        s = db.all_settings()
        self.settings = s
        self.vad.sensitivity = float(s["vad_sensitivity"])

    def close(self) -> None:
        self.closed = True
        self.jobs.put(None)
        _active_sessions.discard(self)

    # ------------------------------------------------------------ commands from the browser
    def command(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        with self._lock:
            if kind == "config":
                self._apply_settings()
                self.mode = "handsfree" if msg.get("mode") == "handsfree" else "ptt"
                self.vad.reset()
                self._set_state(self._base_state())
                if self.mode == "handsfree" and self.settings["wake_engine"] == "openwakeword":
                    self.jobs.put(("warm_oww", self.generation, None))
                self.send({"type": "ready", "mode": self.mode, "wake_word": self._wake_label(),
                           "stt_model": self.eng.stt.model_name})
            elif kind == "ptt_start":
                self.generation += 1
                tail = self._ring_tail(0.15)
                self.ptt_buf = [tail] if tail.size else []
                self._set_state("ptt")
            elif kind == "ptt_stop":
                audio = np.concatenate(self.ptt_buf) if self.ptt_buf else np.zeros(0, np.int16)
                self.ptt_buf = []
                if audio.size < SAMPLE_RATE * 0.3:
                    self.send({"type": "heard_nothing", "reason": "too short"})
                    self._set_state(self._base_state())
                else:
                    self._transcribe(audio, "ptt")
            elif kind == "barge_in":
                # The browser heard the user talk over Babilious and already stopped playback.
                self.generation += 1
                self.speaking = False
                self.vad.seed(self._ring_tail(0.8))
                self.listen_deadline = None
                self._set_state("listening", via="barge_in")
            elif kind == "follow_up":
                if self.mode == "handsfree" and self.state in ("wake", "idle"):
                    self.vad.reset()
                    secs = float(msg.get("seconds") or self.settings["follow_up_seconds"])
                    self.listen_deadline = time.time() + secs
                    self._set_state("listening", via="follow_up", seconds=secs)
            elif kind == "speaking":
                self.speaking = bool(msg.get("value"))
            elif kind == "cancel":
                self.generation += 1
                self.ptt_buf = []
                self.vad.reset()
                self.listen_deadline = None
                self._set_state(self._base_state())

    def _wake_label(self) -> str:
        if self.settings["wake_engine"] == "openwakeword":
            return str(self.settings["oww_model"]).replace("_", " ")
        return str(self.settings["wake_word"])

    # ------------------------------------------------------------ audio from the browser
    def audio(self, data: bytes) -> None:
        samples = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16)
        if not samples.size:
            return
        with self._lock:
            self._remember(samples)
            state = self.state
            if state == "ptt":
                if sum(b.size for b in self.ptt_buf) < SAMPLE_RATE * MAX_PTT_SECONDS:
                    self.ptt_buf.append(samples)
            elif state == "wake":
                self._wake_audio(samples)
            elif state == "listening":
                if (self.listen_deadline and time.time() > self.listen_deadline and not self.vad.in_speech):
                    self.listen_deadline = None
                    self.send({"type": "listen_timeout"})
                    self._set_state(self._base_state())
                    return
                for seg in self.vad.feed(samples):
                    if seg.seconds >= 0.35:
                        self.listen_deadline = None
                        self._transcribe(seg.audio, "listen")
                        break

    def _wake_audio(self, samples: np.ndarray) -> None:
        if self.settings["wake_engine"] == "openwakeword":
            self.jobs.put(("oww", self.generation, samples))
            return
        for seg in self.vad.feed(samples):
            # One check at a time; a short burst is too short to hold a name.
            if 0.3 <= seg.seconds and not self.spotting:
                self.spotting = True
                self.jobs.put(("spot", self.generation, seg.audio))

    def _remember(self, samples: np.ndarray) -> None:
        self.ring.append(samples)
        self.ring_samples += samples.size
        while self.ring and self.ring_samples - self.ring[0].size >= RING_SECONDS * SAMPLE_RATE:
            self.ring_samples -= self.ring.popleft().size

    def _ring_tail(self, seconds: float) -> np.ndarray:
        if not self.ring:
            return np.zeros(0, np.int16)
        audio = np.concatenate(list(self.ring))
        return audio[-int(seconds * SAMPLE_RATE):]

    def _transcribe(self, audio: np.ndarray, via: str) -> None:
        self._set_state("transcribing")
        self.jobs.put(("transcribe", self.generation, (audio, via)))

    # ------------------------------------------------------------ worker thread
    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None or self.closed:
                return
            kind, gen, payload = job
            try:
                if kind == "transcribe":
                    audio, via = payload
                    result = self.eng.transcribe(audio)
                    self._on_transcript(gen, result, via)
                elif kind == "spot":
                    try:
                        woke, rest, heard = self.eng.spot_wake(payload)
                    finally:
                        self.spotting = False
                    if woke:
                        self._on_wake(gen, rest, heard)
                elif kind == "oww":
                    score = self.eng.oww().feed(payload)
                    if score >= float(self.settings["oww_threshold"]):
                        self.eng.oww().reset()
                        self._on_wake(gen, "", f"(wake score {score:.2f})")
                elif kind == "warm_oww":
                    self.eng.oww()
            except Exception as exc:  # noqa: BLE001
                log.exception("Voice job failed")
                self.send({"type": "error", "message": str(exc)})
                with self._lock:
                    if gen == self.generation:
                        self._set_state(self._base_state())

    def _on_transcript(self, gen: int, result: dict[str, Any], via: str) -> None:
        with self._lock:
            if gen != self.generation:
                return
            text = result["text"].strip()
            wake = str(self.settings["wake_word"]).lower()
            if text.lower().strip(" .,!?") == wake:
                text = ""  # just the name again: nothing to do
            if text:
                self.send({"type": "transcript", "text": text, "via": via,
                           "elapsed": result.get("elapsed"), "seconds": result.get("seconds")})
            else:
                self.send({"type": "heard_nothing", "reason": "no words recognised"})
            self._set_state(self._base_state())

    def _on_wake(self, gen: int, rest: str, heard: str) -> None:
        with self._lock:
            if gen != self.generation or self.state != "wake":
                return
            barge = self.speaking
            self.send({"type": "wake", "heard": heard, "barge": barge})
            if len(rest.split()) >= 2:
                # "Babilious, what's on my list?" in one breath.
                self.send({"type": "transcript", "text": rest, "via": "wake"})
                self._set_state(self._base_state())
            else:
                self.vad.reset()
                self.listen_deadline = time.time() + float(self.settings["follow_up_seconds"])
                self._set_state("listening", via="wake")
