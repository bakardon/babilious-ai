"""Milestone 5: time parsing, reminders, habits, notes, deadlines, briefing, plans, history."""
import json
from datetime import date, datetime, timedelta

import pytest

from babilious import db, tasks
from babilious.intents import parse_task_details
from babilious.manager import briefing, habits, notes, planner, reminders
from babilious.manager.timeparse import describe, next_occurrence, parse
from babilious.tools import actions
from conftest import H

NOW = datetime(2026, 11, 14, 16, 40)   # a Saturday afternoon


# ---------------------------------------------------------------- time parsing

@pytest.mark.parametrize("text, at, rec, rest", [
    ("tomorrow at 9 to continue my radar project", "2026-11-15 09:00", None, "continue my radar project"),
    ("in 2 hours to check the oven", "2026-11-14 18:40", None, "check the oven"),
    ("in half an hour stretch", "2026-11-14 17:10", None, "stretch"),
    ("in an hour and a half call mum", "2026-11-14 18:10", None, "call mum"),
    ("in 10 mins tea", "2026-11-14 16:50", None, "tea"),
    ("at 5 to call the supervisor", "2026-11-14 17:00", None, "call the supervisor"),
    ("at 9 call the bank", "2026-11-14 21:00", None, "call the bank"),         # 9am already gone today
    ("tonight to read chapter 3", "2026-11-14 20:00", None, "read chapter 3"),
    ("this evening call Ahmed", "2026-11-14 19:00", None, "call Ahmed"),
    ("on friday to submit the form", "2026-11-20 09:00", None, "submit the form"),
    ("next monday at 10am meeting prep", "2026-11-16 10:00", None, "meeting prep"),
    ("on 20 november to pay rent", "2026-11-20 09:00", None, "pay rent"),
    ("december 3rd at 14:00 dentist", "2026-12-03 14:00", None, "dentist"),
    ("20/11 at 3pm exam", "2026-11-20 15:00", None, "exam"),
    ("2026-12-01 apply to TU Munich", "2026-12-01 09:00", None, "apply to TU Munich"),
    ("tomorrow morning gym", "2026-11-15 09:00", None, "gym"),
    ("at noon lunch with Ali", "2026-11-15 12:00", None, "lunch with Ali"),
    ("the day after tomorrow at 7pm movie", "2026-11-16 19:00", None, "movie"),
    ("on the 30th rent", "2026-11-30 09:00", None, "rent"),
    ("on 3 january renew passport", "2027-01-03 09:00", None, "renew passport"),   # rolls into next year
    ("every day at 8 to take vitamins", "2026-11-15 08:00", "daily", "take vitamins"),
    ("every weekday at 8:30 standup", "2026-11-16 08:30", "weekdays", "standup"),
    ("every monday to plan the week", "2026-11-16 09:00", "weekly:0", "plan the week"),
    ("every morning stretch", "2026-11-15 09:00", "daily", "stretch"),
    ("monthly pay the internet bill", "2026-12-14 09:00", "monthly", "pay the internet bill"),
])
def test_parse_times(text, at, rec, rest):
    w = parse(text, NOW)
    assert w is not None, text
    assert w.at.strftime("%Y-%m-%d %H:%M") == at
    assert w.recurrence == rec
    assert w.rest == rest


@pytest.mark.parametrize("text", ["buy milk", "at 4pm today", "at 3 today", "on 31 february party", ""])
def test_unreadable_or_past_times_return_none(text):
    assert parse(text, NOW) is None


def test_describe_and_next_occurrence():
    assert describe(datetime(2026, 11, 15, 9), None, NOW) == "tomorrow at 09:00"
    assert describe(datetime(2026, 11, 16, 8, 30), "weekdays", NOW) == "every weekday at 08:30"
    assert next_occurrence(datetime(2026, 11, 20, 9), "weekdays") == datetime(2026, 11, 23, 9)  # Fri → Mon
    assert next_occurrence(datetime(2027, 1, 31, 9), "monthly") == datetime(2027, 2, 28, 9)
    assert next_occurrence(datetime(2026, 12, 15, 9), "monthly") == datetime(2027, 1, 15, 9)


@pytest.mark.parametrize("raw, title, due, pr, project", [
    ("finish my GitHub portfolio by friday", "Finish my GitHub portfolio", "fri", 2, ""),
    ("finish the portfolio by friday, high priority, for the job hunt project", "Finish the portfolio", "fri", 1, "Job hunt"),
    ("buy milk", "Buy milk", None, 2, ""),
    ("write chapter 3 for my thesis project", "Write chapter 3", None, 2, "Thesis"),
    ("review the meeting notes from monday", "Review the meeting notes from monday", None, 2, ""),
    ("fix bug in the time parser, urgent", "Fix bug in the time parser", None, 1, ""),
])
def test_task_details(raw, title, due, pr, project):
    t, d, p, proj = parse_task_details(raw)
    assert (t, p, proj) == (title, pr, project)
    if due == "fri":
        assert date.fromisoformat(d).weekday() == 4
    else:
        assert d is due


