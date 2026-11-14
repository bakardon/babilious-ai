"""Second brain: index folders the user picks, then search them.

- Only folders explicitly added are scanned; drive roots and system folders are refused.
- Every file is indexed by name; supported types also get full-text chunks in SQLite FTS5.
- Re-indexing is incremental (size + modification time), so repeat runs are cheap.
- One background thread at a time; progress is readable through ``job_status()``.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import activity, config, db, documents

log = logging.getLogger(__name__)

MAX_FILES_PER_FOLDER = 20_000
SKIP_DIRS = {
    "node_modules", ".git", ".svn", ".hg", ".venv", "venv", "env", "__pycache__", ".idea", ".vscode",
    "dist", "build", ".cache", ".pytest_cache", ".mypy_cache", "site-packages", "appdata",
    "$recycle.bin", "system volume information", ".next", "target", "bin", "obj",
}
SKIP_FILES = {"desktop.ini", "thumbs.db", ".ds_store"}
FORBIDDEN_PARTS = ["\\appdata"]  # tests clear this: pytest's temp folders live under AppData
MARK_START, MARK_END = "\x02", "\x03"  # snippet highlight markers (the UI turns them into <mark>)


# ================================================================ folders

def _forbidden(path: Path) -> str | None:
    p = str(path).lower().rstrip("\\/")
    if path.parent == path or re.fullmatch(r"[a-z]:", p):
        return "Indexing a whole drive isn't allowed. Pick a specific folder."
    windir = os.environ.get("WINDIR", r"C:\Windows").lower()
    for bad in (windir, r"c:\program files", r"c:\program files (x86)", r"c:\programdata"):
        if p == bad or p.startswith(bad + "\\"):
            return "System folders can't be indexed."
    if any(part in p for part in FORBIDDEN_PARTS):
        return "AppData holds program data, not your documents; it can't be indexed."
    if p == str(Path.home()).lower():
        return "That's your entire user folder. Pick specific folders like Documents or Desktop instead."
    return None


def list_folders() -> list[dict[str, Any]]:
    with db.connect() as conn:
        return db.rows(conn.execute("SELECT * FROM folders ORDER BY path"))


def add_folder(raw_path: str) -> dict[str, Any]:
    raw_path = raw_path.strip().strip('"')
    if not raw_path:
        raise ValueError("Folder path is empty.")
    path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    if not path.is_absolute():
        raise ValueError("Use a full path, e.g. C:\\Users\\you\\Documents\\Notes")
    if not path.is_dir():
        raise ValueError(f"Folder not found: {path}")
    path = path.resolve()
    reason = _forbidden(path)
    if reason:
        raise ValueError(reason)
    for f in list_folders():
        existing = Path(f["path"])
        if path == existing:
            raise ValueError("That folder is already indexed.")
        if existing in path.parents:
            raise ValueError(f"Already covered by {existing}.")
        if path in existing.parents:
            raise ValueError(f"It contains {existing}, which is already indexed. Remove that one first.")
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO folders(path, added_at) VALUES (?, ?)", (str(path), db.now()))
        fid = int(cur.lastrowid)
    activity.record("index", f"Added folder to second brain: {path}")
    return {"id": fid, "path": str(path)}


def remove_folder(folder_id: int) -> bool:
    """Forget a folder's index. Files on disk are never touched."""
    with db.connect() as conn:
        row = conn.execute("SELECT path FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    activity.record("index", f"Removed folder from index (files untouched): {row['path']}")
    return True


def pick_folder_dialog() -> str | None:
    """Open the native Windows folder picker (on this PC) and return the chosen path."""
    if os.name != "nt":
        raise ValueError("The folder picker is only available on Windows; type the path instead.")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$o = New-Object System.Windows.Forms.Form -Property @{TopMost=$true; ShowInTaskbar=$false};"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.Description = 'Choose a folder for Babilious to index';"
        "$d.ShowNewFolderButton = $false;"
        "if ($d.ShowDialog($o) -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                             capture_output=True, text=True, timeout=300,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Couldn't open the folder picker: {exc}") from exc
    return out.stdout.strip() or None


# ================================================================ indexing job

_job_lock = threading.Lock()
_state_lock = threading.Lock()
_state: dict[str, Any] = {"running": False}
_cancel = threading.Event()
_change_counter = 0  # bumps whenever chunks change (used by the embedding cache)


