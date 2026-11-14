"""SQLite storage: one local file, no server.

Each call opens a short-lived connection, which keeps things thread-safe for
Flask's threaded dev server without a pool.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT 'New conversation',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    model           TEXT,
    source          TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL DEFAULT 'fact',
    content    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'user',
    pinned     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'done')),
    priority     INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
    project      TEXT NOT NULL DEFAULT '',
    due_date     TEXT,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS activity (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    summary TEXT NOT NULL,
    status  TEXT NOT NULL DEFAULT 'completed'
            CHECK (status IN ('completed', 'suggested', 'failed', 'info')),
    detail  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model           TEXT NOT NULL,
    ts              TEXT NOT NULL,
    load_ms         REAL,
    first_token_ms  REAL,
    total_ms        REAL,
    tokens_per_sec  REAL,
    prompt_tokens_per_sec REAL,
    output_tokens   INTEGER
);

-- Milestone 2: second brain over user-selected folders
CREATE TABLE IF NOT EXISTS folders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    path            TEXT NOT NULL UNIQUE,
    added_at        TEXT NOT NULL,
    last_indexed_at TEXT,
    file_count      INTEGER NOT NULL DEFAULT 0,
    content_count   INTEGER NOT NULL DEFAULT 0,
    note            TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id  INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    path       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    ext        TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mtime      REAL NOT NULL,
    indexed_at TEXT NOT NULL,
    has_text   INTEGER NOT NULL DEFAULT 0,
    chars      INTEGER NOT NULL DEFAULT 0,
    summary    TEXT NOT NULL DEFAULT '',
    keywords   TEXT NOT NULL DEFAULT '',
    links      TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_documents_folder ON documents(folder_id);
CREATE INDEX IF NOT EXISTS idx_documents_mtime ON documents(mtime);
CREATE INDEX IF NOT EXISTS idx_documents_name ON documents(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS chunks (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord    INTEGER NOT NULL,
    text   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model    TEXT NOT NULL,
    vector   BLOB NOT NULL
);

-- Milestone 5: personal manager
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    due_at      TEXT NOT NULL,              -- UTC ISO
    recurrence  TEXT,                       -- daily | weekdays | weekly:<0-6> | monthly
    status      TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'fired', 'done', 'cancelled')),
    task_id     INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL,
    fired_at    TEXT,
    fire_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,              -- reminder | briefing | info
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    reminder_id INTEGER,
    created_at  TEXT NOT NULL,
    read        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS habits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    schedule    TEXT NOT NULL DEFAULT 'daily',   -- daily | weekdays | <n>x_week
    created_at  TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS habit_logs (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    day      TEXT NOT NULL,                       -- local YYYY-MM-DD
    PRIMARY KEY (habit_id, day)
);
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, body, tags, content='notes', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, body, tags) VALUES (new.id, new.title, new.body, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body, tags) VALUES ('delete', old.id, old.title, old.body, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body, tags) VALUES ('delete', old.id, old.title, old.body, old.tags);
    INSERT INTO notes_fts(rowid, title, body, tags) VALUES (new.id, new.title, new.body, new.tags);
END;
CREATE TABLE IF NOT EXISTS research (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    sources         TEXT NOT NULL DEFAULT '[]',   -- JSON [{n, title, url, site, kind}]
    warnings        TEXT NOT NULL DEFAULT '[]',
    depth           TEXT NOT NULL DEFAULT 'normal',
    model           TEXT NOT NULL DEFAULT '',
    conversation_id INTEGER,
    created_at      TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db_path() -> Path:
    return Path(config.DB_PATH)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


def init_db() -> None:
    """Create tables and import the legacy JSON data once."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(SCHEMA_VERSION),),
        )
    migrate_legacy_json()


# ---------------------------------------------------------------- settings

def get_setting(key: str, default: Any = None) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return config.DEFAULT_SETTINGS.get(key, default)
    return json.loads(row["value"])


def set_setting(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def all_settings() -> dict[str, Any]:
    merged = dict(config.DEFAULT_SETTINGS)
    with connect() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            merged[row["key"]] = json.loads(row["value"])
    return merged


# ---------------------------------------------------------------- migration

def migrate_legacy_json() -> None:
    """Copy the original JSON memory/mission files into SQLite (once).

    The JSON files are left untouched so nothing from the original project
    is lost.
    """
    if get_setting("legacy_migrated", False):
        return
    imported_memories = imported_tasks = 0
    with connect() as conn:
        ts = now()
        if config.LEGACY_MEMORY_FILE.exists():
            try:
                data = json.loads(config.LEGACY_MEMORY_FILE.read_text(encoding="utf-8"))
                seen: set[str] = set()
                for entry in data.get("entries", []):
                    summary = str(entry.get("summary", "")).strip()
                    if not summary or summary.lower() in seen:
                        continue
                    seen.add(summary.lower())
                    conn.execute(
                        "INSERT INTO memories(kind, content, source, created_at, updated_at) "
                        "VALUES (?, ?, 'adventure_log.json', ?, ?)",
                        (str(entry.get("type", "fact")), summary, ts, ts),
                    )
                    imported_memories += 1
            except (OSError, ValueError) as exc:
                log.warning("Could not import adventure log: %s", exc)

        if config.LEGACY_MISSIONS_FILE.exists():
            try:
                data = json.loads(config.LEGACY_MISSIONS_FILE.read_text(encoding="utf-8"))
                for mission in data.get("missions", []):
                    title = str(mission.get("objective") or mission.get("title") or "").strip()
                    if not title:
                        continue
                    done = bool(mission.get("completed"))
                    conn.execute(
                        "INSERT INTO tasks(title, notes, status, project, created_at, completed_at) "
                        "VALUES (?, ?, ?, 'Babilious', ?, ?)",
                        (title, str(mission.get("title", "")), "done" if done else "open",
                         ts, ts if done else None),
                    )
                    imported_tasks += 1
            except (OSError, ValueError) as exc:
                log.warning("Could not import missions: %s", exc)

        if config.LEGACY_STATE_FILE.exists():
            try:
                state = json.loads(config.LEGACY_STATE_FILE.read_text(encoding="utf-8"))
                focus = str(state.get("current_expedition", "")).strip()
                if focus:
                    conn.execute(
                        "INSERT INTO memories(kind, content, source, created_at, updated_at) "
                        "VALUES ('focus', ?, 'user_state.json', ?, ?)",
                        (f"Current focus: {focus}", ts, ts),
                    )
                    imported_memories += 1
            except (OSError, ValueError) as exc:
                log.warning("Could not import user state: %s", exc)

        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('legacy_migrated', 'true') "
            "ON CONFLICT(key) DO UPDATE SET value = 'true'"
        )
    log.info("Legacy import: %d memories, %d tasks", imported_memories, imported_tasks)