# ---------------------------------------------------------------- reminders & scheduler

@pytest.fixture
def toasts(monkeypatch):
    sent = []
    from babilious.manager import notify
    monkeypatch.setattr(notify, "toast", lambda title, body="", wait=False: sent.append((title, body)) or True)
    return sent


def test_reminder_fires_once(toasts):
    now = reminders.local_now()
    r = reminders.create("call mum", now + timedelta(minutes=5))
    assert reminders.tick(now) == []
    fired = reminders.tick(now + timedelta(minutes=6))
    assert len(fired) == 1 and not fired[0]["missed"]
    assert reminders.get(r["id"])["status"] == "fired"
    assert toasts == [("⏰ call mum", "Reminder from Babilious.")]
    assert reminders.tick(now + timedelta(minutes=7)) == []          # not again
    note = reminders.notifications()[0]
    assert note["reminder_id"] == r["id"] and note["read"] == 0
    reminders.update_status(r["id"], "done")
    assert reminders.notifications() == []                           # acknowledged


def test_missed_reminder_is_still_delivered(toasts):
    now = reminders.local_now()
    r = reminders.create("submit form", now + timedelta(minutes=1))
    fired = reminders.tick(now + timedelta(hours=3))                  # PC was asleep
    assert fired[0]["missed"] and "Missed" in toasts[0][1]


def test_repeating_reminder_reschedules(toasts):
    now = reminders.local_now()
    r = reminders.create("vitamins", now + timedelta(minutes=1), "daily")
    reminders.tick(now + timedelta(minutes=2))
    again = reminders.get(r["id"])
    assert again["status"] == "scheduled" and again["fire_count"] == 1
    assert reminders.to_local(again["due_at"]) == (now + timedelta(minutes=1, days=1)).replace(microsecond=0)
    # Days missed while off are skipped, not fired one by one.
    reminders.tick(now + timedelta(days=5))
    assert reminders.get(r["id"])["fire_count"] == 2


def test_snooze_cancel_and_past(toasts):
    now = reminders.local_now()
    r = reminders.create("stretch", now + timedelta(minutes=1))
    reminders.tick(now + timedelta(minutes=2))
    s = reminders.snooze(r["id"], 10)
    assert s["status"] == "scheduled"
    assert reminders.update_status(r["id"], "cancelled")["status"] == "cancelled"
    with pytest.raises(ValueError, match="passed"):
        reminders.create("too late", now - timedelta(hours=1))
    with pytest.raises(ValueError):
        reminders.create("   ", now + timedelta(hours=1))


def test_morning_briefing_notification(toasts):
    db.set_setting("briefing_time", "08:30")
    tasks.create_task("Send the SOP", due_date=date.today().isoformat())
    morning = datetime.combine(date.today(), datetime.min.time()).replace(hour=8, minute=31)
    reminders.tick(morning.replace(hour=8, minute=0))
    assert not toasts
    reminders.tick(morning)
    assert toasts and "Send the SOP" in toasts[0][1]
    reminders.tick(morning.replace(hour=9))
    assert len(toasts) == 1                                           # once a day


# ---------------------------------------------------------------- habits

def test_habit_streaks():
    today = reminders.local_now().date()
    h = habits.create("Read 20 minutes")
    for i in range(1, 4):
        habits.log(h["id"], today - timedelta(days=i))
    h = habits.get(h["id"])
    assert h["streak"] == 3 and not h["done_today"] and h["due_today"]   # today not done yet: streak intact
    h = habits.log(h["id"])
    assert h["streak"] == 4 and h["done_today"] and h["last14"][-1]
    habits.log(h["id"], today - timedelta(days=1), done=False)
    assert habits.get(h["id"])["streak"] == 1
    with pytest.raises(ValueError):
        habits.log(h["id"], today + timedelta(days=1))
    with pytest.raises(ValueError):
        habits.create("read 20 minutes")                                 # duplicate, case-insensitive


def test_weekday_habit_skips_weekends():
    h = habits.create("Standup notes", "weekdays")
    fri = date(2026, 11, 13)
    habits.log(h["id"], fri - timedelta(days=1))
    habits.log(h["id"], fri)
    mon = date(2026, 11, 16)
    stats = [x for x in habits.list_habits(mon) if x["id"] == h["id"]][0]
    assert stats["streak"] == 2                                          # Sat/Sun don't break it


def test_find_habit():
    habits.create("Go to the gym")
    habits.create("Read 20 minutes")
    assert habits.find("I went to the gym")["name"] == "Go to the gym"
    assert habits.find("i read for a while")["name"] == "Read 20 minutes"
    assert habits.find("i called the bank") is None


