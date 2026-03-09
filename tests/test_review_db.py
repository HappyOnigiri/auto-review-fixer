"""Unit tests for review_db using local SQLite test DB."""

import os
from pathlib import Path

import pytest

import review_db


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """Use a temp SQLite DB and reset connection for each test."""
    monkeypatch.delenv("REFIX_TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("REFIX_TURSO_AUTH_TOKEN", raising=False)
    db_file = tmp_path / "reviews_test.db"
    monkeypatch.setenv("REVIEW_DB_PATH", str(db_file))
    review_db.reset_connection()
    yield
    review_db.reset_connection()


def test_init_db_creates_table():
    review_db.init_db()
    conn = review_db.get_connection()
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_reviews'"
    ).fetchone()
    assert result is not None


def test_mark_processed_then_is_processed():
    review_db.init_db()
    assert not review_db.is_processed("rev-123")
    review_db.mark_processed("rev-123", "owner/repo", 1, body="b", summary="s")
    assert review_db.is_processed("rev-123")


def test_count_processed_for_pr():
    review_db.init_db()
    review_db.mark_processed("r1", "a/b", 10)
    review_db.mark_processed("r2", "a/b", 10)
    review_db.mark_processed("r3", "a/b", 20)
    assert review_db.count_processed_for_pr("a/b", 10) == 2
    assert review_db.count_processed_for_pr("a/b", 20) == 1
    assert review_db.count_processed_for_pr("x/y", 10) == 0


def test_reset_all_clears_records():
    review_db.init_db()
    review_db.mark_processed("r1", "a/b", 1)
    review_db.mark_processed("r2", "a/b", 1)
    assert review_db.is_processed("r1")
    review_db.reset_all()
    assert not review_db.is_processed("r1")
    assert not review_db.is_processed("r2")
    assert review_db.count_processed_for_pr("a/b", 1) == 0


def test_no_turso_connection_in_tests():
    """Ensure tests never connect to Turso (no sync_url/auth_token)."""
    review_db.init_db()
    review_db.get_connection()
    # We use local SQLite only; _turso_mode should be False
    assert review_db._turso_mode is False
