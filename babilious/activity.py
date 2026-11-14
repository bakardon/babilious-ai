"""Activity log: a record of what Babilious did, suggested, or failed to do.

``status`` keeps completed actions clearly separate from suggestions.
"""
from __future__ import annotations

import logging
from typing import Any

from .db import connect, now, rows

log = logging.getLogger("babilious.activity")
STATUSES = ("completed", "suggested", "failed", "info")


def record(kind: str, summary: str, status: str = "completed", detail: str = "") -> None:
    if status not in STATUSES:
        status = "info"
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO activity(ts, kind, summary, status, detail) VALUES (?, ?, ?, ?, ?)",
                (now(), kind, summary[:300], status, detail[:2000]),
            )
    except Exception:  # logging must never break the app
        log.exception("Could not write activity entry")
    log.info("[%s/%s] %s", kind, status, summary)


def recent(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)))
