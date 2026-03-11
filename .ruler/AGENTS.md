# AGENTS.md

## Overview

Auto Review Fixer (refix) — GitHub PR 上の CodeRabbit レビューコメントを Claude AI で自動修正する Python CLI ツール。単一サービス構成。

## Prerequisites

- Python 3.12
- venv (`.venv/`) — `make setup` または `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` で作成
- `gh` CLI（認証済み）— 実行時に GitHub API 呼び出しで使用
- `.env` ファイル — `.env.sample` からコピー（ローカル開発・テストでは全項目オプション）

## Key commands

主要コマンドは `Makefile` に定義済み。`make help-en`（英語）/ `make help`（日本語）で一覧表示。

| Command | Description |
|---|---|
| `make setup` | pip 依存インストール + `.env` テンプレート作成 |
| `make test` | pytest 実行（外部呼び出しは全モック、シークレット不要） |
| `make dry-run` | Claude を呼ばずにアプリ実行（`REPOS` 環境変数 or `repos.txt` が必要） |
| `make run` | 本番実行（Claude CLI + gh CLI + 認証情報が必要） |

## Caveats

- **リンター未設定** — CI は `make test` のみ実行。
- テストは `conftest.py` で `REFIX_TURSO_DATABASE_URL` / `REFIX_TURSO_AUTH_TOKEN` を自動 unset するため、常にローカル SQLite を使用。
- `Makefile` は `.venv/bin/python` を自動検出するため、venv の activate なしで `make test` が動作する。
- SQLite DB は `data/reviews.db` に自動作成される。
- スモークテスト: `REPOS="octocat/Hello-World" make dry-run`

## Pull Request 運用

- PR 作成時は Draft を使わず、最初から **Ready for review** の状態で作成する。
