"""Milestone 3: voice.

Fast tests use a fake engine. The end-to-end tests at the bottom run the real
models: Piper speaks a sentence, the audio is streamed over the real WebSocket
like the browser does, and faster-whisper must hand back the right words.
They are skipped automatically if the models haven't been downloaded.
"""
import io
import json
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from babilious import db
from babilious.voice import PIPER_DIR, WHISPER_DIR
from babilious.voice.session import VoiceSession
from babilious.voice.tts import clean_for_speech
from babilious.voice.vad import EnergyVAD
from babilious.voice.wake import wake_match
from conftest import H

ROOT = Path(__file__).resolve().parent.parent
HAVE_MODELS = (any(WHISPER_DIR.glob("models--Systran--faster-whisper-base.en/snapshots/*/model.bin"))
               and (PIPER_DIR / "en_US-ryan-medium.onnx").exists())


# ---------------------------------------------------------------- wake word matching

@pytest.mark.parametrize("heard, rest", [
    ("Babilious, what's on my list today?", "what's on my list today?"),
    ("Hey Babilious.", ""),
    ("Okay, Babylicious, switch to the faster model", "switch to the faster model"),
    ("Bobby Lious what time is it", "what time is it"),
    ("That Babilious, what are we doing today?", "what are we doing today?"),
])
def test_wake_match_accepts_variants(heard, rest):
    assert wake_match(heard, "Babilious") == (True, rest)


@pytest.mark.parametrize("heard", ["Hey Barbara, what's up?", "The babies are hilarious.",
                                   "Fabulous work today.", "Is it raining in Lahore?", ""])
def test_wake_match_rejects_lookalikes(heard):
    assert wake_match(heard, "Babilious")[0] is False


def test_custom_wake_word():
    assert wake_match("Jarvis, lights on", "Jarvis") == (True, "lights on")
    assert wake_match("Computer, status report", "Computer") == (True, "status report")


# ---------------------------------------------------------------- VAD

def _tone(seconds, amp=8000, freq=220):
    t = np.arange(int(16000 * seconds)) / 16000
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _quiet(seconds, amp=40):
    return np.random.default_rng(0).normal(0, amp, int(16000 * seconds)).astype(np.int16)


def test_vad_finds_one_segment_with_preroll():
    vad = EnergyVAD(end_ms=500)
    stream = np.concatenate([_quiet(1), _tone(1.2), _quiet(1)])
    segs = []
    for i in range(0, stream.size, 333):  # odd chunk sizes, like a real socket
        segs += vad.feed(stream[i:i + 333])
    assert len(segs) == 1
    assert 1.3 <= segs[0].seconds <= 2.1  # speech + pre-roll + trailing silence


def test_vad_ignores_steady_noise_and_caps_length():
    vad = EnergyVAD()
    assert sum(len(vad.feed(_quiet(0.5, amp=300))) for _ in range(6)) == 0
    vad = EnergyVAD(max_ms=1000)
    segs = vad.feed(np.concatenate([_quiet(0.5), _tone(2.5)]))
    assert segs and segs[0].truncated


# ---------------------------------------------------------------- text for speech

def test_clean_for_speech():
    text = "Sure. Run `pytest`:\n```python\nprint('hi')\n```\nSee https://x.org and C:\\Users\\me\\a.txt **now** 🐾"
    out = clean_for_speech(text)
    assert "print" not in out and "https" not in out and "C:\\" not in out and "**" not in out
    assert "code on screen" in out and "pytest" in out and "🐾" not in out


# ---------------------------------------------------------------- session state machine (fake engine)

class FakeEngine:
    class _STT:
        model_name = "fake"

    def __init__(self, transcript="what's on my list", wake=(True, "", "Babilious")):
        self.stt = self._STT()
        self.transcript = transcript
        self.wake = wake
        self.spotted = 0

    def transcribe(self, audio, wake_word=""):
        return {"text": self.transcript, "elapsed": 0.01, "seconds": audio.size / 16000}

    def spot_wake(self, audio):
        self.spotted += 1
        return self.wake


def _session(engine):
    events = []
    s = VoiceSession(events.append, eng=engine)
    return s, events


