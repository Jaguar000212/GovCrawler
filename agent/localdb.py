"""Agent-local settings + machine identity — a tiny SQLite (`agent_local.db`),
deliberately plain `sqlite3` with a `PRAGMA user_version` step-runner rather
than SQLAlchemy/Alembic, mirroring `agent/local_store.py`'s existing "this is
per-machine data, not shared schema" precedent (plan.md §19.1 Phase 9 Part 2).

Holds the machine-local settings that used to have no real home once the
agent stops being able to fall back to "just talk to myself over loopback"
(`cloud_api_base_url`, the local BFF's own bind host/port, worker
concurrency) plus a durable `agent_id` — a UUID minted once on first run,
never a real hostname, that identifies this machine to the cloud (e.g. for
job-resume ownership, see agent/api.py).
"""

import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from portal.paths import DATA_DIR

log = logging.getLogger(__name__)

_DEFAULT_PATH = DATA_DIR / "agent_local.db"

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS local_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS visited_history (
    url TEXT PRIMARY KEY,
    visited_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_visited_history_visited_at ON visited_history (visited_at);
"""

_lock = threading.RLock()  # re-entrant: _require_conn() takes it and is itself called from within a `with _lock:` block
_conn: sqlite3.Connection | None = None


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(_SCHEMA_V1)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    if version < 2:
        conn.executescript(_SCHEMA_V2)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    return conn


def init(db_path: Path) -> None:
    """Explicit init — call before any get_setting/get_agent_id call to
    control where the DB file lives (tests MUST do this with an isolated
    path; see feedback_test_isolation lesson from plan.md §19.1 Phase 8 — a
    throwaway test DB must never silently touch the real live file).
    Re-callable — closes any prior connection first."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = _connect(db_path)


def _require_conn() -> sqlite3.Connection:
    """Lazily inits against the real default path if nothing called init()
    explicitly — production entrypoints (the launcher, portal.main) don't
    strictly need to call init() themselves, only tests need to (with an
    isolated path) to avoid ever touching the real file."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect(_DEFAULT_PATH)
        return _conn


def get_setting(key: str, default: str | None = None) -> str | None:
    with _lock:
        row = _require_conn().execute(
            "SELECT value FROM local_settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with _lock:
        conn = _require_conn()
        conn.execute(
            "INSERT INTO local_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def has_setting(key: str) -> bool:
    return get_setting(key) is not None


def get_agent_id() -> str:
    """A UUID minted once on first run and persisted forever — NOT a real
    hostname, so nothing machine-identifying leaks into the shared cloud DB
    when this gets stamped onto a job (agent/api.py, cloud/api/coordination.py)."""
    existing = get_setting("agent_id")
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    set_setting("agent_id", new_id)
    log.info(f"Minted new agent_id: {new_id}")
    return new_id


def mark_visited(url: str) -> None:
    """Durable, cross-job local history — the replacement for the old
    cloud-side VisitedUrl table's recrawl-protection role (plan.md §19.1
    Phase 9 Part 2, 2.2). Not outboxed/synced anywhere; this machine's own
    business only."""
    with _lock:
        conn = _require_conn()
        conn.execute(
            "INSERT INTO visited_history (url, visited_at) VALUES (?, ?) "
            "ON CONFLICT(url) DO UPDATE SET visited_at = excluded.visited_at",
            (url, time.time()),
        )
        conn.commit()


def get_recently_visited(since_ts: float) -> set[str]:
    with _lock:
        rows = _require_conn().execute(
            "SELECT url FROM visited_history WHERE visited_at >= ?", (since_ts,)
        ).fetchall()
        return {r[0] for r in rows}


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
