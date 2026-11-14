"""Understand times like "tomorrow at 9", "in 2 hours", "every weekday at 8:30", "on 20 november".

Small and predictable on purpose: every phrase it accepts is covered by tests,
and anything it can't read returns None so Babilious asks instead of guessing.
All times are local; callers convert to UTC for storage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WD = {d: i for i, d in enumerate(WEEKDAYS)} | {d[:3]: i for i, d in enumerate(WEEKDAYS)} | {"tues": 1, "thur": 3, "thurs": 3}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MO = {m: i + 1 for i, m in enumerate(MONTHS)} | {m[:3]: i + 1 for i, m in enumerate(MONTHS)} | {"sept": 9}
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
       "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
       "forty": 40, "forty-five": 45, "fortyfive": 45, "sixty": 60, "ninety": 90,
       "a couple of": 2, "a couple": 2, "couple of": 2, "a few": 3, "few": 3}
PARTS = {"morning": time(9, 0), "afternoon": time(15, 0), "evening": time(19, 0), "night": time(21, 0),
         "noon": time(12, 0), "midday": time(12, 0), "lunch": time(13, 0), "lunchtime": time(13, 0),
         "midnight": time(23, 59), "end of the day": time(17, 30), "eod": time(17, 30)}
DEFAULT_TIME = time(9, 0)
WD_NAMES = "|".join(sorted(WD, key=len, reverse=True))
MO_NAMES = "|".join(sorted(MO, key=len, reverse=True))
NUMWORDS = "|".join(sorted((re.escape(k) for k in NUM), key=len, reverse=True))


@dataclass
class When:
    at: datetime                   # local, naive
    recurrence: str | None = None  # daily | weekdays | weekly:<0-6> | monthly
    rest: str = ""                 # the text with the time phrase removed
    explicit_time: bool = False


def _num(s: str) -> float | None:
    s = s.strip().lower()
    if s in NUM:
        return float(NUM[s])
    try:
        return float(s)
    except ValueError:
        return None


TIME_RE = re.compile(
    r"\b(?:at\s+|@\s*|by\s+)?(?P<h>[01]?\d|2[0-3])(?::|\.)(?P<m>[0-5]\d)\s*(?P<ap>a\.?m\.?|p\.?m\.?)?(?![\d/])"
    r"|\b(?:at\s+|@\s*|by\s+)?(?P<h2>1[0-2]|0?[1-9])\s*(?P<ap2>a\.?m\.?|p\.?m\.?)(?=\W|$)"
    r"|\bat\s+(?P<h3>1[0-2]|0?[1-9]|1[3-9]|2[0-3])(?:\s*o'?clock)?(?![\d:/.])", re.I)


def _find_time(text: str, day_is_today: bool, now: datetime) -> tuple[time | None, str]:
    m = TIME_RE.search(text)
    if m:
        if m.group("h") is not None:
            h, mi, ap = int(m.group("h")), int(m.group("m")), m.group("ap")
        elif m.group("h2") is not None:
            h, mi, ap = int(m.group("h2")), 0, m.group("ap2")
        else:
            h, mi, ap = int(m.group("h3")), 0, None
        if ap:
            ap = ap.lower().replace(".", "")
            if ap == "pm" and h < 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
        elif h < 12:
            # No am/pm: "at 5" means 5pm; "at 9" means 9am unless 9am has already gone today.
            if 1 <= h <= 6:
                h += 12
            elif 7 <= h <= 11 and day_is_today and time(h, mi) <= now.time():
                h += 12
        return time(h, mi), (text[:m.start()] + " " + text[m.end():])
    # Day-part words: the one written first wins ("at noon lunch with Ali" → noon).
    hits = []
    for word, t in PARTS.items():
        mm = re.search(rf"\b(?:at\s+|in the\s+|this\s+|by\s+)?{re.escape(word)}\b", text, re.I)
        if mm:
            hits.append((mm.start(), -len(word), mm, t))
    if hits:
        _, _, mm, t = min(hits, key=lambda h: (h[0], h[1]))
        return t, text[:mm.start()] + " " + text[mm.end():]
    if re.search(r"\btonight\b", text, re.I):
        return time(20, 0), text
    return None, text


def _next_weekday(base: date, wd: int) -> date:
    """The next given weekday strictly after `base`."""
    return base + timedelta(days=(wd - base.weekday()) % 7 or 7)


def parse(text: str, now: datetime | None = None) -> When | None:
    now = (now or datetime.now()).replace(microsecond=0)
    original = text
    t = " " + text + " "
    low = t.lower()

    # ---------------------------------------------------------- recurrence
    recurrence = None
    rec_patterns = [
        (rf"\bevery\s+(?:week\s?day|weekday)s?\b|\b(?:on\s+)?weekdays\b|\bevery\s+working\s+day\b", "weekdays"),
        (rf"\bevery\s+(?P<wd>{WD_NAMES})\b|\bon\s+(?P<wd2>{WD_NAMES})s\b", "weekly"),
        (r"\bevery\s+(?:single\s+)?day\b|\bdaily\b|\beach\s+day\b|\bevery\s+(?=morning|evening|night|afternoon)", "daily"),
        (r"\bevery\s+week\b|\bweekly\b", "weekly"),
        (r"\bevery\s+month\b|\bmonthly\b", "monthly"),
    ]
    rec_wd = None
    for pat, kind in rec_patterns:
        m = re.search(pat, low)
        if m:
            recurrence = kind
            if kind == "weekly" and m.groupdict().get("wd") or m.groupdict().get("wd2"):
                rec_wd = WD[(m.group("wd") or m.group("wd2")).lower()]
            t = t[:m.start()] + " " + t[m.end():]
            low = t.lower()
            break

    # ---------------------------------------------------------- relative ("in 2 hours")
    m = re.search(rf"\bin\s+(?P<n>half an?|\d+(?:\.\d+)?|{NUMWORDS})\s*(?P<u>minutes?|mins?|m|hours?|hrs?|h|days?|weeks?)\b"
                  r"(?:\s+and\s+(?:a\s+)?half)?", low)
    if m and recurrence is None:
        n_txt = m.group("n")
        n = 0.5 if n_txt.startswith("half") else _num(n_txt)
        if "and a half" in m.group(0) or "and half" in m.group(0):
            n = (n or 0) + 0.5
        unit = m.group("u")
        if n is None:
            return None
        if n_txt.startswith("half"):
            delta = timedelta(minutes=30) if unit.startswith("h") else timedelta(hours=12)
        elif unit.startswith(("m",)) and not unit.startswith("mo"):
            delta = timedelta(minutes=n)
        elif unit.startswith("h"):
            delta = timedelta(hours=n)
        elif unit.startswith("d"):
            delta = timedelta(days=n)
        else:
            delta = timedelta(weeks=n)
        rest = t[:m.start()] + " " + t[m.end():]
        return When(now + delta, None, _tidy(rest), explicit_time=True)

    # ---------------------------------------------------------- the day
    day: date | None = None
    span = None
    if m := re.search(r"\b(?:the\s+)?day\s+after\s+tomorrow\b", low):
        day, span = now.date() + timedelta(days=2), m
    elif m := re.search(r"\btomorrow\b|\btmrw\b|\btmr\b", low):
        day, span = now.date() + timedelta(days=1), m
    elif m := re.search(r"\btoday\b|\btonight\b|\bthis\s+(?:morning|afternoon|evening)\b|\blater\s+today\b", low):
        day = now.date()
        span = m if not m.group(0).startswith(("tonight", "this")) else None
    elif m := re.search(r"\bnext\s+week\b", low):
        day, span = now.date() + timedelta(days=7 - now.weekday()), m
    elif m := re.search(r"\b(?:this\s+)?weekend\b", low):
        day, span = _next_weekday(now.date(), 5) if now.weekday() != 5 else now.date(), m
    elif m := re.search(rf"\b(?:on\s+)?(?:(?P<nx>next|this|coming)\s+)?(?P<wd>{WD_NAMES})\b", low):
        # "friday" / "this friday" = the coming one (today if it's Friday and still possible);
        # "next friday" = the coming one too, never today.
        wd = WD[m.group("wd")]
        days = (wd - now.weekday()) % 7
        if days == 0 and m.group("nx") == "next":
            days = 7
        day, span = now.date() + timedelta(days=days), m
    elif m := re.search(r"\b(?P<y>20\d\d)-(?P<mo>\d{1,2})-(?P<d>\d{1,2})\b", low):
        day, span = date(int(m.group("y")), int(m.group("mo")), int(m.group("d"))), m
    elif m := re.search(rf"\b(?:on\s+)?(?:the\s+)?(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mo>{MO_NAMES})\b(?:\s+(?P<y>20\d\d))?"
                        rf"|\b(?:on\s+)?(?P<mo2>{MO_NAMES})\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<y2>20\d\d))?", low):
        d = int(m.group("d") or m.group("d2"))
        mo = MO[(m.group("mo") or m.group("mo2"))]
        y = int(m.group("y") or m.group("y2") or now.year)
        try:
            day = date(y, mo, d)
        except ValueError:
            return None
        if not (m.group("y") or m.group("y2")) and day < now.date():
            day = date(y + 1, mo, d)
        span = m
    elif m := re.search(r"\b(?:on\s+)?(?P<d>\d{1,2})/(?P<mo>\d{1,2})(?:/(?P<y>\d{2,4}))?\b", low):
        # Day-first, as written in Pakistan/UK.
        y = int(m.group("y")) if m.group("y") else now.year
        y = y + 2000 if y < 100 else y
        try:
            day = date(y, int(m.group("mo")), int(m.group("d")))
        except ValueError:
            return None
        if not m.group("y") and day < now.date():
            day = day.replace(year=day.year + 1)
        span = m
    elif m := re.search(r"\bon\s+the\s+(?P<d>\d{1,2})(?:st|nd|rd|th)\b", low):
        d = int(m.group("d"))
        try:
            day = now.date().replace(day=d)
            if day < now.date():
                day = (now.date().replace(day=1) + timedelta(days=32)).replace(day=d)
        except ValueError:
            return None
        span = m
    if span is not None:
        t = t[:span.start()] + " " + t[span.end():]

    # ---------------------------------------------------------- the time
    # For repeating reminders "at 8" is always 8am, whatever the time is now.
    is_today = recurrence is None and (day is None or day == now.date())
    tm, t = _find_time(t, is_today, now)
    if re.search(r"\btonight\b", low) and tm is None:
        tm = time(20, 0)
    t = re.sub(r"\btonight\b|\bthis\s+(?:morning|afternoon|evening)\b|\blater\s+today\b", " ", t, flags=re.I)

    if recurrence:
        tm = tm or DEFAULT_TIME
        base = now.date()
        at = datetime.combine(base, tm)
        if recurrence == "weekly" and rec_wd is not None:
            days = (rec_wd - base.weekday()) % 7
            at = datetime.combine(base + timedelta(days=days), tm)
        if at <= now:
            at = next_occurrence(at, f"weekly:{rec_wd}" if rec_wd is not None else recurrence)
        while recurrence == "weekdays" and at.weekday() >= 5:
            at += timedelta(days=1)
        rec = f"weekly:{rec_wd if rec_wd is not None else at.weekday()}" if recurrence == "weekly" else recurrence
        return When(at, rec, _tidy(t), explicit_time=True)

    if day is None and tm is None:
        return None
    if day is None:
        at = datetime.combine(now.date(), tm)
        if at <= now:
            at += timedelta(days=1)
        return When(at, None, _tidy(t), explicit_time=True)
    at = datetime.combine(day, tm or DEFAULT_TIME)
    if at <= now and day == now.date() and tm is not None:
        return None  # a time today that has already passed: ask rather than guess
    if at <= now:
        at = datetime.combine(day, max(DEFAULT_TIME, (now + timedelta(hours=1)).time()))
    return When(at, None, _tidy(t), explicit_time=tm is not None)


def next_occurrence(at: datetime, recurrence: str) -> datetime:
    if recurrence == "daily":
        return at + timedelta(days=1)
    if recurrence == "weekdays":
        nxt = at + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    if recurrence.startswith("weekly"):
        return at + timedelta(days=7)
    if recurrence == "monthly":
        month = at.month % 12 + 1
        year = at.year + (at.month == 12)
        for d in (at.day, 30, 29, 28):
            try:
                return at.replace(year=year, month=month, day=d)
            except ValueError:
                continue
    raise ValueError(f"Unknown recurrence {recurrence}")


def _tidy(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    t = re.sub(r"^(?:to|that|about|for)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+(?:at|on|by|for|in)$", "", t, flags=re.I)
    return t.strip(" ,.;:-")


def describe(at: datetime, recurrence: str | None = None, now: datetime | None = None) -> str:
    """Friendly description: 'tomorrow at 9:00', 'Friday 21 Nov at 17:30', 'every weekday at 8:30'."""
    now = now or datetime.now()
    hm = at.strftime("%H:%M")
    if recurrence:
        if recurrence == "daily":
            return f"every day at {hm}"
        if recurrence == "weekdays":
            return f"every weekday at {hm}"
        if recurrence.startswith("weekly"):
            return f"every {WEEKDAYS[at.weekday()].title()} at {hm}"
        if recurrence == "monthly":
            return f"every month on the {at.day} at {hm}"
    days = (at.date() - now.date()).days
    if days == 0:
        return f"today at {hm}"
    if days == 1:
        return f"tomorrow at {hm}"
    if 1 < days < 7:
        return f"{WEEKDAYS[at.weekday()].title()} at {hm}"
    return f"{at:%a %d %b} at {hm}" + (f" {at.year}" if at.year != now.year else "")


def parse_date(text: str, now: datetime | None = None) -> date | None:
    """Just the day ("by friday", "due 20 nov", "next week")."""
    w = parse(text, now)
    return w.at.date() if w else None
