"""Quick notes (SQLite + full-text search). Babilious can use relevant notes in chat."""
from __future__ import annotations

import re
from typing import Any

from .. import activity, db


def _title_from(body: str) -> str:
    first = body.strip().splitlines()[0] if body.strip() else ""
    first = re.sub(r"^#+\s*", "", first)
    return first if len(first) <= 60 else first[:57].rstrip() + "..."


def _tags(body: str, tags: str = "") -> str:
    found = set(t.lower() for t in re.findall(r"(?<!\w)#([a-zA-Z][\w-]{1,30})", body))
    found |= {t.strip().lstrip("#").lower() for t in tags.replace(",", " ").split() if t.strip()}
    return " ".join(sorted(found))


def create(body: str, title: str = "", tags: str = "") -> dict[str, Any]:
    body = body.strip()
    if not body:
        raise ValueError("The note is empty.")
    ts = db.now()
    with db.connect() as conn:
        nid = conn.execute("INSERT INTO notes(title, body, tags, created_at, updated_at) VALUES (?,?,?,?,?)",
                           (title.strip() or _title_from(body), body, _tags(body, tags), ts, ts)).lastrowid
    activity.record("note", f"Saved a note: {_title_from(body)}")
    return get(int(nid))


def get(nid: int) -> dict[str, Any]:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone()
    if not r:
        raise ValueError("Note not found")
    return dict(r)


def update(nid: int, body: str | None = None, title: str | None = None, tags: str | None = None) -> dict[str, Any]:
    n = get(nid)
    body = n["body"] if body is None else body.strip()
    if not body:
        raise ValueError("The note is empty.")
    if title is None:
        # Keep a hand-written title; refresh one that was derived from the old first line.
        title = _title_from(body) if n["title"] == _title_from(n["body"]) else n["title"]
    if tags is None:
        # Tags written in the old body follow the new body; hand-added ones are kept.
        old_inline = set(_tags(n["body"]).split())
        tags = " ".join(t for t in n["tags"].split() if t not in old_inline)
    with db.connect() as conn:
        conn.execute("UPDATE notes SET body = ?, title = ?, tags = ?, updated_at = ? WHERE id = ?",
                     (body, title.strip() or _title_from(body), _tags(body, tags), db.now(), nid))
    return get(nid)


def delete(nid: int) -> bool:
    with db.connect() as conn:
        return conn.execute("DELETE FROM notes WHERE id = ?", (nid,)).rowcount > 0


def search(query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    with db.connect() as conn:
        words = re.findall(r"[a-zA-Z0-9]{2,}", query)
        if not words:
            return db.rows(conn.execute("SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,)))
        fts = " OR ".join(f'"{w}"' for w in words)
        return db.rows(conn.execute(
            "SELECT n.*, snippet(notes_fts, 1, '\x02', '\x03', ' ... ', 16) AS snippet FROM notes_fts "
            "JOIN notes n ON n.id = notes_fts.rowid WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT ?",
            (fts, limit)))


def relevant(question: str, limit: int = 2) -> list[tuple[str, str]]:
    """Notes clearly related to a chat message (needs 2 matching words, like file retrieval)."""
    from ..indexer import query_tokens
    tokens = query_tokens(question, chat=True)
    if len(tokens) < 2 and not (tokens and len(tokens[0]) >= 5):
        return []
    need = 1 if len(tokens) == 1 else 2
    out = []
    for n in search(" ".join(tokens), limit=8):
        text = (n["title"] + " " + n["body"]).lower()
        if sum(1 for t in tokens if t[:5] in text) >= need:
            out.append((f"note: {n['title']}", n["body"][:700]))
        if len(out) >= limit:
            break
    return out
