"""Reminders and the in-app notification inbox, plus the background scheduler."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import activity, db
from . import notify
from .timeparse import describe, next_occurrence

log = logging.getLogger(__name__)
TICK_SECONDS = 20
MISSED_AFTER = timedelta(minutes=5)


# ---------------------------------------------------------------- time helpers
def to_utc(local: datetime) -> str:
    return local.astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")


def to_local(utc_iso: str) -> datetime:
    return datetime.fromisoformat(utc_iso).astimezone().replace(tzinfo=None)


def local_now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _out(r: Any) -> dict[str, Any]:
    d = dict(r)
    at = to_local(d["due_at"])
    d["due_local"] = at.isoformat(timespec="minutes")
    d["when"] = describe(at, d["recurrence"])
    return d


# ---------------------------------------------------------------- reminders
def create(text: str, at_local: datetime, recurrence: str | None = None, task_id: int | None = None) -> dict[str, Any]:
    text = text.strip().rstrip(".")
    if not text:
        raise ValueError("What should I remind you about?")
    if at_local <= local_now() - timedelta(seconds=30):
        raise ValueError("That time has already passed.")
    with db.connect() as conn:
        rid = conn.execute(
            "INSERT INTO reminders(text, due_at, recurrence, task_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (text[:300], to_utc(at_local), recurrence, task_id, db.now())).lastrowid
    r = get(int(rid))
    activity.record("reminder", f"Reminder set for {r['when']}: {text[:80]}")
    return r


def get(rid: int) -> dict[str, Any]:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise ValueError("Reminder not found")
    return _out(r)


def list_reminders(include_done: bool = False) -> list[dict[str, Any]]:
    q = "SELECT * FROM reminders" + ("" if include_done else " WHERE status IN ('scheduled', 'fired')")
    with db.connect() as conn:
        return [_out(r) for r in conn.execute(q + " ORDER BY CASE status WHEN 'fired' THEN 0 ELSE 1 END, due_at")]


def between(start_local: datetime, end_local: datetime) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM reminders WHERE status = 'scheduled' AND due_at >= ? AND due_at < ? "
                            "ORDER BY due_at", (to_utc(start_local), to_utc(end_local))).fetchall()
    return [_out(r) for r in rows]


def update_status(rid: int, status: str) -> dict[str, Any]:
    if status not in ("done", "cancelled"):
        raise ValueError("status must be done or cancelled")
    r = get(rid)
    with db.connect() as conn:
        if r["recurrence"] and status == "done":
            # "Done" on a repeating reminder acknowledges this occurrence; it stays scheduled.
            conn.execute("UPDATE reminders SET status = 'scheduled' WHERE id = ?", (rid,))
        else:
            conn.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, rid))
        conn.execute("UPDATE notifications SET read = 1 WHERE reminder_id = ?", (rid,))
    if status == "cancelled":
        activity.record("reminder", f"Cancelled reminder: {r['text'][:80]}")
    return get(rid)


def snooze(rid: int, minutes: int = 10) -> dict[str, Any]:
    if not 1 <= minutes <= 7 * 24 * 60:
        raise ValueError("Snooze between 1 minute and 7 days.")
    r = get(rid)
    at = local_now() + timedelta(minutes=minutes)
    with db.connect() as conn:
        if r["recurrence"]:
            # Keep the series: snoozing creates a one-off copy.
            conn.execute("INSERT INTO reminders(text, due_at, created_at) VALUES (?, ?, ?)",
                         (r["text"], to_utc(at), db.now()))
            conn.execute("UPDATE reminders SET status = 'scheduled' WHERE id = ?", (rid,))
        else:
            conn.execute("UPDATE reminders SET status = 'scheduled', due_at = ? WHERE id = ?", (to_utc(at), rid))
        conn.execute("UPDATE notifications SET read = 1 WHERE reminder_id = ?", (rid,))
    return get(rid)


def find(text: str) -> list[dict[str, Any]]:
    words = [w for w in text.lower().split() if len(w) > 2]
    return [r for r in list_reminders() if words and all(w in r["text"].lower() for w in words)]


# ---------------------------------------------------------------- notifications inbox
def notify_user(kind: str, title: str, body: str = "", reminder_id: int | None = None, desktop: bool = True) -> int:
    with db.connect() as conn:
        nid = conn.execute("INSERT INTO notifications(kind, title, body, reminder_id, created_at) VALUES (?,?,?,?,?)",
                           (kind, title[:200], body[:2000], reminder_id, db.now())).lastrowid
    if desktop:
        notify.toast(title, body)
    return int(nid)


def notifications(unread_only: bool = True, after_id: int = 0, limit: int = 30) -> list[dict[str, Any]]:
    q = "SELECT * FROM notifications WHERE id > ?" + (" AND read = 0" if unread_only else "")
    with db.connect() as conn:
        return db.rows(conn.execute(q + " ORDER BY id DESC LIMIT ?", (after_id, limit)))


def mark_read(nid: int | None = None) -> None:
    with db.connect() as conn:
        if nid:
            conn.execute("UPDATE notifications SET read = 1 WHERE id = ?", (nid,))
        else:
            conn.execute("UPDATE notifications SET read = 1")


# ---------------------------------------------------------------- scheduler
def tick(now_local: datetime | None = None) -> list[dict[str, Any]]:
    """Fire everything that's due. Returns what was fired (used by tests)."""
    now_local = now_local or local_now()
    fired = []
    with db.connect() as conn:
        due = conn.execute("SELECT * FROM reminders WHERE status = 'scheduled' AND due_at <= ? ORDER BY due_at",
                           (to_utc(now_local),)).fetchall()
    for r in due:
        r = dict(r)
        at = to_local(r["due_at"])
        missed = now_local - at > MISSED_AFTER
        title = f"⏰ {r['text']}"
        body = (f"Missed while Babilious was closed (was due {describe(at, None, now_local)})." if missed
                else "Reminder from Babilious.")
        with db.connect() as conn:
            if r["recurrence"]:
                nxt = at
                while nxt <= now_local:   # skip occurrences that passed while the PC was off
                    nxt = next_occurrence(nxt, r["recurrence"])
                conn.execute("UPDATE reminders SET due_at = ?, fired_at = ?, fire_count = fire_count + 1 WHERE id = ?",
                             (to_utc(nxt), db.now(), r["id"]))
            else:
                conn.execute("UPDATE reminders SET status = 'fired', fired_at = ?, fire_count = fire_count + 1 "
                             "WHERE id = ?", (db.now(), r["id"]))
        notify_user("reminder", title, body, r["id"])
        activity.record("reminder", f"Reminded you: {r['text'][:80]}" + (" (missed)" if missed else ""))
        fired.append({**r, "missed": missed})
    _maybe_briefing(now_local)
    return fired


def _maybe_briefing(now_local: datetime) -> None:
    hhmm = str(db.get_setting("briefing_time", "") or "")
    if not hhmm:
        return
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except ValueError:
        return
    today = now_local.date().isoformat()
    if db.get_setting("briefing_last_sent", "") == today or now_local.time() < now_local.replace(hour=h, minute=m).time():
        return
    if now_local.hour > h + 3:   # don't send a morning briefing in the evening after a late start
        db.set_setting("briefing_last_sent", today)
        return
    from .briefing import agenda, agenda_text
    db.set_setting("briefing_last_sent", today)
    notify_user("briefing", "☀️ Your day, from Babilious", agenda_text(agenda(now_local), short=True))


_thread: threading.Thread | None = None
_stop = threading.Event()


def start_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def loop():
        while not _stop.is_set():
            try:
                tick()
            except Exception:  # noqa: BLE001 - the scheduler must keep running
                log.exception("Reminder tick failed")
            _stop.wait(TICK_SECONDS)
    _thread = threading.Thread(target=loop, daemon=True, name="reminders")
    _thread.start()


def stop_scheduler() -> None:
    _stop.set()

