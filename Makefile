.PHONY: run run-silent dry-run run-summarize-only reset setup test ci lint repomix repomix-full repomix-task repomix-core prep-repomix install-hooks help help-en sync-rule

# venv の Python が利用可能な場合はそれを使用する（activate なしで make test/ci を実行するため）
PYTHON := $(if $(wildcard .venv/bin/python),$(abspath .venv/bin/python),$(shell command -v python3 || command -v python))
REPOMIX_VERSION ?= 1.12.0
.DEFAULT_GOAL := run

sync-rule:
	@sh scripts/sync_rule.sh

help:
	@echo "Refix - Makefile targets:"
	@echo ""
	@echo "  make run"
	@echo "    未処理レビューを Claude で要約・修正・push して PR の状態管理コメントに記録。"
	@echo "    デバッグレベルのログ（要約全文・プロンプト全文）を表示"
	@echo ""
	@echo "  make run-silent"
	@echo "    本番実行と同じだが、ログを最小限に抑える"
	@echo ""
	@echo "  make dry-run"
	@echo "    Claude を呼ばず、実行コマンドとダミー要約を表示"
	@echo ""
	@echo "  make run-summarize-only"
	@echo "    要約のみ実行して結果を表示（修正モデル実行・状態コメント更新なし）"
	@echo ""
	@echo "  make setup"
	@echo "    依存パッケージをインストールし、.env および .refix-batch.yaml テンプレートを作成"

help-en:
	@echo "Refix - Makefile targets:"
	@echo ""
	@echo "  make run"
	@echo "    Summarize unresolved reviews with Claude, fix and push, and record results in a PR state comment."
	@echo "    Shows debug-level logs (full prompts, summaries)."
	@echo ""
	@echo "  make run-silent"
	@echo "    Same as run, but minimize log output."
	@echo ""
	@echo "  make dry-run"
	@echo "    Show commands and dummy summaries without calling Claude."
	@echo ""
	@echo "  make run-summarize-only"
	@echo "    Run summarization only and print results."
	@echo "    Does not run fix model or update the PR state comment. (for verification)"
	@echo ""
	@echo "  make setup"
	@echo "    Install dependencies and create .env and .refix-batch.yaml templates."

setup:
	$(PYTHON) -m pip install -r requirements.txt
	@if [ ! -f .env ]; then \
		cp .env.sample .env && echo ".env created from .env.sample"; \
	else \
		echo ".env already exists, skipping."; \
	fi
	@if [ ! -f .refix-batch.yaml ]; then \
		cp .refix-batch.sample.yaml .refix-batch.yaml && echo ".refix-batch.yaml created from sample"; \
	else \
		echo ".refix-batch.yaml already exists, skipping."; \
	fi
	@printf '#!/bin/sh\nmake sync-rule\n' > .git/hooks/post-merge && chmod +x .git/hooks/post-merge
	@printf '#!/bin/sh\nmake sync-rule\n' > .git/hooks/post-checkout && chmod +x .git/hooks/post-checkout
	@echo "setup: git hooks installed"
	@make sync-rule

run:
	cd src && python auto_fixer.py

run-silent:
	cd src && python auto_fixer.py --silent

dry-run:
	cd src && python auto_fixer.py --dry-run

run-summarize-only:
	cd src && python auto_fixer.py --summarize-only

test:
	PYTHONPATH=src $(PYTHON) -m pytest -q --ignore=works

ci:
	$(PYTHON) scripts/ci.py

lint:
	$(PYTHON) -m ruff format src tests scripts
	$(PYTHON) -m ruff check src tests scripts --fix
	$(PYTHON) scripts/fix_newlines.py

# --- Repomix ---
# コードベースを AI フレンドリーな単一ファイルにまとめます。
# 用途に合わせて 3 つのバリアントを提供します：
#   - full: 全ファイル（.gitignore 以外すべて）
#   - task: AI エージェントへの機能改修相談に最適化（src/ + tests/ + .github/workflows/ + 基本定義ファイル）
#   - core: ロジック + Actions 構成のみ（src/ + .github/workflows/）
repomix: repomix-full repomix-task repomix-core
	@echo "Repomix files generated in tmp/repomix/"

repomix-full: prep-repomix
	npx --yes repomix@$(REPOMIX_VERSION) --config .repomix/full.config.json --quiet

repomix-task: prep-repomix
	npx --yes repomix@$(REPOMIX_VERSION) --config .repomix/task.config.json --quiet

repomix-core: prep-repomix
	npx --yes repomix@$(REPOMIX_VERSION) --config .repomix/core.config.json --quiet

prep-repomix:
	@mkdir -p tmp/repomix

install-hooks:
	@HOOKS_DIR="$$(git config --path --get core.hooksPath 2>/dev/null || true)"; \
	if [ -z "$$HOOKS_DIR" ]; then HOOKS_DIR="$$(git rev-parse --git-path hooks)"; fi; \
	mkdir -p "$$HOOKS_DIR" && \
	install -m 755 scripts/githooks/pre-commit "$$HOOKS_DIR/pre-commit" && \
	echo "Git hooks installed."
