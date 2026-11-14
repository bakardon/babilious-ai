"""Read-only calendar from an iCal (.ics) link: Google Calendar, Outlook, iCloud...

In Google Calendar: Settings > your calendar > "Secret address in iCal format". Paste it in
Settings > Integrations. It's stored only in the local database and fetched straight from
Google; Babilious never writes to your calendar.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .. import db

log = logging.getLogger(__name__)
CACHE_SECONDS = 900
_cache: dict[str, Any] = {"url": "", "at": 0.0, "events": [], "error": ""}
_lock = threading.Lock()
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def url() -> str:
    return str(db.get_setting("calendar_ics_url", "") or "").strip()


def valid_url(value: Any) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    if v.lower().startswith("webcal://"):
        v = "https://" + v[9:]
    if not re.match(r"https://[\w.-]+/\S+$", v):
        raise ValueError("Paste the calendar's iCal address (it starts with https:// or webcal://).")
    return v


# ---------------------------------------------------------------- parsing
def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_dt(params: str, value: str) -> tuple[datetime, bool]:
    """(naive local datetime, all_day)."""
    value = value.strip()
    if "VALUE=DATE" in params.upper() or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d"), True
    dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None), False
    m = re.search(r"TZID=\"?([^;:\"]+)", params)
    if m:
        try:
            return dt.replace(tzinfo=ZoneInfo(m.group(1))).astimezone().replace(tzinfo=None), False
        except Exception:  # noqa: BLE001  (Windows-style or unknown zone names: treat as local time)
            pass
    return dt, False


def parse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": set()}
            continue
        if line == "END:VEVENT":
            if cur and cur.get("start") and cur.get("status", "").upper() != "CANCELLED":
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, _, params = head.partition(";")
        name = name.upper()
        try:
            if name == "SUMMARY":
                cur["title"] = _unescape(value).strip() or "(no title)"
            elif name == "LOCATION":
                cur["location"] = _unescape(value).strip()
            elif name == "DTSTART":
                cur["start"], cur["all_day"] = _parse_dt(params, value)
            elif name == "DTEND":
                cur["end"], _ = _parse_dt(params, value)
            elif name == "RRULE":
                cur["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
            elif name == "EXDATE":
                for v in value.split(","):
                    cur["exdates"].add(_parse_dt(params, v)[0])
            elif name == "STATUS":
                cur["status"] = value
            elif name == "RECURRENCE-ID":
                cur["recurrence_id"] = True
        except ValueError:
            continue
    return events


def _occurrences(ev: dict[str, Any], lo: datetime, hi: datetime) -> list[datetime]:
    start: datetime = ev["start"]
    rule = ev.get("rrule")
    if not rule:
        return [start] if lo <= start < hi or (ev.get("end") and start < lo < ev["end"] and not ev["all_day"]) else []
    freq = rule.get("FREQ", "").upper()
    step = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        try:
            until = _parse_dt("", rule["UNTIL"])[0]
        except ValueError:
            pass
    bydays = [DAYS.index(d[-2:]) for d in rule.get("BYDAY", "").split(",") if d[-2:] in DAYS]
    out: list[datetime] = []
    n = 0
    cursor = start
    for _ in range(3000):
        if freq == "WEEKLY" and bydays:
            week_start = cursor - timedelta(days=cursor.weekday())
            cands = sorted(week_start + timedelta(days=d) for d in bydays)
            cands = [c.replace(hour=start.hour, minute=start.minute, second=start.second) for c in cands]
        else:
            cands = [cursor]
        for c in cands:
            if c < start:
                continue
            if (until and c > until) or (count is not None and n >= count) or c >= hi:
                return out
            n += 1
            if c >= lo and c not in ev["exdates"]:
                out.append(c)
        if freq == "DAILY":
            cursor += timedelta(days=step)
        elif freq == "WEEKLY":
            cursor += timedelta(weeks=step)
        elif freq == "MONTHLY":
            y, m = divmod(cursor.month - 1 + step, 12)
            try:
                cursor = cursor.replace(year=cursor.year + y, month=m + 1)
            except ValueError:  # e.g. the 31st in a short month: skip it
                cursor = cursor.replace(day=1, year=cursor.year + y, month=m + 1) + timedelta(days=40)
        elif freq == "YEARLY":
            try:
                cursor = cursor.replace(year=cursor.year + step)
            except ValueError:
                cursor = cursor.replace(year=cursor.year + step, day=28)
        else:
            return out
    return out


def expand(events: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    lo, hi = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())
    out = []
    for ev in events:
        dur = (ev["end"] - ev["start"]) if ev.get("end") else timedelta(0)
        for at in _occurrences(ev, lo, hi):
            out.append({"kind": "event", "title": ev.get("title", "(no title)"), "date": at.date().isoformat(),
                        "time": "" if ev["all_day"] else at.strftime("%H:%M"),
                        "end": "" if ev["all_day"] or not dur else (at + dur).strftime("%H:%M"),
                        "all_day": ev["all_day"], "location": ev.get("location", "")})
    out.sort(key=lambda e: (e["date"], e["time"] or "00:00"))
    return out


# ---------------------------------------------------------------- fetching
def _load(force: bool = False) -> list[dict[str, Any]]:
    link = url()
    if not link:
        return []
    with _lock:
        if not force and _cache["url"] == link and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["events"]
        try:
            r = requests.get(link, timeout=15, headers={"User-Agent": "Babilious calendar reader"})
            r.raise_for_status()
            if "BEGIN:VCALENDAR" not in r.text[:2000]:
                raise ValueError("that link didn't return a calendar (is it the iCal address?)")
            events = parse(r.text)
            _cache.update(url=link, at=time.time(), events=events, error="")
        except Exception as exc:  # noqa: BLE001
            log.info("Calendar fetch failed: %s", exc)
            _cache.update(error=str(exc)[:200])
            if _cache["url"] != link:
                _cache.update(url=link, at=time.time(), events=[])
            else:
                _cache["at"] = time.time() - CACHE_SECONDS + 120  # retry in 2 minutes
        return _cache["events"]


def events_between(start: date, end: date, force: bool = False) -> list[dict[str, Any]]:
    return expand(_load(force), start, end)


def status() -> dict[str, Any]:
    return {"configured": bool(url()), "error": _cache["error"], "events": len(_cache["events"]),
            "fetched": _cache["at"] if _cache["url"] == url() else 0}
