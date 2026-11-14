"""Run tools or park them for approval. Every outcome lands in the activity log."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import activity, db
from . import Risk, get, load_all

log = logging.getLogger(__name__)
EXPIRE_MINUTES = 15

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool            TEXT NOT NULL,
    args            TEXT NOT NULL,
    preview         TEXT NOT NULL,
    risk            TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    origin          TEXT NOT NULL,
    conversation_id INTEGER,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    summary         TEXT NOT NULL DEFAULT '',
    output          TEXT NOT NULL DEFAULT '',
    data            TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status, id);
"""


def init() -> None:
    with db.connect() as conn:
        conn.executescript(SCHEMA)
    load_all()


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["args"] = json.loads(d["args"])
    d["data"] = json.loads(d["data"] or "{}")
    return d


def request(tool_name: str, args: dict[str, Any], origin: str = "command",
            conversation_id: int | None = None) -> dict[str, Any]:
    """Run a safe tool now, or create a pending action that needs approval.

    origin: "command" (your clear request), "model" (the AI suggested it), "ui" (a button).
    """
    tool = get(tool_name)
    risk: Risk = tool.risk(args)
    if origin == "model":
        risk = risk.at_least("confirm")
    preview = tool.preview(args)
    if risk.level == "blocked":
        activity.record("action", f"Blocked: {preview} ({risk.reason})", "failed")
        return {"status": "blocked", "tool": tool_name, "preview": preview, "risk": "blocked",
                "reason": risk.reason, "summary": f"I won't do that: {risk.reason}"}
    if risk.level == "safe":
        return _execute(tool_name, args, preview, "safe", "", origin, conversation_id)
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO actions(tool, args, preview, risk, reason, origin, conversation_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (tool_name, json.dumps(args), preview, risk.level, risk.reason, origin, conversation_id, db.now()))
        action_id = int(cur.lastrowid)
    activity.record("action", f"Waiting for approval: {preview}", "suggested")
    return get_action(action_id)


def _execute(tool_name: str, args: dict[str, Any], preview: str, risk: str, reason: str,
             origin: str, conversation_id: int | None, action_id: int | None = None) -> dict[str, Any]:
    tool = get(tool_name)
    try:
        result = tool.run(args)
    except Exception as exc:  # noqa: BLE001 - a broken tool must never look like success
        log.exception("Tool %s failed", tool_name)
        from . import ToolResult
        result = ToolResult(False, f"That failed: {exc}")
    status = "completed" if result.ok else "failed"
    activity.record("action", result.summary if result.ok else f"{preview}: {result.summary}", status,
                    detail=result.output[:2000])
    with db.connect() as conn:
        if action_id is None:
            cur = conn.execute(
                "INSERT INTO actions(tool, args, preview, risk, reason, origin, conversation_id, status, created_at, "
                "decided_at, summary, output, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tool_name, json.dumps(args), preview, risk, reason, origin, conversation_id, status, db.now(),
                 db.now(), result.summary, result.output[:50_000], json.dumps(result.data)))
            action_id = int(cur.lastrowid)
        else:
            conn.execute("UPDATE actions SET status=?, decided_at=?, summary=?, output=?, data=? WHERE id=?",
                         (status, db.now(), result.summary, result.output[:50_000], json.dumps(result.data), action_id))
    return get_action(action_id)


def get_action(action_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if not r:
        raise ValueError("Action not found")
    d = _row(r)
    d["followup"] = get(d["tool"]).followup if d["tool"] in load_all() else False
    return d


def _expire() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=EXPIRE_MINUTES)).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("UPDATE actions SET status='expired', decided_at=? WHERE status='pending' AND created_at < ?",
                     (db.now(), cutoff))


def approve(action_id: int) -> dict[str, Any]:
    """Run a pending action. Only reachable from the UI's Approve button (header-guarded API)."""
    _expire()
    a = get_action(action_id)
    if a["status"] != "pending":
        raise ValueError(f"This action is {a['status']}, not waiting for approval.")
    with db.connect() as conn:  # claim it so a double click can't run it twice
        claimed = conn.execute("UPDATE actions SET status='running' WHERE id=? AND status='pending'",
                               (action_id,)).rowcount
    if not claimed:
        raise ValueError("Already being handled.")
    activity.record("action", f"Approved: {a['preview']}", "info")
    return _execute(a["tool"], a["args"], a["preview"], a["risk"], a["reason"], a["origin"],
                    a["conversation_id"], action_id)


def reject(action_id: int) -> dict[str, Any]:
    a = get_action(action_id)
    if a["status"] != "pending":
        raise ValueError(f"This action is {a['status']}.")
    with db.connect() as conn:
        conn.execute("UPDATE actions SET status='rejected', decided_at=?, summary='Cancelled by you' WHERE id=?",
                     (db.now(), action_id))
    activity.record("action", f"Rejected: {a['preview']}", "info")
    return get_action(action_id)


def list_actions(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    _expire()
    with db.connect() as conn:
        if status:
            rows = conn.execute("SELECT * FROM actions WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        else:
            rows = conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,))
        return [_row(r) for r in rows]


def last_failed_output(minutes: int = 30) -> dict[str, Any] | None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM actions WHERE status='failed' AND output != '' AND created_at >= ? "
                         "ORDER BY id DESC LIMIT 1", (cutoff,)).fetchone()
    return _row(r) if r else None
