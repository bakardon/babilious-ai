"""Flask app: HTML shell plus a small JSON API."""
from __future__ import annotations

import json
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import re
from datetime import date, datetime
from typing import Any, Callable
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, render_template, request, send_file, stream_with_context

from . import activity, assistant, config, db, indexer, knowledge, memory, models, system, tasks
from .llm import OllamaClient, OllamaError, OllamaUnavailable
from . import tools as tools_pkg
from .tools import actions as tool_actions
from .tools import apps as app_tools
from .tools import places
from .tools import screen as screen_tools
from .integrations import github as gh_tools
from .integrations import ics
from .integrations import music
from .integrations import vision
from .research import engine as research
from .manager import briefing as m_briefing
from .manager import habits as m_habits
from .manager import notes as m_notes
from .manager import reminders as m_reminders
from .manager.timeparse import describe as describe_when
from .manager.timeparse import next_occurrence
from .manager.timeparse import parse as parse_when
from .voice import available as voice_available
from .voice import engine as voice_engine
from .voice.engine import valid_wake_word
from .voice.session import VoiceSession
from .voice.stt import MODELS as STT_MODELS
from .voice.tts import VOICES as TTS_VOICES
from .voice.tts import installed_voices
from .voice.wake import OWW_PRETRAINED

try:  # optional: voice works only when flask-sock is installed
    from flask_sock import ConnectionClosed, Sock
except ImportError:  # pragma: no cover
    Sock = None
    ConnectionClosed = Exception

log = logging.getLogger("babilious")


