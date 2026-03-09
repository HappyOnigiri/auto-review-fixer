#!/usr/bin/env python3
"""
Review DB - Turso Cloud / local SQLite connection and processed review tracking.

Connection mode is determined by environment variables:
- Both REFIX_TURSO_DATABASE_URL and REFIX_TURSO_AUTH_TOKEN set → Turso Cloud
- Otherwise → local SQLite at data/reviews.db
"""

import os
import sys
from pathlib import Path

import libsql

_conn = None
_turso_mode = False


def _get_db_path() -> Path:
    """Return DB file path. REVIEW_DB_PATH env overrides default (for tests)."""
    override = os.environ.get("REVIEW_DB_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).parent.parent / "data" / "reviews.db"


def reset_connection():
    """Reset module-level connection state. Use in tests to get a fresh DB."""
    global _conn, _turso_mode
    _conn = None
    _turso_mode = False


def get_connection():
    """Connect to Turso Cloud or local SQLite (singleton)."""
    global _conn, _turso_mode
    if _conn is not None:
        return _conn

    url = os.environ.get("REFIX_TURSO_DATABASE_URL", "")
    auth_token = os.environ.get("REFIX_TURSO_AUTH_TOKEN", "")

    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if url and auth_token:
        print(f"Connecting to Turso Cloud (embedded replica): {url}")
        _conn = libsql.connect(database=str(db_path), sync_url=url, auth_token=auth_token)
        _turso_mode = True
        _conn.sync()  # pull latest from Turso at startup
    else:
        print(f"Using local SQLite: {db_path}")
        _conn = libsql.connect(database=str(db_path))

    return _conn


def _sync_if_turso():
    """Push local changes to Turso Cloud if in Turso mode."""
    if _turso_mode and _conn is not None:
        _conn.sync()


def init_db():
    """Create the processed_reviews table if it doesn't exist."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_reviews (
            review_id TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            pr_number INTEGER NOT NULL,
            body TEXT,
            summary TEXT,
            processed_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Migrate existing DB: add missing columns
    for col in ("body TEXT", "summary TEXT"):
        try:
            conn.execute(f"ALTER TABLE processed_reviews ADD COLUMN {col}")
        except Exception as e:
            if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                pass  # Column already exists, safe to ignore
            else:
                print(f"Warning: unexpected error adding column '{col}': {type(e).__name__}: {e}", file=sys.stderr)
                raise
    conn.commit()


def is_processed(review_id: str) -> bool:
    """Return True if the review has already been processed."""
    conn = get_connection()
    result = conn.execute(
        "SELECT 1 FROM processed_reviews WHERE review_id = ?", [review_id]
    ).fetchone()
    return result is not None


def mark_processed(review_id: str, repo: str, pr_number: int, body: str = "", summary: str = ""):
    """Record a review as processed."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO processed_reviews (review_id, repo, pr_number, body, summary) VALUES (?, ?, ?, ?, ?)",
        [review_id, repo, pr_number, body, summary],
    )
    conn.commit()
    _sync_if_turso()  # push to Turso Cloud


def count_processed_for_pr(repo: str, pr_number: int) -> int:
    """Return the number of processed reviews for a given PR."""
    conn = get_connection()
    result = conn.execute(
        "SELECT COUNT(*) FROM processed_reviews WHERE repo = ? AND pr_number = ?",
        [repo, pr_number],
    ).fetchone()
    return result[0] if result else 0


def reset_all():
    """Delete all processed review records."""
    conn = get_connection()
    conn.execute("DELETE FROM processed_reviews")
    conn.commit()
    _sync_if_turso()  # push to Turso Cloud