def job_status() -> dict[str, Any]:
    with _state_lock:
        return dict(_state)


def _update(**kw: Any) -> None:
    with _state_lock:
        _state.update(kw)


def changes() -> int:
    return _change_counter


def start_indexing(folder_id: int | None = None, embed_client: Any = None) -> bool:
    """Start a background re-index. Returns False if one is already running."""
    if not _job_lock.acquire(blocking=False):
        return False
    _cancel.clear()
    _update(running=True, started_at=db.now(), finished_at=None, folder="", scanned=0, indexed=0,
            unchanged=0, removed=0, errors=0, current="", message="Starting...", cancelled=False)
    t = threading.Thread(target=_run, args=(folder_id, embed_client), daemon=True, name="indexer")
    t.start()
    return True


def cancel_indexing() -> None:
    _cancel.set()


def run_indexing_now(folder_id: int | None = None, embed_client: Any = None) -> dict[str, Any]:
    """Synchronous variant (used by tests and scripts)."""
    with _job_lock:
        _cancel.clear()
        _update(running=True, scanned=0, indexed=0, unchanged=0, removed=0, errors=0, cancelled=False)
        _run(folder_id, embed_client, lock_held=False)
    return job_status()


def _run(folder_id: int | None, embed_client: Any, lock_held: bool = True) -> None:
    started = time.perf_counter()
    try:
        folders = [f for f in list_folders() if folder_id is None or f["id"] == folder_id]
        for f in folders:
            if _cancel.is_set():
                break
            _index_folder(f)
        if embed_client is not None and not _cancel.is_set():
            from . import semantic
            if semantic.enabled():
                _update(message="Creating semantic embeddings...")
                semantic.embed_missing(embed_client, progress=lambda n: _update(current=f"{n} chunks embedded"))
        s = job_status()
        msg = (f"Indexed {s['indexed']} new/changed, {s['unchanged']} unchanged, {s['removed']} removed, "
               f"{s['errors']} unreadable in {time.perf_counter() - started:.1f}s")
        if _cancel.is_set():
            msg = "Cancelled. " + msg
        _update(message=msg, cancelled=_cancel.is_set())
        activity.record("index", msg, "completed" if not _cancel.is_set() else "info")
    except Exception as exc:  # noqa: BLE001
        log.exception("Indexing failed")
        _update(message=f"Indexing failed: {exc}")
        activity.record("index", f"Indexing failed: {exc}", "failed")
    finally:
        _update(running=False, finished_at=db.now(), current="")
        if lock_held:
            _job_lock.release()


def _walk(root: Path):
    """Yield (path, stat) for files under root, skipping noise, hidden folders and our own data."""
    _own_data = config.DATA_DIR.resolve()  # never index Babilious' own database and logs
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for e in entries:
            name = e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if name.lower() not in SKIP_DIRS and not name.startswith((".", "$"))                             and Path(e.path).resolve() != _own_data:
                        stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    if name.lower() in SKIP_FILES or name.startswith(("~$", ".~")):
                        continue
                    yield Path(e.path), e.stat()
            except OSError:
                continue


def _index_folder(folder: dict[str, Any]) -> None:
    global _change_counter
    root = Path(folder["path"])
    _update(folder=str(root), message=f"Scanning {root}...")
    if not root.is_dir():
        with db.connect() as conn:
            conn.execute("UPDATE folders SET note = 'Folder not found' WHERE id = ?", (folder["id"],))
        _update(errors=job_status()["errors"] + 1)
        return
    with db.connect() as conn:
        known = {r["path"]: (r["id"], r["size"], r["mtime"]) for r in conn.execute(
            "SELECT id, path, size, mtime FROM documents WHERE folder_id = ?", (folder["id"],))}
    seen: set[str] = set()
    note = ""
    for path, st in _walk(root):
        if _cancel.is_set():
            return
        if len(seen) >= MAX_FILES_PER_FOLDER:
            note = f"Stopped at {MAX_FILES_PER_FOLDER} files; pick a smaller folder for full coverage."
            break
        key = str(path)
        seen.add(key)
        s = job_status()
        _update(scanned=s["scanned"] + 1)
        prev = known.get(key)
        if prev and prev[1] == st.st_size and abs(prev[2] - st.st_mtime) < 1e-3:
            _update(unchanged=s["unchanged"] + 1)
            continue
        _update(current=path.name)
        _store(folder["id"], path, st, prev[0] if prev else None)
        _change_counter += 1
    removed = [known[p][0] for p in known.keys() - seen] if not note else []
    if removed:
        with db.connect() as conn:
            conn.executemany("DELETE FROM documents WHERE id = ?", [(i,) for i in removed])
        _change_counter += 1
        _update(removed=job_status()["removed"] + len(removed))
    with db.connect() as conn:
        counts = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(has_text), 0) FROM documents WHERE folder_id = ?",
            (folder["id"],)).fetchone()
        conn.execute(
            "UPDATE folders SET last_indexed_at = ?, file_count = ?, content_count = ?, note = ? WHERE id = ?",
            (db.now(), counts[0], counts[1], note, folder["id"]))


