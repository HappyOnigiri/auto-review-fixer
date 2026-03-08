"""Pytest configuration and fixtures for auto-review-fixer tests."""

import os
import sys
from pathlib import Path

# Add src to path so tests can import auto_fixer, review_db, summarizer, etc.
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


def pytest_configure(config):
    """Ensure Turso env vars are unset during tests to avoid cloud DB connection."""
    os.environ.pop("TURSO_DATABASE_URL", None)
    os.environ.pop("TURSO_AUTH_TOKEN", None)
