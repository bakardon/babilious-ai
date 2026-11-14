"""Habit tracking: daily / weekday / N-times-a-week habits with streaks."""
from __future__ import annotations

import difflib
import re
from datetime import date, timedelta
from typing import Any

from .. import activity, db

SCHEDULES = ("daily", "weekdays") + tuple(f"{n}x_week" for n in range(1, 7))


def _today() -> date:
    from .reminders import local_now
    return local_now().date()


def create(name: str, schedule: str = "daily") -> dict[str, Any]:
    name = " ".join(name.strip().rstrip(".").split())
    if not 2 <= len(name) <= 60:
        raise ValueError("Habit name: 2-60 characters.")
    if schedule not in SCHEDULES:
        raise ValueError(f"schedule must be one of {', '.join(SCHEDULES)}")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM habits WHERE name = ? AND archived = 0", (name,)).fetchone():
            raise ValueError(f"You're already tracking \"{name}\".")
        conn.execute("DELETE FROM habits WHERE name = ? AND archived = 1", (name,))
        hid = conn.execute("INSERT INTO habits(name, schedule, created_at) VALUES (?, ?, ?)",
                           (name, schedule, db.now())).lastrowid
    activity.record("habit", f"Started tracking: {name}")
    return get(int(hid))


def _due_on(schedule: str, d: date) -> bool:
    return schedule != "weekdays" or d.weekday() < 5


def _stats(h: dict[str, Any], days: set[str], today: date) -> dict[str, Any]:
    sched = h["schedule"]
    done_today = today.isoformat() in days
    if sched.endswith("x_week"):
        target = int(sched[0])
        week_start = today - timedelta(days=today.weekday())
        this_week = sum(1 for i in range(7) if (week_start + timedelta(days=i)).isoformat() in days)
        streak, wk = 0, week_start - timedelta(days=7)   # completed weeks in a row (+ this one if met)
        while sum(1 for i in range(7) if (wk + timedelta(days=i)).isoformat() in days) >= target:
            streak += 1
            wk -= timedelta(days=7)
        if this_week >= target:
            streak += 1
        return {"done_today": done_today, "streak": streak, "streak_unit": "weeks",
                "progress": f"{this_week}/{target} this week", "due_today": this_week < target}
    # Daily-style: count back over scheduled days; today only breaks the streak once it's over.
    streak, d = 0, today if done_today else today - timedelta(days=1)
    while True:
        if not _due_on(sched, d):
            d -= timedelta(days=1)
            continue
        if d.isoformat() in days:
            streak += 1
            d -= timedelta(days=1)
        else:
            break
    last7 = sum(1 for i in range(7) if (today - timedelta(days=i)).isoformat() in days)
    return {"done_today": done_today, "streak": streak, "streak_unit": "days", "progress": f"{last7}/7 days",
            "due_today": _due_on(sched, today) and not done_today}


def list_habits(today: date | None = None) -> list[dict[str, Any]]:
    today = today or _today()
    with db.connect() as conn:
        habits = db.rows(conn.execute("SELECT * FROM habits WHERE archived = 0 ORDER BY id"))
        logs = db.rows(conn.execute("SELECT habit_id, day FROM habit_logs WHERE day >= ?",
                                    ((today - timedelta(days=400)).isoformat(),)))
    by = {}
    for lg in logs:
        by.setdefault(lg["habit_id"], set()).add(lg["day"])
    out = []
    for h in habits:
        days = by.get(h["id"], set())
        recent = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
        out.append({**h, **_stats(h, days, today), "last14": [d in days for d in recent]})
    return out


def get(hid: int) -> dict[str, Any]:
    for h in list_habits():
        if h["id"] == hid:
            return h
    raise ValueError("Habit not found")


def log(hid: int, day: date | None = None, done: bool = True) -> dict[str, Any]:
    day = day or _today()
    if day > _today():
        raise ValueError("Can't check in for the future.")
    h = get(hid)
    with db.connect() as conn:
        if done:
            conn.execute("INSERT OR IGNORE INTO habit_logs(habit_id, day) VALUES (?, ?)", (hid, day.isoformat()))
        else:
            conn.execute("DELETE FROM habit_logs WHERE habit_id = ? AND day = ?", (hid, day.isoformat()))
    if done:
        activity.record("habit", f"Checked in: {h['name']}")
    return get(hid)


def archive(hid: int) -> bool:
    with db.connect() as conn:
        return conn.execute("UPDATE habits SET archived = 1 WHERE id = ?", (hid,)).rowcount > 0


def find(text: str) -> dict[str, Any] | None:
    """Best habit for a phrase like 'I went to the gym' or 'did my reading'."""
    habits = list_habits()
    if not habits:
        return None
    from ..documents import STOP
    noise = STOP | {"went", "did", "just", "today", "minutes", "minute", "hour", "hours", "for", "some", "while"}
    low = text.lower()
    words = set(re.findall(r"[a-z]{3,}", low)) - noise
    best, score = None, 0.0
    for h in habits:
        hw = set(re.findall(r"[a-z]{3,}", h["name"].lower())) - noise
        if not hw or not words:
            continue
        overlap = len(words & hw) / max(len(hw), 1)
        fuzzy = max((difflib.SequenceMatcher(None, w, x).ratio() for w in words for x in hw), default=0)
        s = max(overlap, fuzzy * 0.9)
        if h["name"].lower() in low:
            s = 1.0
        if s > score:
            best, score = h, s
    return best if score >= 0.75 else None
