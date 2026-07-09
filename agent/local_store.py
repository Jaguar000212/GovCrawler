"""
Durable local write-ahead buffer for crawl-engine writes (leads, visited URLs).

Plain sqlite3 (NOT SQLAlchemy/Alembic — deliberately kept out of the app's
main DB machinery per plan.md §8, since it's a per-machine resilience buffer,
not shared schema). `PRAGMA synchronous=FULL` so a queued row survives a power
loss / process kill, not just a clean crash.

One row per pending write; a background flusher (owned by CloudApiClient)
drains oldest-first in batches, deletes on ack, and dead-letters a row after
too many failed attempts so one poison record can't block the whole queue.
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 8

_SCHEMA = """
          CREATE TABLE IF NOT EXISTS outbox
          (
              id
              INTEGER
              PRIMARY
              KEY
              AUTOINCREMENT,
              job_id
              INTEGER
              NOT
              NULL,
              kind
              TEXT
              NOT
              NULL,
              payload_json
              TEXT
              NOT
              NULL,
              created_at
              REAL
              NOT
              NULL,
              attempts
              INTEGER
              NOT
              NULL
              DEFAULT
              0,
              last_error
              TEXT
          );
          CREATE INDEX IF NOT EXISTS ix_outbox_job_kind ON outbox (job_id, kind);

          CREATE TABLE IF NOT EXISTS outbox_dead
          (
              id
              INTEGER
              PRIMARY
              KEY
              AUTOINCREMENT,
              job_id
              INTEGER
              NOT
              NULL,
              kind
              TEXT
              NOT
              NULL,
              payload_json
              TEXT
              NOT
              NULL,
              last_error
              TEXT,
              died_at
              REAL
              NOT
              NULL
          );

          CREATE TABLE IF NOT EXISTS frontier
          (
              job_id
              INTEGER
              PRIMARY
              KEY,
              snapshot_json
              TEXT
              NOT
              NULL,
              saved_at
              REAL
              NOT
              NULL
          ); \
          """


class LocalOutbox:
    """Thread-safe (single lock, matches the engine's single db_pool thread)."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def enqueue(self, job_id: int, kind: str, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO outbox (job_id, kind, payload_json, created_at, attempts) "
                "VALUES (?, ?, ?, ?, 0)",
                (job_id, kind, json.dumps(payload), time.time()),
            )
            self._conn.commit()

    def pending_batch(self, kind: str, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, job_id, payload_json, attempts FROM outbox "
                "WHERE kind = ? ORDER BY id ASC LIMIT ?",
                (kind, limit),
            ).fetchall()
            return [
                {"id": r[0], "job_id": r[1], "payload": json.loads(r[2]), "attempts": r[3]}
                for r in rows
            ]

    def ack(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._lock:
            self._conn.executemany("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])
            self._conn.commit()

    def fail(self, row_id: int, job_id: int, kind: str, payload: dict, error: str) -> None:
        with self._lock:
            attempts = self._conn.execute(
                "SELECT attempts FROM outbox WHERE id = ?", (row_id,)
            ).fetchone()
            attempts = (attempts[0] if attempts else 0) + 1
            if attempts >= MAX_ATTEMPTS:
                self._conn.execute(
                    "INSERT INTO outbox_dead (job_id, kind, payload_json, last_error, died_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (job_id, kind, json.dumps(payload), error, time.time()),
                )
                self._conn.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
                log.error(f"outbox: dead-lettering {kind} row for job {job_id} after {attempts} attempts: {error}")
            else:
                self._conn.execute(
                    "UPDATE outbox SET attempts = ?, last_error = ? WHERE id = ?",
                    (attempts, error, row_id),
                )
            self._conn.commit()

    def is_drained(self, job_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE job_id = ?", (job_id,)
            ).fetchone()
            return row[0] == 0

    def pending_count(self) -> int:
        """Total rows across all jobs — used for outbox backpressure, which
        cares about total local backlog, not any one job's share of it."""
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def save_frontier(self, job_id: int, snapshot: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO frontier (job_id, snapshot_json, saved_at) VALUES (?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET snapshot_json = excluded.snapshot_json, "
                "saved_at = excluded.saved_at",
                (job_id, json.dumps(snapshot), time.time()),
            )
            self._conn.commit()

    def load_frontier(self, job_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT snapshot_json FROM frontier WHERE job_id = ?", (job_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def clear_frontier(self, job_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM frontier WHERE job_id = ?", (job_id,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