def _store(folder_id: int, path: Path, st: os.stat_result, doc_id: int | None) -> None:
    text, error = "", ""
    if documents.can_read(path):
        try:
            text = documents.extract_text(path)
        except Exception as exc:  # noqa: BLE001 - any broken file is just recorded
            error = str(exc)[:200] or exc.__class__.__name__
    s = job_status()
    if error:
        _update(errors=s["errors"] + 1)
    else:
        _update(indexed=s["indexed"] + 1)
    fields = (folder_id, str(path), path.name, path.suffix.lower(), st.st_size, st.st_mtime, db.now(),
              int(bool(text)), len(text), documents.summary(text) if text else "",
              " ".join(documents.keywords(text)) if text else "",
              "|".join(documents.links(text)) if text else "", error)
    with db.connect() as conn:
        if doc_id:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute(
                "UPDATE documents SET folder_id=?, path=?, name=?, ext=?, size=?, mtime=?, indexed_at=?, "
                "has_text=?, chars=?, summary=?, keywords=?, links=?, error=? WHERE id=?", (*fields, doc_id))
        else:
            doc_id = int(conn.execute(
                "INSERT INTO documents(folder_id, path, name, ext, size, mtime, indexed_at, has_text, chars, "
                "summary, keywords, links, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", fields).lastrowid)
        if text:
            conn.executemany("INSERT INTO chunks(doc_id, ord, text) VALUES (?, ?, ?)",
                             [(doc_id, i, c) for i, c in enumerate(documents.chunk(text))])


# ================================================================ search

_TOKEN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_]*")
# Conversational words that should never pull documents into a chat.
CHAT_STOP = set("""
today tomorrow yesterday now doing going thing things think know want need good okay ok yeah yes hey
hello hi thanks thank please help tell said say just really much many well lot feel feeling right
babilious abubakar something anything everything someone time day week explain show give find look
""".split())


def query_tokens(text: str, chat: bool = False) -> list[str]:
    out: list[str] = []
    for t in _TOKEN.findall(text.lower()):
        if len(t) >= 2 and t not in documents.STOP and t not in out and not (chat and t in CHAT_STOP):
            out.append(t)
    return out[:12]


def _fts_query(tokens: list[str]) -> str:
    return " OR ".join(f'"{t}"' for t in tokens)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _doc_out(d: dict[str, Any]) -> dict[str, Any]:
    return {"id": d["id"], "name": d["name"], "path": d["path"], "ext": d["ext"], "size": d["size"],
            "modified": _iso(d["mtime"]), "has_text": bool(d["has_text"]), "summary": d["summary"],
            "error": d["error"]}


