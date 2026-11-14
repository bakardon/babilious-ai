"""Daily agenda / briefing, weekly review, and searchable history.

Everything here is assembled from local data without the model, so it is
always accurate. The model is only asked to *phrase* it (in chat), and the
plain version is the fallback when Ollama is offline.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .. import db, memory, tasks
from . import habits, reminders
from .timeparse import describe

log = logging.getLogger(__name__)


def _fmt_day(d: str, today: date) -> str:
    dd = date.fromisoformat(d)
    delta = (dd - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if 1 < delta < 7:
        return dd.strftime("%A")
    if delta < 0:
        return f"{-delta} days ago"
    return dd.strftime("%a %d %b")


def agenda(now_local: datetime | None = None) -> dict[str, Any]:
    now_local = now_local or reminders.local_now()
    today = now_local.date()
    end_of_day = datetime.combine(today, datetime.max.time())
    week_end = today + timedelta(days=7)
    focus = next((m["content"] for m in memory.list_memories() if m["kind"] == "focus"), "")
    return {
        "date": today.isoformat(),
        "weekday": today.strftime("%A"),
        "now": now_local.strftime("%H:%M"),
        "overdue": tasks.overdue(today),
        "due_today": tasks.due_between(today, today),
        "upcoming": [t for t in tasks.due_between(today + timedelta(days=1), week_end)],
        "in_progress": [t for t in tasks.open_tasks() if t["status"] == "in_progress"],
        "open_count": len(tasks.open_tasks()),
        "reminders_today": reminders.between(now_local, end_of_day),
        "unacknowledged": [r for r in reminders.list_reminders() if r["status"] == "fired"],
        "habits_due": [h for h in habits.list_habits(today) if h["due_today"]],
        "habits_done": [h for h in habits.list_habits(today) if h["done_today"]],
        "focus": focus,
        "events_today": _events_today(today),
    }


def _events_today(today: date) -> list[dict[str, Any]]:
    from ..integrations import ics
    if not ics.url():
        return []
    try:
        return ics.events_between(today, today + timedelta(days=1))
    except Exception:  # noqa: BLE001  (a calendar problem must never break the briefing)
        log.exception("Calendar events failed")
        return []


def agenda_text(a: dict[str, Any], short: bool = False) -> str:
    today = date.fromisoformat(a["date"])
    lines = []
    if a["overdue"]:
        lines.append("Overdue: " + "; ".join(f"{t['title']} (due {_fmt_day(t['due_date'], today)})"
                                            for t in a["overdue"][:5]))
    if a.get("events_today"):
        lines.append("Calendar today: " + "; ".join(
            (f"{e['title']} at {e['time']}" if e["time"] else f"{e['title']} (all day)") for e in a["events_today"][:6]))
    if a["due_today"]:
        lines.append("Due today: " + "; ".join(t["title"] for t in a["due_today"][:5]))
    if a["reminders_today"]:
        lines.append("Reminders today: " + "; ".join(
            f"{r['text']} at {r['due_local'][11:16]}" for r in a["reminders_today"][:5]))
    if a["unacknowledged"]:
        lines.append("Still waiting for you: " + "; ".join(r["text"] for r in a["unacknowledged"][:3]))
    if a["in_progress"]:
        lines.append("In progress: " + "; ".join(t["title"] for t in a["in_progress"][:4]))
    if a["upcoming"] and not short:
        lines.append("Coming up: " + "; ".join(f"{t['title']} ({_fmt_day(t['due_date'], today)})"
                                              for t in a["upcoming"][:5]))
    if a["habits_due"]:
        lines.append("Habits to do today: " + ", ".join(h["name"] for h in a["habits_due"]))
    if not lines:
        lines.append(f"Nothing scheduled. {a['open_count']} open task{'s' if a['open_count'] != 1 else ''} "
                     "with no deadline." if a["open_count"] else "Nothing scheduled and no open tasks.")
    elif not short and a["open_count"]:
        lines.append(f"{a['open_count']} open tasks in total.")
    if a["focus"] and not short:
        lines.append(a["focus"])
    return "\n".join(lines)


def week_review(now_local: datetime | None = None, days: int = 7) -> dict[str, Any]:
    now_local = now_local or reminders.local_now()
    since_local = now_local - timedelta(days=days)
    since = since_local.astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")
    done = tasks.completed_since(since)
    with db.connect() as conn:
        acts = db.rows(conn.execute("SELECT kind, status, summary FROM activity WHERE ts >= ? ORDER BY id", (since,)))
        n_conv = conn.execute("SELECT COUNT(DISTINCT conversation_id) FROM messages WHERE created_at >= ?",
                              (since,)).fetchone()[0]
        n_notes = conn.execute("SELECT COUNT(*) FROM notes WHERE created_at >= ?", (since,)).fetchone()[0]
        try:
            tool_runs = db.rows(conn.execute(
                "SELECT tool, COUNT(*) AS n FROM actions WHERE status = 'completed' AND created_at >= ? GROUP BY tool "
                "ORDER BY n DESC", (since,)))
        except Exception:  # noqa: BLE001 - actions table missing in very old databases
            tool_runs = []
        logs = db.rows(conn.execute("SELECT h.name, COUNT(*) AS n FROM habit_logs l JOIN habits h ON h.id = l.habit_id "
                                    "WHERE l.day >= ? GROUP BY h.name", (since_local.date().isoformat(),)))
    by_project = Counter(t["project"] or "no project" for t in done)
    return {
        "since": since_local.date().isoformat(), "until": now_local.date().isoformat(),
        "completed": done, "by_project": dict(by_project),
        "created_tasks": sum(1 for a in acts if a["kind"] == "task" and a["summary"].startswith("Created")),
        "reminders_fired": sum(1 for a in acts if a["kind"] == "reminder" and a["summary"].startswith("Reminded")),
        "habit_checkins": {h["name"]: h["n"] for h in logs},
        "habit_streaks": {h["name"]: f"{h['streak']} {h['streak_unit']}" for h in habits.list_habits()},
        "tool_runs": {t["tool"]: t["n"] for t in tool_runs},
        "conversations": n_conv, "notes": n_notes,
        "still_open": len(tasks.open_tasks()),
        "overdue": len(tasks.overdue(now_local.date())),
    }


def week_text(w: dict[str, Any]) -> str:
    lines = [f"Week {w['since']} to {w['until']}:"]
    if w["completed"]:
        lines.append(f"Finished {len(w['completed'])} task(s): " + "; ".join(t["title"] for t in w["completed"][:12]))
        if len(w["by_project"]) > 1:
            lines.append("By project: " + ", ".join(f"{p} {n}" for p, n in w["by_project"].items()))
    else:
        lines.append("No tasks marked done.")
    if w["habit_checkins"]:
        lines.append("Habit check-ins: " + ", ".join(f"{h} {n}x" for h, n in w["habit_checkins"].items()))
    if w["tool_runs"]:
        lines.append("Things I did on the PC: " + ", ".join(f"{k.replace('_', ' ')} {v}x" for k, v in w["tool_runs"].items()))
    extra = []
    if w["notes"]:
        extra.append(f"{w['notes']} note(s)")
    if w["reminders_fired"]:
        extra.append(f"{w['reminders_fired']} reminder(s)")
    if w["conversations"]:
        extra.append(f"{w['conversations']} conversation(s)")
    if extra:
        lines.append("Also: " + ", ".join(extra) + ".")
    lines.append(f"Still open: {w['still_open']} task(s)" + (f", {w['overdue']} overdue." if w["overdue"] else "."))
    return "\n".join(lines)


def search_history(query: str, limit: int = 40) -> list[dict[str, Any]]:
    """Search conversations, notes, tasks, reminders and the activity log."""
    words = [w for w in re.findall(r"[\w'-]{2,}", query.lower()) if w not in ("the", "and", "about", "what", "did")]
    if not words:
        return []
    like = [f"%{w}%" for w in words]
    cond = lambda col: " AND ".join(f"LOWER({col}) LIKE ?" for _ in words)  # noqa: E731
    out: list[dict[str, Any]] = []
    with db.connect() as conn:
        for r in conn.execute(f"SELECT m.id, m.conversation_id, m.role, m.content, m.created_at, c.title FROM messages m "
                              f"JOIN conversations c ON c.id = m.conversation_id WHERE {cond('m.content')} "
                              f"ORDER BY m.id DESC LIMIT ?", (*like, limit)):
            out.append({"kind": "chat", "id": r["conversation_id"], "when": r["created_at"],
                        "title": ("You" if r["role"] == "user" else "Babilious") + f" · {r['title']}",
                        "text": r["content"][:240]})
        task_text = "title || ' ' || notes || ' ' || project"
        for r in conn.execute(f"SELECT * FROM tasks WHERE {cond(task_text)} ORDER BY id DESC LIMIT ?", (*like, limit)):
            out.append({"kind": "task", "id": r["id"], "when": r["completed_at"] or r["created_at"],
                        "title": f"Task ({r['status'].replace('_', ' ')})", "text": r["title"]})
        for r in conn.execute(f"SELECT * FROM reminders WHERE {cond('text')} ORDER BY id DESC LIMIT ?", (*like, limit)):
            out.append({"kind": "reminder", "id": r["id"], "when": r["created_at"],
                        "title": f"Reminder ({r['status']})", "text": r["text"]})
        for r in conn.execute(f"SELECT * FROM activity WHERE {cond('summary')} ORDER BY id DESC LIMIT ?", (*like, limit)):
            out.append({"kind": "activity", "id": r["id"], "when": r["ts"], "title": f"Activity ({r['status']})",
                        "text": r["summary"]})
        research_text = "topic || ' ' || summary"
        for r in conn.execute(f"SELECT * FROM research WHERE {cond(research_text)} ORDER BY id DESC LIMIT ?",
                              (*like, limit)):
            out.append({"kind": "research", "id": r["id"], "when": r["created_at"], "title": f"Research · {r['topic']}",
                        "text": r["summary"][:240]})
    from . import notes
    for n in notes.search(" ".join(words), limit=limit):
        out.append({"kind": "note", "id": n["id"], "when": n["updated_at"], "title": f"Note · {n['title']}",
                    "text": n["body"][:240]})
    out.sort(key=lambda x: x["when"] or "", reverse=True)
    return out[:limit]


__all__ = ["agenda", "agenda_text", "week_review", "week_text", "search_history", "describe"]