def _wait(events, kind, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        for e in events:
            if e["type"] == kind:
                return e
        time.sleep(0.01)
    raise AssertionError(f"no {kind} event; got {[e['type'] for e in events]}")


def _pcm(audio):
    return audio.astype(np.int16).tobytes()


def test_push_to_talk_flow():
    s, ev = _session(FakeEngine("add a task to call mum"))
    s.command({"type": "config", "mode": "ptt"})
    s.command({"type": "ptt_start"})
    s.audio(_pcm(_tone(1.0)))
    s.command({"type": "ptt_stop"})
    t = _wait(ev, "transcript")
    assert t["text"] == "add a task to call mum" and t["via"] == "ptt"
    assert ev[-1] == {"type": "state", "state": "idle"}
    s.close()


def test_short_press_is_ignored():
    s, ev = _session(FakeEngine())
    s.command({"type": "ptt_start"})
    s.audio(_pcm(_tone(0.1)))
    s.command({"type": "ptt_stop"})
    assert _wait(ev, "heard_nothing")["reason"] == "too short"
    s.close()


def test_handsfree_wake_then_command():
    eng = FakeEngine("what's on my list", wake=(True, "", "Babilious"))
    s, ev = _session(eng)
    s.command({"type": "config", "mode": "handsfree"})
    s.audio(_pcm(np.concatenate([_quiet(0.5), _tone(0.8), _quiet(1.0)])))   # "Babilious"
    _wait(ev, "wake")
    assert s.state == "listening"
    s.audio(_pcm(np.concatenate([_tone(1.0), _quiet(1.0)])))                 # the command
    assert _wait(ev, "transcript")["via"] == "listen"
    assert s.state == "wake"   # back to waiting for the name
    s.close()


def test_handsfree_name_and_command_in_one_breath():
    s, ev = _session(FakeEngine(wake=(True, "what's on my list today", "Babilious, what's on my list today")))
    s.command({"type": "config", "mode": "handsfree"})
    s.audio(_pcm(np.concatenate([_quiet(0.5), _tone(1.5), _quiet(1.0)])))
    assert _wait(ev, "transcript") == {"type": "transcript", "text": "what's on my list today", "via": "wake"}
    s.close()


def test_no_wake_word_means_nothing_happens():
    eng = FakeEngine(wake=(False, "", "the babies are hilarious"))
    s, ev = _session(eng)
    s.command({"type": "config", "mode": "handsfree"})
    s.audio(_pcm(np.concatenate([_quiet(0.5), _tone(1.0), _quiet(1.0)])))
    time.sleep(0.2)
    assert eng.spotted == 1 and not [e for e in ev if e["type"] in ("wake", "transcript")]
    s.close()


def test_follow_up_window_times_out():
    db.set_setting("follow_up_seconds", 0.2)
    s, ev = _session(FakeEngine())
    s.command({"type": "config", "mode": "handsfree"})
    s.command({"type": "follow_up"})
    assert s.state == "listening"
    time.sleep(0.3)
    s.audio(_pcm(_quiet(0.1)))
    _wait(ev, "listen_timeout")
    assert s.state == "wake"
    s.close()


def test_barge_in_uses_preroll_and_cancels_stale_results():
    s, ev = _session(FakeEngine("stop, do this instead"))
    s.command({"type": "config", "mode": "handsfree"})
    s.command({"type": "speaking", "value": True})
    s.audio(_pcm(_tone(0.5)))   # user starts talking over Babilious
    s.command({"type": "barge_in"})
    assert s.state == "listening" and s.vad.in_speech  # seeded with what was already said
    s.audio(_pcm(np.concatenate([_tone(0.5), _quiet(1.0)])))
    assert _wait(ev, "transcript")["text"] == "stop, do this instead"
    s.close()


# ---------------------------------------------------------------- settings & API

def test_voice_settings_validation(client):
    ok = {"speech_rate": 1.2, "wake_word": "Jarvis", "barge_in": "wake", "wake_engine": "openwakeword",
          "oww_model": "hey_mycroft", "vad_sensitivity": 14, "follow_up_seconds": 5, "spoken_replies": "browser"}
    assert client.patch("/api/settings", json=ok, headers=H).status_code == 200
    for bad in ({"speech_rate": 5}, {"wake_word": "x"}, {"wake_word": "rm -rf /"}, {"barge_in": "maybe"},
                {"oww_model": "../../etc/passwd"}, {"stt_model": "large-v3"}, {"tts_voice": "nope"}):
        r = client.patch("/api/settings", json=bad, headers=H)
        assert r.status_code == 400, bad
    # Atomic: one bad value means nothing is saved.
    r = client.patch("/api/settings", json={"speech_rate": 0.8, "barge_in": "maybe"}, headers=H)
    assert r.status_code == 400 and db.get_setting("speech_rate") == 1.2


def test_voice_status_and_tts_errors(client):
    st = client.get("/api/voice/status").get_json()
    assert st["available"]["stt"] and "settings" in st
    assert client.post("/api/voice/tts", json={"text": ""}, headers=H).status_code == 400
    assert client.post("/api/voice/tts", json={"text": "x" * 3000}, headers=H).status_code == 400
    assert client.post("/api/voice/tts", json={"text": "hi"}).status_code == 403  # header guard


# ---------------------------------------------------------------- JS sentence splitter (Node)

@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_sentence_stream_js():
    script = """
      import { SentenceStream } from './static/js/voice.js';
      const out = []; const s = new SentenceStream((x) => out.push(x));
      for (const tok of ["Sure. ", "Here is the plan for", " today. First we fix", " the bug! Then:",
                         "\\n```py\\nprint('x')\\n```", "\\nDone, obviously."]) s.push(tok);
      s.flush(); console.log(JSON.stringify(out));
    """
    r = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out[0] == "Sure. Here is the plan for today."       # short "Sure." merged
    assert "First we fix the bug!" in out
    assert "I've put the code on screen." in out
    assert not any("print" in x for x in out)
    assert out[-1] == "Done, obviously."


# ---------------------------------------------------------------- real models, real socket

def _to16k(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    n = int(len(a) * 16000 / sr)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.int16)


@pytest.fixture(scope="module")
def live_server():
    from werkzeug.serving import make_server
    from babilious.web import create_app
    from conftest import FakeOllama
    app = create_app(client=FakeOllama(), testing=True)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def speech():
    from babilious.voice.engine import engine
    eng = engine()
    eng.stt.set_model("base.en")
    eng.tts.set_voice("en_US-ryan-medium")
    cache = {}

    def say(text):
        if text not in cache:
            cache[text] = _to16k(eng.tts.synthesize(text)[0])
        return cache[text]
    return say


def _stream(ws, audio):
    for i in range(0, audio.size, 320):   # 20 ms frames, like the browser worklet
        ws.send(audio[i:i + 320].tobytes())


def _events_until(ws, kind, timeout=30):
    got, end = [], time.time() + timeout
    while time.time() < end:
        msg = ws.receive(timeout=max(0.1, end - time.time()))
        if msg is None:
            continue
        e = json.loads(msg)
        got.append(e)
        if e["type"] == kind:
            return got
    raise AssertionError(f"timed out waiting for {kind}: {got}")


@pytest.mark.skipif(not HAVE_MODELS, reason="voice models not downloaded")
def test_live_socket_rejects_foreign_origin(live_server):
    import simple_websocket
    ws = simple_websocket.Client.connect(f"ws://{live_server}/ws/voice", headers={"Origin": "https://evil.example"})
    with pytest.raises(simple_websocket.ConnectionClosed):
        ws.receive(timeout=5)
        ws.receive(timeout=5)


@pytest.mark.skipif(not HAVE_MODELS, reason="voice models not downloaded")
def test_live_push_to_talk_transcribes_real_speech(live_server, speech):
    import simple_websocket
    ws = simple_websocket.Client.connect(f"ws://{live_server}/ws/voice", headers={"Origin": f"http://{live_server}"})
    ws.send(json.dumps({"type": "config", "mode": "ptt"}))
    _events_until(ws, "ready")
    ws.send(json.dumps({"type": "ptt_start"}))
    _stream(ws, np.concatenate([np.zeros(3200, np.int16), speech("Create a task to finish my GitHub portfolio."),
                                np.zeros(3200, np.int16)]))
    ws.send(json.dumps({"type": "ptt_stop"}))
    t = _events_until(ws, "transcript")[-1]
    assert "task" in t["text"].lower() and "portfolio" in t["text"].lower()
    ws.close()


@pytest.mark.skipif(not HAVE_MODELS, reason="voice models not downloaded")
def test_live_handsfree_wake_word_and_command(live_server, speech):
    import simple_websocket
    ws = simple_websocket.Client.connect(f"ws://{live_server}/ws/voice", headers={"Origin": f"http://{live_server}"})
    ws.send(json.dumps({"type": "config", "mode": "handsfree"}))
    _events_until(ws, "ready")
    room = np.random.default_rng(3).normal(0, 50, 16000).astype(np.int16)
    # Unrelated talk must not wake him...
    _stream(ws, np.concatenate([room, speech("The weather is lovely in Lahore."), room]))
    time.sleep(3)
    # ...his name plus a command must.
    _stream(ws, np.concatenate([room, speech("Babilious, what's on my list today?"), room]))
    events = _events_until(ws, "transcript")
    kinds = [e["type"] for e in events]
    assert kinds.count("wake") == 1
    t = events[-1]
    assert t["via"] == "wake" and "list" in t["text"].lower()
    ws.close()
