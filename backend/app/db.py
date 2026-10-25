import hashlib
import hmac
import sqlite3
import time
from contextlib import contextmanager

from .config import settings

TRANSITIONS = {
    "queued": {"transcribing", "failed", "deleted"},
    "transcribing": {"preparing", "failed", "deleted"},
    "preparing": {"ready", "failed", "deleted"},
    "ready": {"deleted"},
    "failed": {"ready", "deleted"},
    "deleted": set(),
}


@contextmanager
def connection():
    cfg = settings()
    cfg.storage_path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(cfg.db_path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, token_hash TEXT NOT NULL,
            title TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, expires_at REAL NOT NULL, error TEXT,
            result_location TEXT, mock INTEGER NOT NULL, dispatched_at REAL
        )""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        if "dispatched_at" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN dispatched_at REAL")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def get_job(job_id: str):
    with connection() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def find_request(key: str):
    with connection() as db:
        row = db.execute("SELECT * FROM jobs WHERE request_key = ?", (key,)).fetchone()
    return dict(row) if row else None


def authorized(job, token: str) -> bool:
    return bool(job and hmac.compare_digest(job["token_hash"], digest(token)))


def create_job(job_id: str, key: str, token: str, title: str):
    now = time.time()
    with connection() as db:
        db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, NULL, NULL, ?, NULL)",
            (
                job_id,
                key,
                digest(token),
                title,
                now,
                now,
                now + settings().pending_retention_hours * 3600,
                settings().mock_mode,
            ),
        )
    return get_job(job_id)


def transition(job_id: str, target: str, *, error: str | None = None, result_location: str | None = None):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row or target not in TRANSITIONS[row["state"]]:
            raise ValueError("Invalid job transition")
        now = time.time()
        expiry = (
            now + settings().result_retention_hours * 3600
            if target in {"ready", "failed"}
            else row["expires_at"]
        )
        db.execute(
            """UPDATE jobs SET state=?, updated_at=?, expires_at=?, error=?, result_location=?
                      WHERE id=?""",
            (target, now, expiry, error, result_location, job_id),
        )


def cleanup_candidates(now: float):
    with connection() as db:
        return [
            dict(r)
            for r in db.execute("SELECT * FROM jobs WHERE expires_at <= ? OR state = 'deleted'", (now,))
        ]


def remove_job(job_id: str):
    with connection() as db:
        db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def mark_dispatched(job_id: str):
    with connection() as db:
        db.execute("UPDATE jobs SET dispatched_at=? WHERE id=?", (time.time(), job_id))


def undispatched_jobs(now: float):
    with connection() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM jobs WHERE state='queued' AND dispatched_at IS NULL AND created_at < ? AND expires_at > ?",
                (now - 60, now),
            )
        ]
