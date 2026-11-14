"""Chat commands for the personal manager (reminders, briefing, habits, notes, plans, history)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .. import db, tasks
from . import briefing, habits, notes, reminders
from .timeparse import describe, parse

LEAD = re.compile(r"^\s*(?:(?:hey|ok(?:ay)?)\s+)?(?:babilious[\s,:!.-]*)?(?:(?:please|can you|could you|would you)\s+)*", re.I)


@dataclass
class Reply:
    text: str = ""
    mood: str = "happy"
    action: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    task: dict[str, Any] | None = None      # LLM phrasing task; `text` is the offline fallback
    actions: list[dict[str, Any]] = field(default_factory=list)


def _clean(text: str) -> str:
    return LEAD.sub("", text.strip()).strip().rstrip("?!.") or text.strip()


# ---------------------------------------------------------------- reminders
REMIND = re.compile(r"^(?:remind me|set (?:a |an )?reminder|add (?:a )?reminder|reminder)\b(?:\s+(?:to|that|about|for)\b|\s*:)?\s*(?P<rest>.*)$", re.I)
LIST_REMINDERS = re.compile(r"\b(?:what are|show|list|any|see)\b.*\breminders?\b|^reminders$|^my reminders$", re.I)
CANCEL_REMINDER = re.compile(r"^(?:cancel|delete|remove|stop|forget)\s+(?:the\s+|my\s+|that\s+)?reminders?\s*(?:about|for|to)?\s*(?P<q>.*)$", re.I)
SNOOZE = re.compile(r"^snooze(?:\s+(?:it|that|the reminder))?(?:\s+for)?\s*(?P<n>\d+|an?|half an)?\s*(?P<u>minutes?|mins?|hours?|hrs?)?$", re.I)


def _remind(text: str, conversation_id: int | None) -> Reply | None:
    m = REMIND.match(text)
    if not m:
        return None
    rest = m.group("rest").strip()
    w = parse(rest) if rest else None
    if w is None:
        if rest and re.search(r"\b(at|on|tomorrow|today|tonight|in \d)\b", rest, re.I):
            return Reply("That time has already passed (or I couldn't read it). Try something like "
                         "\"tomorrow at 9\" or \"in 2 hours\".", "confused", "reminder_unclear")
        if not rest:
            return Reply("Remind you of what, and when?", "curious", "reminder_unclear")
        db.set_setting("pending_reminder", {"text": rest, "conversation_id": conversation_id,
                                            "ts": reminders.local_now().isoformat()})
        return Reply(f"When should I remind you to {rest}? (e.g. \"tomorrow at 9\", \"in 30 minutes\", \"every weekday at 8\")",
                     "curious", "reminder_ask_time")
    body = re.sub(r"^(?:me\s+)?(?:to|that|about)\s+", "", w.rest, flags=re.I).strip() or rest
    r = reminders.create(body, w.at, w.recurrence)
    return Reply(f"Done. I'll remind you to {r['text']} {r['when']}.", "happy", "reminder_created", {"reminder": r})


def _pending_time(text: str, conversation_id: int | None) -> Reply | None:
    """Answer to "When should I remind you?" in the same conversation within 10 minutes."""
    p = db.get_setting("pending_reminder", None)
    if not p or p.get("conversation_id") != conversation_id:
        return None
    try:
        if reminders.local_now() - datetime.fromisoformat(p["ts"]) > timedelta(minutes=10):
            db.set_setting("pending_reminder", None)
            return None
    except (KeyError, ValueError):
        return None
    w = parse(text)
    if w is None or len(w.rest.split()) > 3:
        return None
    db.set_setting("pending_reminder", None)
    r = reminders.create(p["text"], w.at, w.recurrence)
    return Reply(f"Done. I'll remind you to {r['text']} {r['when']}.", "happy", "reminder_created", {"reminder": r})


def _list_reminders() -> Reply:
    items = reminders.list_reminders()
    if not items:
        return Reply("No reminders set. Your future self is on their own.", "curious", "reminder_list")
    lines = "\n".join(f"- {r['text']}: {r['when']}" + (" (waiting for you)" if r["status"] == "fired" else "")
                      for r in items[:12])
    return Reply(f"Your reminders:\n{lines}", "curious", "reminder_list", {"reminders": items})


def _cancel_reminder(q: str) -> Reply:
    found = reminders.find(q) if q else []
    if not found:
        return Reply("I couldn't find a reminder matching that. Say \"what are my reminders?\" to see them.",
                     "confused", "reminder_cancel_failed")
    if len(found) > 1:
        return Reply("That matches several reminders:\n" + "\n".join(f"- {r['text']} ({r['when']})" for r in found[:6]) +
                     "\nBe more specific, or cancel them in Reminders.", "curious", "reminder_cancel_ambiguous")
    r = reminders.update_status(found[0]["id"], "cancelled")
    return Reply(f"Cancelled: {r['text']} ({r['when']}).", "happy", "reminder_cancelled")


def _snooze(m: re.Match) -> Reply:
    fired = [r for r in reminders.list_reminders() if r["status"] == "fired"]
    if not fired:
        return Reply("There's nothing to snooze right now.", "confused", "snooze_failed")
    n = m.group("n")
    n = 1 if n in ("a", "an") else 0.5 if n == "half an" else int(n) if n else 10
    minutes = int(n * 60) if (m.group("u") or "").startswith("h") or n == 0.5 else int(n)
    r = reminders.snooze(fired[0]["id"], minutes or 10)
    return Reply(f"Snoozed \"{r['text']}\" until {r['when']}.", "happy", "snoozed")


# ---------------------------------------------------------------- briefing, deadlines, weekly review
BRIEF = re.compile(r"^(?:good morning|morning|gm|brief me|(?:give me )?(?:my |the |a )?(?:daily |morning )?briefing|"
                   r"what'?s (?:my day|on (?:for )?today|today look like|my day look like|the plan for today)|"
                   r"what do i have (?:on )?today|plan my day|what'?s on my plate today|what'?s happening today)\b", re.I)
DUE = re.compile(r"^(?:what'?s|what is|anything|show(?: me)?|list)\s+(?:due|overdue|coming up)\b|"
                 r"^(?:my\s+)?deadlines?\b|\bwhat'?s overdue\b|\bdue this week\b", re.I)
WEEK = re.compile(r"\b(?:summari[sz]e|review|recap)\s+(?:my\s+|this\s+|the\s+)?(?:past\s+)?week\b|\bweekly (?:review|summary|recap)\b|"
                  r"\bwhat (?:did i|have i) (?:do|done|accomplish(?:ed)?|get done|achieve(?:d)?)\s+(?:this|last|in the past)\s+week\b|"
                  r"\bwhat i (?:accomplished|did|got done) this week\b|\bsummari[sz]e what i accomplished\b", re.I)


def _quiet_day(a: dict[str, Any]) -> str:
    open_ = tasks.open_tasks()
    if not open_:
        return f"Nothing scheduled this {a['weekday']} and no open tasks. Suspiciously peaceful. Enjoy it."
    names = ", ".join(f"\"{t['title']}\"" for t in open_[:3])
    more = f" and {len(open_) - 3} more" if len(open_) > 3 else ""
    return (f"Nothing scheduled today: no deadlines, no reminders. You do have {len(open_)} open "
            f"task{'s' if len(open_) != 1 else ''} with no deadline: {names}{more}. "
            f"I'd start with \"{open_[0]['title']}\". Or give them dates so I can nag you properly.")


def _brief() -> Reply:
    a = briefing.agenda()
    plain = briefing.agenda_text(a)
    dated = (a["overdue"] or a["due_today"] or a["reminders_today"] or a["unacknowledged"] or a["habits_due"]
             or a.get("events_today"))
    if not dated:
        # Nothing dated to talk about: answer directly so the model can't invent deadlines.
        return Reply(_quiet_day(a), "curious", "briefing", {"agenda": a})
    instruction = ("Give me my briefing for today in your own voice, in 2-5 short sentences. Use ONLY the agenda "
                   "above. Something is overdue or due today ONLY if it is listed under 'Overdue' or 'Due today'; "
                   "never give a task a deadline the agenda doesn't show. End with one nudge about where to start.")
    forbid = []
    if not a["overdue"]:
        forbid.append(r"\boverdue\b|\blate\b|past due")
    if not a["due_today"]:
        forbid.append(r"\bdue today\b")
    return Reply(f"Here's your {a['weekday']}:\n{plain}", "curious", "briefing", {"agenda": a},
                 task={"context": f"Today's agenda ({a['weekday']} {a['date']}, {a['now']}):\n---\n{plain}\n---",
                       "instruction": instruction, "sources": [], "detail": "Checking your day...",
                       "isolated": True, "forbid": forbid})


def _due(text: str) -> Reply:
    today = reminders.local_now().date()
    over = tasks.overdue(today)
    end = today + timedelta(days=1 if "tomorrow" in text.lower() else 0 if "today" in text.lower() else 7)
    soon = tasks.due_between(today, end)
    if not over and not soon:
        span = "today" if end == today else "tomorrow" if end == today + timedelta(days=1) else "in the next 7 days"
        return Reply(f"Nothing due {span}" + (", and nothing overdue." if not over else "."), "happy", "due_list")
    lines = []
    if over:
        lines.append("Overdue:\n" + "\n".join(f"- {t['title']} (was due {briefing._fmt_day(t['due_date'], today)})" for t in over))
    if soon:
        lines.append("Due soon:\n" + "\n".join(f"- {t['title']} ({briefing._fmt_day(t['due_date'], today)})" for t in soon))
    return Reply("\n".join(lines), "curious" if not over else "annoyed", "due_list", {"overdue": over, "due": soon})


def _week() -> Reply:
    w = briefing.week_review()
    plain = briefing.week_text(w)
    return Reply(plain, "curious", "week_review", {"week": w},
                 task={"context": f"What the user did in the past 7 days (from Babilious' records):\n---\n{plain}\n---",
                       "instruction": ("Summarise what I accomplished this week in your own voice: 3-6 sentences. "
                                       "Credit real progress, point out what slipped, suggest one focus for next week. "
                                       "Use ONLY the records above; don't invent work, numbers or deadlines."),
                       "sources": [], "detail": "Reviewing your week...", "isolated": True})


# ---------------------------------------------------------------- done / habits
DONE = re.compile(r"^(?:mark|set|tick off|check off)\s+(?:the\s+)?(?P<q>.+?)\s+(?:task\s+)?(?:as\s+)?(?:done|complete(?:d)?|finished)$|"
                  r"^(?:i(?:'ve| have)?\s+(?:just\s+)?(?:finished|completed|did|ticked off)|(?:just\s+)?(?:finished|completed|done with))\s+(?P<q2>.+)$|"
                  r"^i\s+(?:just\s+)?(?:went to|went for|worked out|exercised|meditated|read|ran|studied|practiced|practised)\b(?P<q3>.*)$", re.I)
HABIT_ADD = re.compile(r"^(?:add|start|create|track|new)\s+(?:a\s+)?(?:new\s+)?habit(?:\s+(?:to|of|called|named|:))?\s+(?P<name>.+?)"
                       r"(?:\s+(?P<sched>every ?day|daily|on weekdays|every weekday|weekdays|(?P<n>\d|once|twice|three times|four times|five times)\s+(?:a|per)\s+week))?$|"
                       r"^track\s+(?P<name2>.+?)\s+as\s+a\s+habit$", re.I)
HABIT_LIST = re.compile(r"\b(?:how are|show|list|check)\s+(?:my\s+)?habits\b|\bhabit streaks?\b|^my habits$|\bmy streaks?\b", re.I)
TIMES = {"once": 1, "twice": 2, "three times": 3, "four times": 4, "five times": 5}


def _done(m: re.Match, raw: str) -> Reply | None:
    q = (m.group("q") or m.group("q2") or "").strip()
    is_activity = m.group("q3") is not None
    phrase = raw if is_activity else q
    h = habits.find(phrase)
    if h:
        if h["done_today"]:
            return Reply(f"\"{h['name']}\" is already checked off today. Streak: {h['streak']} {h['streak_unit']}.",
                         "happy", "habit_logged")
        h = habits.log(h["id"])
        cheer = "Look at you." if h["streak"] >= 3 else "Noted."
        return Reply(f"{cheer} \"{h['name']}\" checked off. Streak: {h['streak']} {h['streak_unit']}.",
                     "excited" if h["streak"] >= 3 else "happy", "habit_logged", {"habit": h})
    if is_activity:
        return None
    t = tasks.find_task(q)
    if not t:
        if m.group("q") is None:
            return None  # "I finished the book": not a task, so just chat about it
        return Reply(f"I couldn't find an open task or habit matching \"{q}\".", "confused", "done_not_found")
    tasks.update_task(t["id"], status="done")
    from .. import activity
    activity.record("task", f"Completed task: {t['title']}")
    left = len(tasks.open_tasks())
    return Reply(f"Marked \"{t['title']}\" as done. {left} open task{'s' if left != 1 else ''} left.", "happy",
                 "task_done", {"task_id": t["id"]})


def _habit_add(m: re.Match) -> Reply:
    name = (m.group("name") or m.group("name2") or "").strip().rstrip(".")
    sched = (m.group("sched") or "").lower()
    schedule = "daily"
    if "weekday" in sched:
        schedule = "weekdays"
    elif m.group("n"):
        n = TIMES.get(m.group("n").lower()) or int(m.group("n"))
        schedule = f"{min(max(n, 1), 6)}x_week" if n < 7 else "daily"
    name = re.sub(r"^(?:to\s+)", "", name, flags=re.I)
    h = habits.create(name[0].upper() + name[1:], schedule)
    when = {"daily": "every day", "weekdays": "on weekdays"}.get(schedule, f"{schedule[0]}x a week")
    return Reply(f"Tracking \"{h['name']}\" {when}. Tell me when you've done it (\"I did {h['name'].lower()}\").",
                 "happy", "habit_created", {"habit": h})


def _habit_list() -> Reply:
    hs = habits.list_habits()
    if not hs:
        return Reply("You're not tracking any habits. Say \"add a habit to read 20 minutes every day\".",
                     "curious", "habit_list")
    lines = "\n".join(f"- {'✅' if h['done_today'] else '⬜'} {h['name']}: streak {h['streak']} {h['streak_unit']} · {h['progress']}"
                      for h in hs)
    return Reply(f"Your habits:\n{lines}", "curious", "habit_list", {"habits": hs})


# ---------------------------------------------------------------- notes
NOTE = re.compile(r"^(?:take a note|make a note|add a note|new note|note(?: down)?|jot(?: this)? down|write(?: this)? down)"
                  r"(?:\s+(?:that|saying|about))?\s*:?\s+(?P<body>[\s\S]+)$", re.I)
NOTE_LIST = re.compile(r"^(?:show|list|find|search)\s+(?:me\s+)?(?:my\s+)?notes(?:\s+(?:about|on|for|mentioning)\s+(?P<q>.+))?$|"
                       r"^what notes do i have(?:\s+(?:about|on)\s+(?P<q2>.+))?$|^my notes$", re.I)


def _note(body: str) -> Reply:
    n = notes.create(body)
    return Reply(f"Noted: \"{n['title']}\". It's in Second Brain > Notes" +
                 (f", tagged {', '.join('#' + t for t in n['tags'].split())}." if n["tags"] else "."), "happy",
                 "note_created", {"note": n})


def _note_list(q: str) -> Reply:
    found = notes.search(q or "", limit=10)
    if not found:
        return Reply(f"No notes{' about ' + q if q else ''} yet.", "curious", "note_list")
    lines = "\n".join(f"- {n['title']} ({n['updated_at'][:10]})" for n in found)
    return Reply(f"{'Notes about ' + q if q else 'Your latest notes'}:\n{lines}", "curious", "note_list", {"notes": found})


# ---------------------------------------------------------------- planning
PLAN = re.compile(r"^(?:help me\s+)?(?:organi[sz]e|plan(?: out)?|break down|make (?:me )?a plan for|create a plan for|"
                  r"help me (?:get|stay) on top of)\s+(?P<goal>.{6,})$", re.I)


def _plan(goal: str, client: Any, conversation_id: int | None) -> Reply:
    from ..tools import actions
    from .planner import draft
    from ..llm import OllamaError, OllamaUnavailable
    if goal.lower().strip() in ("my day", "today", "the day"):
        return _brief()
    model = str(db.get_setting("active_model"))
    ctx = ""
    related = [t["title"] for t in tasks.open_tasks() if any(w in t["title"].lower() for w in goal.lower().split() if len(w) > 4)]
    if related:
        ctx = "Existing related tasks (don't duplicate): " + "; ".join(related[:8])
    try:
        plan = draft(goal, client, model, ctx)
    except (OllamaUnavailable,) as exc:
        return Reply("I need the local model to draft a plan, and Ollama isn't running.", "error", "plan_failed")
    except (OllamaError, ValueError) as exc:
        return Reply(f"I couldn't put a decent plan together: {exc}", "confused", "plan_failed")
    a = actions.request("create_tasks", plan, "command", conversation_id)
    return Reply(f"Here's a plan for \"{plan['project']}\". It needs your OK first: nothing is created until you approve, "
                 "and you can edit or delete any task afterwards.", "curious", "plan_proposed", actions=[a])


# ---------------------------------------------------------------- history
HISTORY = re.compile(r"^(?:what did i (?:say|write|tell you) about|search (?:my )?history (?:for|about)|"
                     r"when did (?:i|we) (?:mention|talk about|discuss)|find (?:in )?(?:my )?(?:history|conversations?|chats?) (?:about|for)?)\s+(?P<q>.+)$", re.I)


def _history(q: str) -> Reply:
    hits = briefing.search_history(q, limit=12)
    if not hits:
        return Reply(f"Nothing in your history mentions \"{q}\".", "confused", "history")
    lines = "\n".join(f"- [{h['kind']}] {h['when'][:10]} · {h['title']}: {h['text'][:120]}" for h in hits[:8])
    return Reply(f"Found {len(hits)} mention(s) of \"{q}\":\n{lines}\n\nThe full search is in History.", "curious",
                 "history", {"results": hits})


# ---------------------------------------------------------------- entry point
def handle(text: str, client: Any, conversation_id: int | None = None) -> Reply | None:
    t = _clean(text)
    if r := _pending_time(t, conversation_id):
        return r
    if r := _remind(t, conversation_id):
        return r
    if m := SNOOZE.match(t):
        return _snooze(m)
    if m := CANCEL_REMINDER.match(t):
        return _cancel_reminder(m.group("q").strip())
    if LIST_REMINDERS.search(t):
        return _list_reminders()
    if BRIEF.match(t):
        return _brief()
    if WEEK.search(t):
        return _week()
    if DUE.search(t):
        return _due(t)
    if m := HABIT_ADD.match(t):
        try:
            return _habit_add(m)
        except ValueError as exc:
            return Reply(str(exc), "confused", "habit_failed")
    if HABIT_LIST.search(t):
        return _habit_list()
    if m := NOTE.match(t):
        return _note(m.group("body"))
    if m := NOTE_LIST.match(t):
        return _note_list((m.group("q") or m.group("q2") or "").strip())
    if m := HISTORY.match(t):
        return _history(m.group("q").strip())
    if m := PLAN.match(t):
        return _plan(m.group("goal").strip(), client, conversation_id)
    if m := DONE.match(t):
        return _done(m, t)
    return None


__all__ = ["handle", "Reply", "describe"]