# ---------------------------------------------------------------- notes

def test_notes_crud_and_search():
    n = notes.create("Radar uses 77 GHz FMCW with 4 GHz bandwidth #radar #thesis")
    assert n["tags"] == "radar thesis" and n["title"].startswith("Radar uses 77 GHz")
    notes.create("Groceries: eggs, milk")
    hits = notes.search("bandwidth")
    assert [h["id"] for h in hits] == [n["id"]] and "\x02" in hits[0]["snippet"]
    notes.update(n["id"], body="Radar uses 60 GHz now #radar")
    assert notes.search("bandwidth") == [] and notes.search("60")[0]["id"] == n["id"]
    assert notes.relevant("what frequency does my radar use? 60 GHz?")
    assert notes.delete(n["id"]) and notes.search("radar") == []


# ---------------------------------------------------------------- agenda, week, history

def test_agenda_and_week(toasts):
    today = reminders.local_now().date()
    tasks.create_task("Old overdue thing", due_date=(today - timedelta(days=2)).isoformat())
    tasks.create_task("Due today thing", due_date=today.isoformat())
    tid = tasks.create_task("Finished thing", project="Thesis")
    tasks.update_task(tid, status="done")
    habits.create("Meditate")
    a = briefing.agenda()
    text = briefing.agenda_text(a)
    assert "Overdue: Old overdue thing" in text and "Due today: Due today thing" in text and "Meditate" in text
    w = briefing.week_review()
    assert [t["title"] for t in w["completed"]] == ["Finished thing"] and w["by_project"] == {"Thesis": 1}
    assert "Finished 1 task(s): Finished thing" in briefing.week_text(w)


def test_history_search():
    from babilious import memory
    cid = memory.create_conversation()
    memory.add_message(cid, "user", "My radar project deadline is in March")
    notes.create("radar antenna spec")
    tasks.create_task("Order radar parts")
    kinds = {r["kind"] for r in briefing.search_history("radar")}
    assert {"chat", "note", "task"} <= kinds
    assert briefing.search_history("") == []


# ---------------------------------------------------------------- planner

def test_plan_validation():
    plan = planner.validate({"project": "Apps", "tasks": [{"title": "a b c"}, {"title": "A B C"}, {"title": "x"},
                                                          {"title": "Second task", "due_in_days": 999, "priority": 7}]}, "g")
    assert [t["title"] for t in plan["tasks"]] == ["A b c", "Second task"]
    assert plan["tasks"][1]["due_in_days"] is None and plan["tasks"][1]["priority"] == 3
    with pytest.raises(ValueError):
        planner.validate({"tasks": [{"title": "only one"}]}, "g")
    with pytest.raises(ValueError):
        planner.validate("not json", "g")


# ---------------------------------------------------------------- through the chat

def _chat(client, text, cid=None):
    r = client.post("/api/chat/stream", json={"message": text, "conversation_id": cid}, headers=H)
    events = [json.loads(line) for line in r.get_data(as_text=True).splitlines()]
    return events[0]["id"], events[-1]


def test_chat_reminders(client, toasts):
    cid, d = _chat(client, "Remind me tomorrow at 9 to continue my radar project")
    assert d["action"] == "reminder_created" and "continue my radar project tomorrow at 09:00" in d["text"]
    cid, d = _chat(client, "remind me to call my supervisor", cid)
    assert d["action"] == "reminder_ask_time"
    cid, d = _chat(client, "in 2 hours", cid)
    assert d["action"] == "reminder_created" and "call my supervisor" in d["text"]
    _, d = _chat(client, "what are my reminders?", cid)
    assert "continue my radar project" in d["text"] and "call my supervisor" in d["text"]
    _, d = _chat(client, "cancel the reminder about supervisor", cid)
    assert d["action"] == "reminder_cancelled"
    _, d = _chat(client, "remind me at 4am today to x")
    assert d["action"] in ("reminder_created", "reminder_unclear")   # depends on the current time


def test_chat_briefing_uses_local_data(client):
    tasks.create_task("Email the professor", due_date=reminders.local_now().date().isoformat())
    _, d = _chat(client, "Good morning!")
    assert d["source"] == "llm"
    assert "Due today: Email the professor" in client.fake.last_messages[-2]["content"]
    client.fake.available = False
    _, d = _chat(client, "brief me")
    assert d["source"] == "command" and "Email the professor" in d["text"]   # offline fallback is still accurate


def test_quiet_day_briefing_skips_the_model(client):
    tasks.create_task("Undated thing")
    _, d = _chat(client, "good morning")
    assert d["source"] == "command" and "no deadlines" in d["text"] and "Undated thing" in d["text"]
    assert client.fake.last_messages is None                          # the model was never asked


