"""Conversation history (short-term) and long-term memories."""
from __future__ import annotations

from typing import Any

from .db import connect, now, rows

MEMORY_KINDS = ("fact", "preference", "goal", "project", "milestone", "focus", "person")


# ---------------------------------------------------------------- conversations

def create_conversation(title: str = "New conversation") -> int:
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations(title, created_at, updated_at) VALUES (?, ?, ?)",
            (title[:120], ts, ts),
        )
        return int(cur.lastrowid)


def conversation_exists(conversation_id: int) -> bool:
    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone() is not None


def list_conversations(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute(
            "SELECT c.id, c.title, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
            "FROM conversations c ORDER BY c.updated_at DESC, c.id DESC LIMIT ?",
            (limit,),
        ))


def get_messages(conversation_id: int, limit: int | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if limit:
            result = rows(conn.execute(
                "SELECT * FROM (SELECT * FROM messages WHERE conversation_id = ? "
                "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (conversation_id, limit),
            ))
        else:
            result = rows(conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ))
    return result


def add_message(conversation_id: int, role: str, content: str,
                model: str | None = None, source: str | None = None) -> int:
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages(conversation_id, role, content, model, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, role, content, model, source, ts),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, conversation_id))
        # Name the conversation after its first user message.
        if role == "user":
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = 'New conversation'",
                (_title_from(content), conversation_id),
            )
        return int(cur.lastrowid)


def _title_from(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 60 else text[:57].rstrip() + "..."


def delete_conversation(conversation_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------- long-term memory

def list_memories(query: str = "") -> list[dict[str, Any]]:
    with connect() as conn:
        if query:
            return rows(conn.execute(
                "SELECT * FROM memories WHERE content LIKE ? ORDER BY pinned DESC, id DESC",
                (f"%{query}%",),
            ))
        return rows(conn.execute("SELECT * FROM memories ORDER BY pinned DESC, id DESC"))


def add_memory(content: str, kind: str = "fact", source: str = "user", pinned: bool = False) -> int:
    content = content.strip()
    if not content:
        raise ValueError("Memory content cannot be empty.")
    if kind not in MEMORY_KINDS:
        kind = "fact"
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO memories(kind, content, source, pinned, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, content, source, int(pinned), ts, ts),
        )
        return int(cur.lastrowid)


def update_memory(memory_id: int, **fields: Any) -> bool:
    allowed = {k: v for k, v in fields.items() if k in {"content", "kind", "pinned"}}
    if "kind" in allowed and allowed["kind"] not in MEMORY_KINDS:
        raise ValueError(f"Unknown memory kind: {allowed['kind']}")
    if "content" in allowed and not str(allowed["content"]).strip():
        raise ValueError("Memory content cannot be empty.")
    if "pinned" in allowed:
        allowed["pinned"] = int(bool(allowed["pinned"]))
    if not allowed:
        return False
    sets = ", ".join(f"{k} = ?" for k in allowed)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE memories SET {sets}, updated_at = ? WHERE id = ?",
            (*allowed.values(), now(), memory_id),
        )
        return cur.rowcount > 0


def delete_memory(memory_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0


def memory_context(limit: int = 12) -> str:
    """Compact long-term memory block for the system prompt."""
    with connect() as conn:
        items = rows(conn.execute(
            "SELECT kind, content FROM memories ORDER BY pinned DESC, id DESC LIMIT ?", (limit,)
        ))
    if not items:
        return ""
    return "\n".join(f"- ({m['kind']}) {m['content']}" for m in items)