def search(query: str = "", ext: str | None = None, folder_id: int | None = None,
           sort: str = "relevance", path_contains: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    """Search indexed files by name and content.

    With no query, returns files sorted by modification time (newest first).
    """
    tokens = query_tokens(query)
    filters, params = [], []
    if ext:
        exts = [e if e.startswith(".") else "." + e for e in ext.lower().replace(",", " ").split()]
        filters.append(f"d.ext IN ({','.join('?' * len(exts))})")
        params += exts
    if folder_id:
        filters.append("d.folder_id = ?")
        params.append(folder_id)
    if path_contains:
        filters.append("d.path LIKE ?")
        params.append(f"%{path_contains}%")
    where = (" AND " + " AND ".join(filters)) if filters else ""

    with db.connect() as conn:
        if not tokens:
            docs = db.rows(conn.execute(
                f"SELECT d.* FROM documents d WHERE 1=1{where} ORDER BY d.mtime DESC LIMIT ?", (*params, limit)))
            return [_doc_out(d) for d in docs]

        results: dict[int, dict[str, Any]] = {}
        # 1. Name matches: every token must appear in the file name.
        name_sql = " AND ".join("d.name LIKE ?" for _ in tokens)
        for d in conn.execute(f"SELECT d.* FROM documents d WHERE {name_sql}{where} LIMIT 200",
                              (*[f"%{t}%" for t in tokens], *params)):
            results[d["id"]] = {**_doc_out(dict(d)), "score": -50.0, "match": "name", "snippet": ""}
        # 2. Content matches through FTS5 (bm25: lower is better), best chunk per file.
        try:
            hits = conn.execute(
                "SELECT c.doc_id, bm25(chunks_fts) AS score, "
                f"snippet(chunks_fts, 0, '{MARK_START}', '{MARK_END}', ' ... ', 20) AS snip "
                "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? ORDER BY score LIMIT 300", (_fts_query(tokens),)).fetchall()
        except Exception as exc:  # noqa: BLE001 - a malformed query must not crash search
            log.warning("FTS query failed: %s", exc)
            hits = []
        best: dict[int, tuple[float, str]] = {}
        for h in hits:
            if h["doc_id"] not in best:
                best[h["doc_id"]] = (h["score"], h["snip"])
        if best:
            ids = list(best)
            docs = conn.execute(
                f"SELECT d.* FROM documents d WHERE d.id IN ({','.join('?' * len(ids))}){where}",
                (*ids, *params)).fetchall()
            for d in docs:
                score, snip = best[d["id"]]
                if d["id"] in results:
                    results[d["id"]].update(score=-50.0 + score, match="name+content", snippet=snip)
                else:
                    results[d["id"]] = {**_doc_out(dict(d)), "score": score, "match": "content", "snippet": snip}
    items = list(results.values())
    if sort == "modified":
        items.sort(key=lambda r: r["modified"], reverse=True)
    else:
        items.sort(key=lambda r: r["score"])
    return items[:limit]


def retrieve_for_chat(question: str, limit: int = 2, max_chars: int = 900,
                      embed_client: Any = None) -> list[dict[str, Any]]:
    """Chunks from indexed files that clearly relate to the question.

    Strict on purpose: every snippet costs CPU time in the prompt, and an
    irrelevant one makes a small model ramble.
    """
    tokens = query_tokens(question, chat=True)
    if not tokens:
        return []
    # One distinctive word (e.g. "radar") is enough; otherwise two must match.
    need = 1 if len(tokens) == 1 and len(tokens[0]) >= 4 else 2
    with db.connect() as conn:
        try:
            hits = db.rows(conn.execute(
                "SELECT c.id AS chunk_id, c.doc_id, c.text, bm25(chunks_fts) AS score, d.name, d.path "
                "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid JOIN documents d ON d.id = c.doc_id "
                "WHERE chunks_fts MATCH ? ORDER BY score LIMIT 40", (_fts_query(tokens),)))
        except Exception:  # noqa: BLE001
            hits = []
    candidates = []
    for h in hits:
        low = (h["text"] + " " + h["name"]).lower()
        coverage = sum(1 for t in tokens if t[:5] in low)
        if coverage >= need:
            candidates.append((-coverage, h["score"], h))
    if embed_client is not None:
        from . import semantic
        if semantic.enabled():
            for sim, h in semantic.search(embed_client, question, limit=4):
                if sim >= semantic.MIN_SIMILARITY:
                    candidates.append((-len(tokens), -sim, h))
    candidates.sort(key=lambda c: (c[0], c[1]))
    out, used = [], set()
    for _, _, h in candidates:
        if h["doc_id"] in used:
            continue
        used.add(h["doc_id"])
        text = h["text"] if len(h["text"]) <= max_chars else h["text"][:max_chars].rsplit(" ", 1)[0] + " ..."
        out.append({"doc_id": h["doc_id"], "name": h["name"], "path": h["path"], "text": text})
        if len(out) >= limit:
            break
    return out


def find_document(name_query: str) -> dict[str, Any] | None:
    """Best document whose file name matches a phrase like 'project title pdf'."""
    words = [w for w in re.findall(r"[\w.-]+", name_query.lower()) if w not in documents.STOP and len(w) > 1]
    if not words:
        return None
    with db.connect() as conn:
        rows = db.rows(conn.execute("SELECT * FROM documents WHERE has_text = 1"))
    best, best_score = None, 0.0
    for d in rows:
        name = d["name"].lower()
        stem = Path(name).stem.replace("_", " ").replace("-", " ")
        score = sum(1 for w in words if w in name or w in stem) / len(words)
        if name in name_query.lower() or stem in name_query.lower():
            score += 1
        if score > best_score or (score == best_score and best and d["mtime"] > best["mtime"]):
            best, best_score = d, score
    return best if best_score >= 0.5 else None


def document_text(doc_id: int, max_chars: int | None = None) -> str:
    """Fresh text from disk when possible, else the indexed chunks."""
    with db.connect() as conn:
        d = conn.execute("SELECT path FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not d:
            raise ValueError("Document not found")
        try:
            text = documents.extract_text(Path(d["path"]))
        except Exception:  # noqa: BLE001
            parts = [r["text"] for r in conn.execute(
                "SELECT text FROM chunks WHERE doc_id = ? ORDER BY ord", (doc_id,))]
            text = "\n".join(parts)
    return text if max_chars is None else text[:max_chars]


def get_document(doc_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        d = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not d:
            raise ValueError("Document not found")
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,)).fetchone()[0]
        preview = "\n".join(r["text"] for r in conn.execute(
            "SELECT text FROM chunks WHERE doc_id = ? ORDER BY ord LIMIT 2", (doc_id,)))
    d = dict(d)
    return {**_doc_out(d), "keywords": d["keywords"].split(), "chunks": n_chunks,
            "preview": preview[:2500], "related": related(doc_id)}


def stats() -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS files, COALESCE(SUM(has_text),0) AS with_text, "
            "COALESCE(SUM(CASE WHEN error != '' THEN 1 ELSE 0 END),0) AS unreadable FROM documents").fetchone()
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        emb = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        by_ext = db.rows(conn.execute(
            "SELECT ext, COUNT(*) AS n FROM documents GROUP BY ext ORDER BY n DESC LIMIT 12"))
    return {"files": row["files"], "with_text": row["with_text"], "unreadable": row["unreadable"],
            "chunks": chunks, "embedded_chunks": emb, "by_ext": by_ext, "folders": len(list_folders())}


