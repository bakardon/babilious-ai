"""Local task list (SQLite)."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from .db import connect, now, rows

STATUSES = ("open", "in_progress", "done")


def _valid_date(value: str | None) -> str | None:
    if not value:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("due_date must be YYYY-MM-DD")
    date.fromisoformat(value)  # raises on impossible dates
    return value


def list_tasks(status: str | None = None) -> list[dict[str, Any]]:
    order = ("ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'open' THEN 1 ELSE 2 END, "
             "priority ASC, COALESCE(due_date, '9999-12-31') ASC, id DESC")
    with connect() as conn:
        if status:
            return rows(conn.execute(f"SELECT * FROM tasks WHERE status = ? {order}", (status,)))
        return rows(conn.execute(f"SELECT * FROM tasks {order}"))


def open_tasks() -> list[dict[str, Any]]:
    return [t for t in list_tasks() if t["status"] != "done"]


def current_task() -> dict[str, Any] | None:
    tasks = open_tasks()
    return tasks[0] if tasks else None


def create_task(title: str, notes: str = "", priority: int = 2,
                project: str = "", due_date: str | None = None) -> int:
    title = title.strip()
    if not title:
        raise ValueError("Task title cannot be empty.")
    priority = int(priority)
    if priority not in (1, 2, 3):
        raise ValueError("priority must be 1 (high), 2 (normal) or 3 (low)")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(title, notes, priority, project, due_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title[:300], notes, priority, project.strip(), _valid_date(due_date), now()),
        )
        return int(cur.lastrowid)


def update_task(task_id: int, **fields: Any) -> bool:
    allowed = {k: v for k, v in fields.items()
               if k in {"title", "notes", "status", "priority", "project", "due_date"}}
    if "status" in allowed:
        if allowed["status"] not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        allowed["completed_at"] = now() if allowed["status"] == "done" else None
    if "priority" in allowed and int(allowed["priority"]) not in (1, 2, 3):
        raise ValueError("priority must be 1, 2 or 3")
    if "due_date" in allowed:
        allowed["due_date"] = _valid_date(allowed["due_date"])
    if "title" in allowed and not str(allowed["title"]).strip():
        raise ValueError("Task title cannot be empty.")
    if not allowed:
        return False
    sets = ", ".join(f"{k} = ?" for k in allowed)
    with connect() as conn:
        cur = conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*allowed.values(), task_id))
        return cur.rowcount > 0


def delete_task(task_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount > 0


def task_context(limit: int = 6) -> str:
    tasks = open_tasks()[:limit]
    if not tasks:
        return ""
    lines = []
    for t in tasks:
        due = f", due {t['due_date']}" if t["due_date"] else ""
        lines.append(f"- {t['title']} [{t['status'].replace('_', ' ')}{due}]")
    return "\n".join(lines)


# ---------------------------------------------------------------- Milestone 5: deadlines & projects
def overdue(today: date) -> list[dict[str, Any]]:
    return [t for t in open_tasks() if t["due_date"] and t["due_date"] < today.isoformat()]


def due_between(start: date, end: date) -> list[dict[str, Any]]:
    """Open tasks due in [start, end]."""
    s, e = start.isoformat(), end.isoformat()
    return [t for t in open_tasks() if t["due_date"] and s <= t["due_date"] <= e]


def completed_since(since_iso: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute("SELECT * FROM tasks WHERE status = 'done' AND completed_at >= ? "
                                 "ORDER BY completed_at", (since_iso,)))


def projects_summary(today: date) -> list[dict[str, Any]]:
    """Per project: progress, next deadline, overdue count."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in list_tasks():
        groups.setdefault(t["project"] or "", []).append(t)
    out = []
    for name, items in groups.items():
        open_ = [t for t in items if t["status"] != "done"]
        dues = sorted(t["due_date"] for t in open_ if t["due_date"])
        out.append({"project": name or "No project", "key": name, "total": len(items),
                    "done": len(items) - len(open_), "open": len(open_),
                    "overdue": sum(1 for d in dues if d < today.isoformat()),
                    "next_due": dues[0] if dues else None,
                    "next_task": next((t["title"] for t in sorted(open_, key=lambda t: (t["due_date"] or "9999", t["priority"]))), None)})
    out.sort(key=lambda p: (p["key"] == "", p["open"] == 0, p["next_due"] or "9999", p["project"].lower()))
    return out


def find_task(text: str, include_done: bool = False) -> dict[str, Any] | None:
    """Best open task matching a phrase ('the GitHub portfolio one')."""
    import difflib
    import re as _re
    words = set(_re.findall(r"[a-z0-9]{3,}", text.lower())) - {"the", "task", "one", "about", "that", "with", "for", "and", "finished", "done"}
    if not words:
        return None
    pool = list_tasks() if include_done else open_tasks()
    best, score = None, 0.0
    for t in pool:
        tw = set(_re.findall(r"[a-z0-9]{3,}", t["title"].lower()))
        if not tw:
            continue
        overlap = len(words & tw) / len(words)
        fuzzy = difflib.SequenceMatcher(None, " ".join(sorted(words)), " ".join(sorted(tw))).ratio()
        s = max(overlap, fuzzy * 0.8)
        if s > score:
            best, score = t, s
    return best if score >= 0.6 else None