def _setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(config.LOG_LEVEL)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(config.LOG_DIR / "babilious.log", maxBytes=1_000_000, backupCount=3,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _ndjson(events) -> Response:
    def gen():
        for event in events:
            yield json.dumps(event) + "\n"
    return Response(stream_with_context(gen()), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def create_app(client: OllamaClient | None = None, testing: bool = False) -> Flask:
    _setup_logging()
    app = Flask(__name__, template_folder=str(config.BASE_DIR / "templates"),
                static_folder=str(config.BASE_DIR / "static"))
    app.config["SECRET_KEY"] = config.get_secret_key()
    app.config["TESTING"] = testing
    app.config["JSON_SORT_KEYS"] = False
    ollama = client or OllamaClient()
    app.extensions["ollama"] = ollama
    sock = Sock(app) if Sock else None
    db.init_db()
    tool_actions.init()
    if not testing:
        m_reminders.start_scheduler()
    if not testing and indexer.list_folders():
        # Cheap incremental refresh on startup: only changed files are re-read.
        indexer.start_indexing(embed_client=ollama)

    # ---------------------------------------------------------- safety
    @app.before_request
    def guard():
        """Only this app's own page may change state.

        A custom header cannot be sent cross-site without a CORS preflight
        (which we never approve), so other websites can't drive the local API.
        """
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/"):
            if request.headers.get("X-Babilious") != "1":
                abort(403, "Missing X-Babilious header")
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                abort(403, "Cross-origin request refused")

    def _same_origin(origin: str, host: str) -> bool:
        """Origin must be this server. Ports are compared whenever the Host header has one."""
        o = urlparse(origin)
        h_name, _, h_port = host.rpartition(":") if ":" in host.strip("[]").split("]")[-1] else (host, "", "")
        if (o.hostname or "").lower() != h_name.strip("[]").lower():
            return False
        return not h_port or str(o.port or (443 if o.scheme == "https" else 80)) == h_port

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def http_error(err):
        if request.path.startswith("/api/"):
            return jsonify({"error": getattr(err, "description", str(err))}), err.code
        return err

    @app.errorhandler(ValueError)
    def value_error(err):
        return jsonify({"error": str(err)}), 400

    @app.errorhandler(OllamaUnavailable)
    def ollama_down(_err):
        return jsonify({"error": "Ollama is not running. Start it with `ollama serve`."}), 503

    @app.errorhandler(OllamaError)
    def ollama_error(err):
        return jsonify({"error": str(err)}), 502

    # ---------------------------------------------------------- pages
    @app.get("/")
    def home():
        return render_template("index.html")

    # ---------------------------------------------------------- status
    @app.get("/api/status")
    def status():
        available = ollama.is_available()
        return jsonify({
            "ollama": available,
            "active_model": models.active_model(),
            "resources": system.resources(ollama),  # loaded_models is [] when Ollama is down
            "current_task": tasks.current_task(),
            "open_tasks": len(tasks.open_tasks()),
            "memories": len(memory.list_memories()),
            "recent_activity": activity.recent(8),
        })

    @app.get("/api/health")
    def health():
        return jsonify({"checks": system.health(ollama)})

    # ---------------------------------------------------------- chat
    def _conversation_id(data: dict) -> int:
        cid = data.get("conversation_id")
        if cid and memory.conversation_exists(int(cid)):
            return int(cid)
        return memory.create_conversation()

    @app.post("/api/chat/stream")
    def chat_stream():
        data = _body()
        text = str(data.get("message", "")).strip()
        if not text:
            return jsonify({"error": "Please enter a message."}), 400
        if len(text) > 8000:
            return jsonify({"error": "Message is too long (8000 characters max)."}), 400
        cid = _conversation_id(data)
        via = "voice" if data.get("via") == "voice" else "text"

        def events():
            yield {"type": "conversation", "id": cid}
            yield from assistant.respond(cid, text, ollama, via=via)
        return _ndjson(events())

    @app.post("/api/chat")
    def chat():
        """Non-streaming variant (kept for scripts and the original API)."""
        data = _body()
        text = str(data.get("message", "")).strip()
        if not text:
            return jsonify({"error": "Please enter a message."}), 400
        cid = _conversation_id(data)
        final: dict[str, Any] = {}
        for event in assistant.respond(cid, text, ollama):
            if event["type"] in ("done", "error"):
                final = event
        if final.get("type") == "error":
            return jsonify({"error": final["message"], "conversation_id": cid}), 502
        return jsonify({"question": text, "answer": final.get("text", ""), "conversation_id": cid,
                        "mood": final.get("mood"), "source": final.get("source")})

    @app.get("/api/conversations")
    def conversations():
        return jsonify({"conversations": memory.list_conversations()})

    @app.post("/api/conversations")
    def new_conversation():
        return jsonify({"id": memory.create_conversation()}), 201

    @app.get("/api/conversations/<int:cid>")
    def conversation(cid: int):
        if not memory.conversation_exists(cid):
            abort(404, "Conversation not found")
        return jsonify({"id": cid, "messages": memory.get_messages(cid)})

    @app.delete("/api/conversations/<int:cid>")
    def delete_conversation(cid: int):
        if not memory.delete_conversation(cid):
            abort(404, "Conversation not found")
        activity.record("chat", f"Deleted conversation #{cid}")
        return jsonify({"ok": True})

    @app.post("/api/clear")
    def clear():
        """Original endpoint: now starts a fresh conversation (history is kept)."""
        return jsonify({"ok": True, "id": memory.create_conversation()})

    # ---------------------------------------------------------- models
    @app.get("/api/models")
    def list_models():
        return jsonify(models.overview(ollama))

    @app.post("/api/models/active")
    def set_model():
        name = models.set_active(ollama, str(_body().get("name", "")))
        return jsonify({"active": name})

    @app.post("/api/models/benchmark")
    def bench():
        name = str(_body().get("name") or models.active_model())
        return jsonify(models.benchmark(ollama, name))

    @app.post("/api/models/pull")
    def pull():
        name = str(_body().get("name", "")).strip()
        if not name:
            abort(400, "Model name required")

        def events():
            try:
                for p in ollama.pull_model(name):
                    yield {"type": "progress", "status": p.get("status", ""),
                           "completed": p.get("completed"), "total": p.get("total")}
                activity.record("model", f"Downloaded model {name}")
                yield {"type": "done", "name": name}
            except (OllamaError, OllamaUnavailable) as exc:
                activity.record("model", f"Download of {name} failed: {exc}", "failed")
                yield {"type": "error", "message": str(exc)}
        return _ndjson(events())

    @app.delete("/api/models/<path:name>")
    def delete_model(name: str):
        if name == models.active_model():
            abort(400, "Switch to another model before removing the active one.")
        ollama.delete_model(name)
        activity.record("model", f"Removed model {name}")
        return jsonify({"ok": True})

    # ---------------------------------------------------------- tasks
    @app.get("/api/tasks")
    def get_tasks():
        return jsonify({"tasks": tasks.list_tasks(request.args.get("status") or None)})

    @app.post("/api/tasks")
    def add_task():
        d = _body()
        tid = tasks.create_task(str(d.get("title", "")), notes=str(d.get("notes", "")),
                                priority=int(d.get("priority", 2)), project=str(d.get("project", "")),
                                due_date=d.get("due_date") or None)
        activity.record("task", f"Created task: {d.get('title')}")
        return jsonify({"id": tid}), 201

    @app.patch("/api/tasks/<int:tid>")
    def edit_task(tid: int):
        if not tasks.update_task(tid, **_body()):
            abort(404, "Task not found")
        return jsonify({"ok": True})

    @app.delete("/api/tasks/<int:tid>")
    def remove_task(tid: int):
        if not tasks.delete_task(tid):
            abort(404, "Task not found")
        activity.record("task", f"Deleted task #{tid}")
        return jsonify({"ok": True})

    # ---------------------------------------------------------- memories
    @app.get("/api/memories")
    def get_memories():
        return jsonify({"memories": memory.list_memories(request.args.get("q", "")),
                        "kinds": memory.MEMORY_KINDS})

    @app.post("/api/memories")
    def add_mem():
        d = _body()
        mid = memory.add_memory(str(d.get("content", "")), kind=str(d.get("kind", "fact")),
                                pinned=bool(d.get("pinned")))
        activity.record("memory", "Added a memory")
        return jsonify({"id": mid}), 201

    @app.patch("/api/memories/<int:mid>")
    def edit_mem(mid: int):
        if not memory.update_memory(mid, **_body()):
            abort(404, "Memory not found")
        return jsonify({"ok": True})

    @app.delete("/api/memories/<int:mid>")
    def del_mem(mid: int):
        if not memory.delete_memory(mid):
            abort(404, "Memory not found")
        activity.record("memory", f"Deleted memory #{mid}")
        return jsonify({"ok": True})

    @app.get("/api/knowledge")
    def knowledge_files():
        return jsonify({"files": knowledge.file_list()})

    # ---------------------------------------------------------- second brain
    @app.get("/api/folders")
    def get_folders():
        return jsonify({"folders": indexer.list_folders(), "job": indexer.job_status(),
                        "stats": indexer.stats()})

    @app.post("/api/folders")
    def post_folder():
        folder = indexer.add_folder(str(_body().get("path", "")))
        indexer.start_indexing(folder["id"], embed_client=ollama)
        return jsonify(folder), 201

    @app.post("/api/folders/pick")
    def pick_folder():
        """Open the native folder dialog on this PC. Only returns the path; adding is separate."""
        return jsonify({"path": indexer.pick_folder_dialog()})

    @app.delete("/api/folders/<int:fid>")
    def delete_folder(fid: int):
        if indexer.job_status().get("running"):
            abort(400, "Wait for indexing to finish (or cancel it) first.")
        if not indexer.remove_folder(fid):
            abort(404, "Folder not found")
        return jsonify({"ok": True})

    @app.post("/api/index")
    def start_index():
        fid = _body().get("folder_id")
        started = indexer.start_indexing(int(fid) if fid else None, embed_client=ollama)
        return jsonify({"started": started, "job": indexer.job_status()})

    @app.get("/api/index/status")
    def index_status():
        return jsonify(indexer.job_status())

    @app.post("/api/index/cancel")
    def cancel_index():
        indexer.cancel_indexing()
        return jsonify({"ok": True})

    @app.get("/api/files/search")
    def file_search():
        a = request.args
        results = indexer.search(a.get("q", ""), ext=a.get("ext") or None,
                                 folder_id=int(a["folder"]) if a.get("folder") else None,
                                 sort=a.get("sort", "relevance"), limit=min(int(a.get("limit", 50)), 200))
        return jsonify({"results": results})

    @app.get("/api/documents/<int:doc_id>")
    def document(doc_id: int):
        try:
            return jsonify(indexer.get_document(doc_id))
        except ValueError:
            abort(404, "Document not found")

    @app.get("/api/graph")
    def doc_graph():
        return jsonify(indexer.graph(limit=min(int(request.args.get("limit", 150)), 400)))

    # ---------------------------------------------------------- settings / activity
    def _bool(v: Any) -> bool:
        return v in (True, 1, "1", "true", "True", "on", "yes")

    def _choice(*options: str):
        def parse(v: Any) -> str:
            if str(v) not in options:
                raise ValueError(f"must be one of {', '.join(options)}")
            return str(v)
        return parse

    def _ranged(kind, lo, hi):
        def parse(v: Any):
            x = kind(v)
            if not lo <= x <= hi:
                raise ValueError(f"must be between {lo} and {hi}")
            return x
        return parse

    def _oww_model(v: Any) -> str:
        v = str(v).strip()
        if v in OWW_PRETRAINED or (v.lower().endswith(".onnx") and Path(v).is_file()):
            return v
        raise ValueError(f"use one of {', '.join(OWW_PRETRAINED)} or a path to a custom .onnx model")

    def _hhmm_or_empty(v: Any) -> str:
        v = str(v or "").strip()
        if v and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("use HH:MM, e.g. 08:30")
        return v

    def _folder_or_empty(v: Any) -> str:
        v = str(v or "").strip()
        if v and not Path(v).is_dir():
            raise ValueError("folder not found")
        return v

    def _name_map(v: Any, check: Callable[[Path], bool], what: str) -> dict[str, str]:
        if not isinstance(v, dict) or len(v) > 100:
            raise ValueError("expected a {name: path} map")
        out = {}
        for name, path in v.items():
            name = " ".join(str(name).lower().split())
            if not re.fullmatch(r"[a-z0-9][a-z0-9 .+_-]{0,40}", name):
                raise ValueError(f"bad name: {name}")
            if not check(Path(str(path))):
                raise ValueError(f"{what} not found: {path}")
            out[name] = str(path)
        return out

    def _custom_apps(v: Any) -> dict[str, str]:
        return _name_map(v, lambda p: p.is_file() and p.suffix.lower() in (".exe", ".lnk", ".bat", ".cmd", ".url"),
                         "program (.exe/.lnk)")

    def _projects(v: Any) -> dict[str, str]:
        return _name_map(v, lambda p: p.is_dir(), "folder")

    def _voice_name(v: Any) -> str:
        v = str(v)
        if v not in {x["name"] for x in TTS_VOICES} and v not in installed_voices():
            raise ValueError("unknown voice")
        return v

    def _vision_model(v: Any) -> str:
        v = str(v or "").strip()
        if v and not re.fullmatch(r"[\w.:/-]{1,80}", v):
            raise ValueError("invalid model name")
        return v

    EDITABLE = {"temperature": float, "num_ctx": int, "keep_alive": str,
                "history_messages": int, "max_reply_tokens": int, "user_name": str,
                "use_documents_in_chat": _bool, "semantic_search": _bool, "embed_model": str,
                # voice
                "stt_model": _choice(*[m["name"] for m in STT_MODELS]),
                "tts_voice": _voice_name,
                "speech_rate": _ranged(float, 0.5, 2.0),
                "spoken_replies": _choice("piper", "browser", "off"),
                "wake_word": valid_wake_word,
                "wake_engine": _choice("whisper", "openwakeword"),
                "oww_model": _oww_model,
                "oww_threshold": _ranged(float, 0.1, 0.95),
                "barge_in": _choice("voice", "wake", "off"),
                "vad_sensitivity": _ranged(float, 4.0, 30.0),
                "follow_up_seconds": _ranged(float, 0.0, 20.0),
                # computer assistant
                "default_folder": _folder_or_empty,
                "model_tool_routing": _bool,
                "custom_apps": _custom_apps,
                "projects": _projects,
                # personal manager
                "briefing_time": _hhmm_or_empty,
                "desktop_notifications": _bool,
                # integrations (Milestone 6)
                "spotify_client_id": music.valid_client_id,
                "music_pause_for_voice": _bool,
                "calendar_ics_url": ics.valid_url,
                "vision_model": _vision_model,
                "research_depth": _choice("quick", "normal", "deep")}

    @app.get("/api/actions")
    def list_tool_actions():
        return jsonify({"actions": tool_actions.list_actions(request.args.get("status") or None,
                                                             min(int(request.args.get("limit", 50)), 200))})

    @app.post("/api/actions/<int:aid>/approve")
    def approve_action(aid: int):
        # Only reachable from the page (X-Babilious header guard): never from the model or a web page.
        return jsonify(tool_actions.approve(aid))

    @app.post("/api/actions/<int:aid>/reject")
    def reject_action(aid: int):
        return jsonify(tool_actions.reject(aid))

    @app.post("/api/actions/run")
    def run_action():
        """Buttons in the UI (screenshot, look at screen...) go through the same permission rules."""
        d = _body()
        return jsonify(tool_actions.request(str(d.get("tool", "")), dict(d.get("args") or {}), "ui"))

    @app.get("/api/screenshots/<name>")
    def screenshot(name: str):
        p = screen_tools.screenshot_path(name)
        if not p:
            abort(404, "Screenshot not found")
        return send_file(p, mimetype="image/png", max_age=0)

    @app.get("/api/automation")
    def automation_info():
        s = db.all_settings()
        return jsonify({"apps": app_tools.installed_apps(), "projects": places.projects(),
                        "default_folder": str(places.default_folder()),
                        "custom_default_folder": s.get("default_folder", ""),
                        "model_tool_routing": s.get("model_tool_routing", True),
                        "tools": [{"name": t.name, "description": t.description}
                                  for t in tools_pkg.load_all().values()]})

    @app.get("/api/settings")
    def get_settings():
        s = db.all_settings()
        return jsonify({k: s.get(k) for k in [*EDITABLE, "active_model"]})

    @app.patch("/api/settings")
    def patch_settings():
        changed = []
        body = _body()
        if _bool(body.get("semantic_search")) and not db.get_setting("semantic_search"):
            embed_model = str(body.get("embed_model") or db.get_setting("embed_model"))
            names = [m["name"] for m in ollama.list_models()] if ollama.is_available() else []
            if not any(n == embed_model or n.split(":")[0] == embed_model.split(":")[0] for n in names):
                abort(400, f"Semantic search needs the embedding model '{embed_model}'. "
                           f"Download it in Settings > Models (or: ollama pull {embed_model}).")
        parsed = {}
        for key, value in body.items():  # validate everything before saving anything
            if key not in EDITABLE:
                abort(400, f"Unknown setting: {key}")
            try:
                parsed[key] = EDITABLE[key](value)
            except (TypeError, ValueError) as exc:
                detail = str(exc) if str(exc) and "could not convert" not in str(exc) else "wrong type"
                abort(400, f"Invalid value for {key}: {detail}")
        for key, value in parsed.items():
            db.set_setting(key, value)
            changed.append(key)
        if changed:
            activity.record("settings", f"Changed settings: {', '.join(changed)}")
        if "semantic_search" in changed and db.get_setting("semantic_search"):
            indexer.start_indexing(embed_client=ollama)  # embeds the chunks already indexed
        if {"stt_model", "tts_voice"} & set(changed) and voice_engine.is_created():
            voice_engine.engine().sync_settings()
        return jsonify({"ok": True, "changed": changed})

    @app.get("/api/activity")
    def get_activity():
        return jsonify({"activity": activity.recent(int(request.args.get("limit", 50)))})

    # ---------------------------------------------------------- voice (Milestone 3)
    @app.get("/api/voice/status")
    def voice_status():
        if not all(voice_available().values()):
            missing = [k for k, ok in voice_available().items() if not ok]
            return jsonify({"available": voice_available(), "missing": missing})
        return jsonify(voice_engine.engine().status())

    @app.post("/api/voice/prepare")
    def voice_prepare():
        _require_voice("stt")
        voice_engine.engine().prepare()
        return jsonify({"ok": True})

    @app.post("/api/voice/tts")
    def voice_tts():
        _require_voice("tts")
        d = _body()
        text = str(d.get("text", "")).strip()
        if not text:
            abort(400, "No text")
        if len(text) > 2000:
            abort(400, "Text too long for one utterance (2000 characters max).")
        rate = d.get("rate")
        try:
            v = d.get("voice")
            wav, stats = voice_engine.engine().speak(text, float(rate) if rate else None,
                                                     _voice_name(v) if v else None)
        except FileNotFoundError as exc:
            abort(409, str(exc))
        return Response(wav, mimetype="audio/wav",
                        headers={"Cache-Control": "no-store", "X-TTS-Seconds": str(stats["elapsed"])})

    @app.post("/api/voice/voices/download")
    def voice_download():
        _require_voice("tts")
        name = _voice_name(_body().get("name", ""))
        voice_engine.engine().download_voice(name)
        return jsonify({"ok": True, "name": name}), 202

    def _require_voice(part: str) -> None:
        if not voice_available().get(part):
            abort(503, "Voice packages are not installed. Run: .venv\\Scripts\\python -m pip install -r requirements.txt")

    if sock is not None:
        @sock.route("/ws/voice")
        def voice_ws(ws):
            # WebSockets skip CORS, so check the origin ourselves: only this app's page may connect.
            origin = request.headers.get("Origin")
            if not origin or not _same_origin(origin, request.host):
                log.warning("Refused voice socket from origin %s", origin)
                ws.close(reason=1008, message="origin not allowed")
                return
            if not voice_available()["stt"]:
                ws.send(json.dumps({"type": "error", "message": "faster-whisper is not installed."}))
                return
            session = VoiceSession(lambda msg: ws.send(json.dumps(msg)))
            try:
                while True:
                    data = ws.receive()
                    if data is None:
                        break
                    if isinstance(data, (bytes, bytearray)):
                        session.audio(bytes(data))
                    else:
                        session.command(json.loads(data))
            except ConnectionClosed:
                pass
            finally:
                session.close()

    # ---------------------------------------------------------- personal manager (Milestone 5)
    def _local_dt(v: Any) -> datetime:
        try:
            return datetime.fromisoformat(str(v)).replace(tzinfo=None, second=0, microsecond=0)
        except ValueError:
            abort(400, "Invalid date/time")

    @app.get("/api/reminders")
    def get_reminders():
        return jsonify({"reminders": m_reminders.list_reminders(include_done=request.args.get("all") == "1")})

    @app.post("/api/reminders")
    def add_reminder():
        d = _body()
        text = str(d.get("text", "")).strip()
        if d.get("at"):
            at, rec = _local_dt(d["at"]), d.get("recurrence") or None
            if rec not in (None, "daily", "weekdays", "monthly") and not re.fullmatch(r"weekly:[0-6]", str(rec)):
                abort(400, "Invalid recurrence")
        else:
            w = parse_when(str(d.get("when", "")))
            if not w:
                abort(400, "I couldn't read that time. Try \"tomorrow at 9\" or \"in 2 hours\".")
            at, rec = w.at, w.recurrence
        return jsonify(m_reminders.create(text, at, rec)), 201

    @app.post("/api/reminders/<int:rid>/<op>")
    def reminder_op(rid: int, op: str):
        if op == "done":
            return jsonify(m_reminders.update_status(rid, "done"))
        if op == "cancel":
            return jsonify(m_reminders.update_status(rid, "cancelled"))
        if op == "snooze":
            return jsonify(m_reminders.snooze(rid, int(_body().get("minutes", 10))))
        abort(404, "Unknown operation")

    @app.post("/api/parse-time")
    def parse_time():
        w = parse_when(str(_body().get("text", "")))
        if not w:
            return jsonify({"ok": False})
        return jsonify({"ok": True, "at": w.at.isoformat(timespec="minutes"), "recurrence": w.recurrence,
                        "when": describe_when(w.at, w.recurrence), "rest": w.rest})

    @app.get("/api/notifications")
    def get_notifications():
        return jsonify({"notifications": m_reminders.notifications(
            unread_only=request.args.get("all") != "1", after_id=int(request.args.get("after", 0)))})

    @app.post("/api/notifications/read")
    def read_notifications():
        nid = _body().get("id")
        m_reminders.mark_read(int(nid) if nid else None)
        return jsonify({"ok": True})

    @app.get("/api/agenda")
    def get_agenda():
        a = m_briefing.agenda()
        return jsonify({**a, "text": m_briefing.agenda_text(a)})

    @app.get("/api/week")
    def get_week():
        w = m_briefing.week_review()
        return jsonify({**w, "text": m_briefing.week_text(w)})

    @app.get("/api/history")
    def history():
        return jsonify({"results": m_briefing.search_history(request.args.get("q", ""), limit=60)})

    @app.get("/api/habits")
    def get_habits():
        return jsonify({"habits": m_habits.list_habits()})

    @app.post("/api/habits")
    def add_habit():
        d = _body()
        return jsonify(m_habits.create(str(d.get("name", "")), str(d.get("schedule", "daily")))), 201

    @app.post("/api/habits/<int:hid>/log")
    def log_habit(hid: int):
        d = _body()
        day = date.fromisoformat(d["day"]) if d.get("day") else None
        return jsonify(m_habits.log(hid, day, done=bool(d.get("done", True))))

    @app.delete("/api/habits/<int:hid>")
    def del_habit(hid: int):
        if not m_habits.archive(hid):
            abort(404, "Habit not found")
        return jsonify({"ok": True})

    @app.get("/api/notes")
    def get_notes():
        return jsonify({"notes": m_notes.search(request.args.get("q", ""))})

    @app.post("/api/notes")
    def add_note():
        d = _body()
        return jsonify(m_notes.create(str(d.get("body", "")), str(d.get("title", "")), str(d.get("tags", "")))), 201

    @app.patch("/api/notes/<int:nid>")
    def edit_note(nid: int):
        d = _body()
        return jsonify(m_notes.update(nid, d.get("body"), d.get("title"), d.get("tags")))

    @app.delete("/api/notes/<int:nid>")
    def del_note(nid: int):
        if not m_notes.delete(nid):
            abort(404, "Note not found")
        return jsonify({"ok": True})

    @app.get("/api/projects-summary")
    def projects_summary():
        return jsonify({"projects": tasks.projects_summary(m_reminders.local_now().date())})

    @app.get("/api/calendar")
    def calendar_feed():
        """Tasks with due dates plus reminder occurrences (repeating ones expanded) in [from, to)."""
        start = date.fromisoformat(request.args["from"])
        end = date.fromisoformat(request.args["to"])
        if (end - start).days > 62:
            abort(400, "Range too large")
        items = [{"kind": "task", "date": t["due_date"], "title": t["title"], "done": t["status"] == "done", "id": t["id"]}
                 for t in tasks.list_tasks() if t["due_date"] and start.isoformat() <= t["due_date"] < end.isoformat()]
        lo, hi = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())
        for r in m_reminders.list_reminders():
            at = datetime.fromisoformat(r["due_local"])
            for _ in range(70):
                if at >= hi:
                    break
                if at >= lo:
                    items.append({"kind": "reminder", "date": at.date().isoformat(), "time": at.strftime("%H:%M"),
                                  "title": r["text"], "id": r["id"], "recurring": bool(r["recurrence"])})
                if not r["recurrence"]:
                    break
                at = next_occurrence(at, r["recurrence"])
        if ics.url():
            items += ics.events_between(start, end)
        return jsonify({"items": items, "calendar": ics.status()})

    # ---------------------------------------------------------- research (Milestone 6)
    @app.post("/api/research")
    def research_run():
        d = _body()
        topic = re.sub(r"\s+", " ", str(d.get("topic", ""))).strip()
        if len(topic) < 3:
            abort(400, "Give me a topic of at least 3 characters.")
        if len(topic) > 200:
            abort(400, "Keep the topic under 200 characters.")
        depth = str(d.get("depth") or db.get_setting("research_depth", "normal"))
        return _ndjson(research.run(topic, ollama, depth, bool(d.get("papers"))))

    @app.get("/api/research")
    def research_list():
        return jsonify({"research": research.list_research(request.args.get("q", ""), 50)})

    @app.get("/api/research/<int:rid>")
    def research_get(rid: int):
        try:
            return jsonify(research.get(rid))
        except KeyError:
            abort(404, "Research not found")

    @app.delete("/api/research/<int:rid>")
    def research_delete(rid: int):
        if not research.delete(rid):
            abort(404, "Research not found")
        return jsonify({"ok": True})

    @app.post("/api/research/<int:rid>/note")
    def research_note(rid: int):
        try:
            return jsonify(research.to_note(rid)), 201
        except KeyError:
            abort(404, "Research not found")

    # ---------------------------------------------------------- music
    @app.get("/api/music")
    def music_status():
        return jsonify(music.status())

    @app.post("/api/music/<action>")
    def music_control(action: str):
        if action not in ("play", "pause", "toggle", "next", "previous"):
            abort(404, "Unknown music action")
        try:
            return jsonify(music.control(action))
        except music.MusicError as exc:
            abort(400, str(exc))

    @app.post("/api/music-voice")
    def music_voice():
        """The page tells us when a voice conversation starts/ends so music can pause around it."""
        phase = _body().get("phase")
        done = music.pause_for_voice() if phase == "start" else music.resume_after_voice() if phase == "end" else False
        return jsonify({"ok": True, "changed": done})

    @app.get("/api/spotify/login")
    def spotify_login():
        # Opened by the Connect button as a normal page navigation; the state value protects the callback.
        try:
            return Response(status=302, headers={"Location": music.SPOTIFY.login_url(request.host)})
        except music.MusicError as exc:
            return Response(f"<p>{exc}</p>", status=400, mimetype="text/html")

    @app.get("/api/spotify/callback")
    def spotify_callback():
        err = request.args.get("error")
        try:
            if err:
                raise music.MusicError(f"Spotify said: {err}")
            music.SPOTIFY.finish_login(request.args.get("code", ""), request.args.get("state", ""), request.host)
            msg, ok = "Spotify is connected. You can close this tab and go back to Babilious.", True
        except (music.MusicError, requests_exc) as exc:
            msg, ok = str(exc), False
        color = "#7ee787" if ok else "#ff7b72"
        return Response(f"<!doctype html><meta charset=utf-8><title>Spotify</title><body style='font:16px system-ui;"
                        f"background:#111;color:{color};padding:40px'><p>{_esc(msg)}</p>"
                        "<script>setTimeout(()=>window.close(),2500)</script></body>", mimetype="text/html")

    @app.post("/api/spotify/disconnect")
    def spotify_disconnect():
        music.SPOTIFY.disconnect()
        return jsonify({"ok": True})

    # ---------------------------------------------------------- integrations overview
    @app.get("/api/integrations")
    def integrations():
        import shutil
        installed = [m["name"] for m in ollama.list_models()] if ollama.is_available() else []
        gh_ok = False
        if shutil.which("gh"):
            import subprocess
            try:
                gh_ok = subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=10,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                gh_ok = False
        repo = None
        try:
            repo = gh_tools.remote_repo(Path(config.BASE_DIR))
        except ValueError:
            pass
        return jsonify({
            "spotify": {"client_id": music.SPOTIFY.client_id(), "connected": music.SPOTIFY.connected(),
                        "redirect_uri": music.SPOTIFY.redirect_uri(request.host)},
            "calendar": {**ics.status(), "url_set": bool(ics.url())},
            "github": {"git": bool(shutil.which("git")), "gh": bool(shutil.which("gh")), "gh_signed_in": gh_ok,
                       "babilious_remote": repo},
            "vision": {"model": vision.active_vision_model(),
                       "installed": [n for n in installed if vision.is_vision(n)]},
        })

    @app.post("/api/calendar/refresh")
    def calendar_refresh():
        today = m_reminders.local_now().date()
        ev = ics.events_between(today, date.fromordinal(today.toordinal() + 31), force=True)
        return jsonify({**ics.status(), "upcoming": ev[:10]})

    return app


def _esc(s: str) -> str:
    import html
    return html.escape(s)


try:
    from requests import RequestException as requests_exc
except ImportError:  # pragma: no cover
    requests_exc = OSError
