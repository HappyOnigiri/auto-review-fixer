# AGENTS.md

## Cursor Cloud specific instructions

### Overview

Auto Review Fixer (refix) — a Python CLI tool that auto-fixes CodeRabbit review comments on GitHub PRs using Claude AI. Single-service, non-monorepo project.

### Prerequisites

- Python 3.12 with venv at `.venv/`
- Dependencies listed in `requirements.txt` (libsql, python-dotenv, pytest)
- `gh` CLI (pre-authenticated) is required at runtime for GitHub API calls
- `.env` file (copy from `.env.sample` if missing; all values are optional for local dev/testing)

### Key commands

All commands are in the `Makefile`:

| Command | Description |
|---|---|
| `make test` | Run pytest (all external calls mocked, no secrets needed) |
| `make dry-run` | Run the app without calling Claude (requires `REPOS` env var or `repos.txt`) |
| `make help-en` | Show available targets in English |
| `make setup` | Install pip deps and create `.env` from sample |

### Caveats

- **No linter is configured** in this project. CI only runs `make test`.
- Tests unset `REFIX_TURSO_DATABASE_URL` and `REFIX_TURSO_AUTH_TOKEN` automatically via `conftest.py`, so tests always use local SQLite.
- The `Makefile` auto-detects `.venv/bin/python` if present; you do not need to activate the venv for `make test`.
- The SQLite database is stored at `data/reviews.db` (created automatically on first run).
- For a quick smoke test of the app, use `REPOS="octocat/Hello-World" make dry-run`.