# ================================================================ connections

def _doc_terms(d: dict[str, Any]) -> set[str]:
    return set(d["keywords"].split())


def _connected(a: dict[str, Any], b: dict[str, Any], ka: set[str], kb: set[str]) -> tuple[int, str] | None:
    stem_a, stem_b = Path(a["name"]).stem.lower(), Path(b["name"]).stem.lower()
    if stem_b in a["links"].split("|") or stem_a in b["links"].split("|"):
        return 10, "link"
    shared = len(ka & kb)
    return (shared, "topic") if shared >= 3 else None


def related(doc_id: int, limit: int = 8) -> list[dict[str, Any]]:
    with db.connect() as conn:
        me = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not me or not me["has_text"]:
            return []
        others = db.rows(conn.execute(
            "SELECT id, name, path, keywords, links FROM documents WHERE has_text = 1 AND id != ? "
            "ORDER BY mtime DESC LIMIT 5000", (doc_id,)))
    me = dict(me)
    km = _doc_terms(me)
    scored = []
    for o in others:
        c = _connected(me, o, km, _doc_terms(o))
        if c:
            shared = sorted(km & _doc_terms(o))
            scored.append({"id": o["id"], "name": o["name"], "path": o["path"], "strength": c[0],
                           "kind": c[1], "shared": shared[:6]})
    scored.sort(key=lambda r: r["strength"], reverse=True)
    return scored[:limit]


def graph(limit: int = 150) -> dict[str, Any]:
    with db.connect() as conn:
        docs = db.rows(conn.execute(
            "SELECT d.id, d.name, d.path, d.ext, d.keywords, d.links, f.path AS folder FROM documents d "
            "JOIN folders f ON f.id = d.folder_id WHERE d.has_text = 1 ORDER BY d.mtime DESC LIMIT ?", (limit,)))
    terms = [_doc_terms(d) for d in docs]
    edges = []
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            c = _connected(docs[i], docs[j], terms[i], terms[j])
            if c:
                edges.append({"source": docs[i]["id"], "target": docs[j]["id"], "weight": c[0], "kind": c[1]})
    nodes = [{"id": d["id"], "name": d["name"], "ext": d["ext"], "folder": d["folder"],
              "keywords": d["keywords"].split()[:5]} for d in docs]
    return {"nodes": nodes, "edges": edges}
