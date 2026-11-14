import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BABILIOUS_SECRET_KEY", "test-secret")

from babilious import config  # noqa: E402
from babilious.llm import OllamaError, OllamaUnavailable  # noqa: E402
from babilious.research import sources as _sources  # noqa: E402

# The real network functions, for the few tests that exercise them against fake HTTP responses.
REAL_SOURCES = {n: getattr(_sources, n) for n in ("search_web", "search_web_rendered", "search_wikipedia",
                                                 "search_arxiv", "fetch")}


class FakeOllama:
    """Stands in for the Ollama server so tests run offline and fast."""

    host = "http://fake-ollama"

    def __init__(self, models=None, available=True, reply="Fine. Here's the answer.", error=None):
        self.models = models if models is not None else [
            {"name": "llama3.2:latest", "size": 2_019_393_189, "params": "3.2B", "quantization": "Q4_K_M",
             "family": "llama", "modified_at": None},
            {"name": "qwen2.5-coder:3b", "size": 1_929_912_626, "params": "3.1B", "quantization": "Q4_K_M",
             "family": "qwen2", "modified_at": None},
            {"name": "llama3.2:1b", "size": 1_321_098_329, "params": "1.2B", "quantization": "Q8_0",
             "family": "llama", "modified_at": None},
        ]
        self.available = available
        self.reply = reply
        self.error = error
        self.last_messages = None
        self.unloaded = []
        self.deleted = []

    def _check(self):
        if not self.available:
            raise OllamaUnavailable("connection refused")

    def is_available(self):
        return self.available

    def version(self):
        return "0.0-test" if self.available else None

    def list_models(self):
        self._check()
        return list(self.models)

    def running_models(self):
        return []

    def chat_stream(self, model, messages, options=None, keep_alive=None):
        self._check()
        if self.error:
            raise OllamaError(self.error)
        self.last_messages = messages
        for word in self.reply.split(" "):
            yield {"token": word + " "}
        yield {"done": True, "stats": {"tokens_per_sec": 12.0, "first_token_ms": 50, "total_ms": 120,
                                       "load_ms": 10, "output_tokens": 5, "prompt_tokens_per_sec": 80.0}}

    def chat(self, model, messages, options=None, keep_alive=None):
        text = "".join(c.get("token", "") for c in self.chat_stream(model, messages, options))
        return {"text": text, "stats": {"tokens_per_sec": 12.0, "first_token_ms": 50, "total_ms": 120}}

    plan = {"project": "Master's applications", "tasks": [
        {"title": "List 5 target programs and their deadlines", "due_in_days": 3, "priority": 1, "notes": ""},
        {"title": "Draft the statement of purpose", "due_in_days": 14, "priority": 1, "notes": ""},
        {"title": "Ask two professors for recommendation letters", "due_in_days": 7, "priority": 2, "notes": ""},
        {"title": "Draft the statement of purpose", "due_in_days": 20, "priority": 2, "notes": "duplicate"},
        {"title": "Book the IELTS test", "due_in_days": None, "priority": 2, "notes": ""},
    ]}

    def chat_json(self, model, messages, options=None):
        self._check()
        self.last_messages = messages
        return self.plan

    def embed(self, model, texts):
        """Deterministic bag-of-words vectors: texts sharing words point the same way."""
        import hashlib
        import re as _re
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in _re.findall(r"[a-z]{3,}", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(v)
        return out

    def unload_model(self, name):
        self.unloaded.append(name)

    def delete_model(self, name):
        self.deleted.append(name)

    def pull_model(self, name):
        yield {"status": "pulling", "completed": 1, "total": 2}
        yield {"status": "success"}


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Every test gets its own empty database and log folder."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    from babilious import db
    db.init_db()
    yield


@pytest.fixture(autouse=True)
def no_real_outside_world(tmp_path, monkeypatch):
    """Tests never touch the internet, the PC's media session, or a real Spotify login."""
    from babilious.integrations import music
    from babilious.research import sources

    def offline(*_a, **_k):
        raise sources.Offline("tests are offline")
    for name in ("search_web", "search_web_rendered", "search_wikipedia", "search_arxiv"):
        monkeypatch.setattr(sources, name, offline)
    monkeypatch.setattr(sources, "fetch", lambda hit: (_ for _ in ()).throw(RuntimeError("no network in tests")))

    def no_session(self, action):
        return {"session": False}
    monkeypatch.setattr(music.SystemMedia, "_run", no_session)
    monkeypatch.setattr(music, "TOKEN_FILE", tmp_path / "spotify_token.json")
    monkeypatch.setattr(music, "_paused_for_voice", False)
    monkeypatch.setattr(music, "open_search", lambda q: None)
    music.SYSTEM._cache = (0.0, None)


@pytest.fixture
def fake():
    return FakeOllama()


@pytest.fixture
def client(fake):
    from babilious.web import create_app
    app = create_app(client=fake, testing=True)
    c = app.test_client()
    c.fake = fake
    return c


H = {"X-Babilious": "1"}