def test_briefing_that_invents_deadlines_is_replaced(client):
    tasks.create_task("Real deadline", due_date=reminders.local_now().date().isoformat())
    tasks.create_task("Undated thing")
    client.fake.reply = "Undated thing is overdue, so panic."         # model invents an overdue task
    r = client.post("/api/chat/stream", json={"message": "brief me"}, headers=H)
    events = [json.loads(line) for line in r.get_data(as_text=True).splitlines()]
    assert not [e for e in events if e["type"] == "token"]             # nothing streamed before the check
    assert "overdue" not in events[-1]["text"] and "Due today: Real deadline" in events[-1]["text"]


def test_chat_tasks_habits_notes(client):
    _, d = _chat(client, "create a task to finish my GitHub portfolio by friday, high priority")
    assert "due Fri" in d["text"] and "high priority" in d["text"]
    _, d = _chat(client, "add a habit to read 20 minutes every day")
    assert d["action"] == "habit_created"
    _, d = _chat(client, "I read for 20 minutes")
    assert d["action"] == "habit_logged" and "Streak: 1 days" in d["text"]
    _, d = _chat(client, "I finished the GitHub portfolio task")
    assert d["action"] == "task_done"
    assert not [t for t in tasks.open_tasks() if "portfolio" in t["title"].lower()]
    _, d = _chat(client, "did you get my last message?")
    assert d["source"] == "llm"                                       # not hijacked as "done"
    _, d = _chat(client, "note that the radar uses 77 GHz #radar")
    assert d["action"] == "note_created" and "#radar" in d["text"]
    _, d = _chat(client, "show my notes about radar")
    assert "radar uses 77 ghz" in d["text"].lower()
    _, d = _chat(client, "what did I say about radar")
    assert d["action"] == "history"
    _, d = _chat(client, "what's due this week?")
    assert d["action"] == "due_list"


def test_chat_plan_needs_approval(client):
    cid, d = _chat(client, "Help me organise my master's applications")
    assert d["action"] == "plan_proposed" and d["actions"][0]["status"] == "pending"
    assert "Draft the statement of purpose" in d["actions"][0]["reason"]
    assert not tasks.list_tasks("open") or all(t["project"] != "Master's applications" for t in tasks.list_tasks())
    done = actions.approve(d["actions"][0]["id"])
    assert done["status"] == "completed"
    created = [t for t in tasks.list_tasks() if t["project"] == "Master's applications"]
    assert len(created) == 4                                          # duplicate dropped
    assert sum(1 for t in created if t["due_date"]) == 3


def test_chat_week_review(client):
    tid = tasks.create_task("Ship milestone 5")
    tasks.update_task(tid, status="done")
    _, d = _chat(client, "What did I accomplish this week?")
    assert d["source"] == "llm" and "Ship milestone 5" in client.fake.last_messages[-2]["content"]


# ---------------------------------------------------------------- API

def test_manager_api(client, toasts):
    r = client.post("/api/reminders", json={"text": "water plants", "when": "every monday at 8"}, headers=H)
    assert r.status_code == 201 and r.get_json()["recurrence"] == "weekly:0"
    assert client.post("/api/reminders", json={"text": "x", "when": "whenever"}, headers=H).status_code == 400
    p = client.post("/api/parse-time", json={"text": "in 2 hours"}, headers=H).get_json()
    assert p["ok"] and p["when"].startswith("today") or p["when"].startswith("tomorrow")
    start = reminders.local_now().date()
    items = client.get(f"/api/calendar?from={start}&to={start + timedelta(days=28)}").get_json()["items"]
    assert sum(1 for i in items if i["kind"] == "reminder") == 4      # weekly reminder expanded
    assert client.get(f"/api/calendar?from={start}&to={start + timedelta(days=100)}").status_code == 400
    h = client.post("/api/habits", json={"name": "Walk", "schedule": "3x_week"}, headers=H).get_json()
    assert client.post(f"/api/habits/{h['id']}/log", json={}, headers=H).get_json()["progress"].startswith("1/3")
    assert client.post("/api/habits", json={"name": "Bad", "schedule": "hourly"}, headers=H).status_code == 400
    n = client.post("/api/notes", json={"body": "hello #x"}, headers=H).get_json()
    assert client.get("/api/notes?q=hello").get_json()["notes"][0]["id"] == n["id"]
    assert client.patch("/api/settings", json={"briefing_time": "8:3"}, headers=H).status_code == 400
    assert client.patch("/api/settings", json={"briefing_time": "08:30", "desktop_notifications": False},
                        headers=H).status_code == 200
    assert client.get("/api/agenda").get_json()["text"]
    assert "text" in client.get("/api/week").get_json()
    assert client.get("/api/projects-summary").status_code == 200
