"""Resource usage and the health check."""
from __future__ import annotations

import importlib.util
import os
import socket
from typing import Any

import psutil

from . import config, db, knowledge, models
from .llm import OllamaClient, OllamaError, OllamaUnavailable

_proc = psutil.Process(os.getpid())
psutil.cpu_percent(None)  # prime the counter so the first reading is meaningful


def resources(client: OllamaClient) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    loaded = client.running_models()
    return {
        "cpu_percent": psutil.cpu_percent(None),
        "ram_percent": vm.percent,
        "ram_used_gb": round((vm.total - vm.available) / 1e9, 1),
        "ram_total_gb": round(vm.total / 1e9, 1),
        "app_ram_mb": round(_proc.memory_info().rss / 1e6),
        "loaded_models": [{"name": m["name"], "ram_gb": round(m["size"] / 1e9, 1)} for m in loaded],
    }


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _check(name: str, ok: bool | None, detail: str, milestone: int | None = None) -> dict[str, Any]:
    status = "ok" if ok else ("pending" if ok is None else "error")
    return {"name": name, "status": status, "detail": detail, "milestone": milestone}


def health(client: OllamaClient, check_internet: bool = True) -> list[dict[str, Any]]:
    checks = []
    version = client.version()
    checks.append(_check("Ollama server", bool(version),
                         f"Running (v{version}) at {client.host}" if version
                         else "Not reachable. Start Ollama or run `ollama serve`."))
    active = models.active_model()
    if version:
        try:
            names = [m["name"] for m in client.list_models()]
            ok = any(n == active or n == f"{active}:latest" for n in names)
            checks.append(_check("Active model", ok, f"{active} is installed" if ok
                                 else f"{active} is not installed. Installed: {', '.join(names) or 'none'}"))
        except (OllamaUnavailable, OllamaError) as exc:
            checks.append(_check("Active model", False, str(exc)))
    else:
        checks.append(_check("Active model", False, f"{active} (cannot verify while Ollama is down)"))

    try:
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
            n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        checks.append(_check("Database", True, f"SQLite OK at {config.DB_PATH} ({n} messages)"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("Database", False, str(exc)))

    files = knowledge.file_list()
    empty = [f["name"] for f in files if f["empty"]]
    checks.append(_check("Knowledge base", bool(files),
                         f"{len(files)} files" + (f" ({', '.join(empty)} empty)" if empty else "")))

    from . import indexer
    st = indexer.stats()
    checks.append(_check("Second brain", True if st["folders"] else None,
                         f"{st['folders']} folders, {st['files']} files, {st['with_text']} readable, "
                         f"{st['chunks']} chunks, {st['embedded_chunks']} embedded" if st["folders"]
                         else "No folders indexed yet (add some in Files)"))
    for mod, label in (("pypdf", "PDF reader"), ("docx", "DOCX reader")):
        checks.append(_check(label, _has(mod), "Installed" if _has(mod) else f"Missing: pip install -r requirements.txt"))

    from .voice import available as voice_available
    va = voice_available()
    if not va["stt"]:
        checks.append(_check("Speech recognition", False, "faster-whisper not installed: pip install -r requirements.txt"))
    else:
        from .voice.engine import engine as voice_engine
        vs = voice_engine().status()
        stt = vs["stt"]
        checks.append(_check(
            "Speech recognition", True if stt["downloaded"] else None,
            f"faster-whisper {stt['model']}: " + ("loaded" if stt["loaded"] else "ready (loads on first use)"
                                                  if stt["downloaded"] else "model downloads on first use")))
        tts = vs["tts"]
        checks.append(_check("Text-to-speech", True if tts["ready"] else None,
                             f"Piper voice {tts['voice']} " + ("installed" if tts["ready"]
                             else "not downloaded (Settings > Voice); browser voices used meanwhile")))
        s = vs["settings"]
        wake_desc = (f"\"{s['wake_word']}\" (whisper spotting)" if s["wake_engine"] == "whisper"
                     else f"openWakeWord {s['oww_model']}" + ("" if va["openwakeword"] else " (not installed)"))
        checks.append(_check("Wake word", True, wake_desc))
    checks.append(_check("Microphone", None,
                         "Checked in the browser when you first use the mic (Settings > Voice shows the devices)"))
    checks.append(_check("Browser automation", _has("playwright") or None,
                         "Playwright installed" if _has("playwright") else "Not installed yet", 4))
    # Milestone 6 integrations: all optional, so "not set up" is pending, never an error.
    from .integrations import ics, music, vision
    checks.append(_check("Spotify", True if music.SPOTIFY.connected() else None,
                         "Connected through your own Spotify app" if music.SPOTIFY.connected()
                         else "Not connected (optional). Windows media controls still work: Settings > Integrations"))
    checks.append(_check("Calendar feed", (not ics.status()["error"]) if ics.url() else None,
                         ("Linked (read-only)" + (f": {ics.status()['error']}" if ics.status()["error"] else ""))
                         if ics.url() else "No calendar linked (optional): Settings > Integrations"))
    vm = vision.active_vision_model()
    checks.append(_check("Vision model", True if vm else None,
                         f"{vm} (used only when you ask him to describe something)" if vm
                         else "Off (optional). OCR reads text on screen; download moondream for descriptions"))
    import shutil
    checks.append(_check("Git", bool(shutil.which("git")) or None,
                         "Installed" + (", GitHub CLI too" if shutil.which("gh") else "") if shutil.which("git")
                         else "Not installed (optional, for project status and commits)"))
    if check_internet:
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=1.5).close()
            checks.append(_check("Internet (optional)", True, "Online"))
        except OSError:
            checks.append(_check("Internet (optional)", None, "Offline; core features still work"))
    return checks
