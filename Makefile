.PHONY: run run-silent dry-run run-summarize-only reset setup test ci repomix repomix-full repomix-task repomix-core install-hooks help help-en

# venv の Python が利用可能な場合はそれを使用する（activate なしで make test/ci を実行するため）
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
REPOMIX_VERSION ?= 1.12.0
.DEFAULT_GOAL := run

help:
	@cd src && python auto_fixer.py --list-commands

help-en:
	@cd src && python auto_fixer.py --list-commands-en

setup:
	pip install -r requirements.txt
	@if [ ! -f .env ]; then \
		cp .env.sample .env && echo ".env created from .env.sample"; \
	else \
		echo ".env already exists, skipping."; \
	fi

run:
	cd src && python auto_fixer.py

run-silent:
	cd src && python auto_fixer.py --silent

dry-run:
	cd src && python auto_fixer.py --dry-run

run-summarize-only:
	cd src && python auto_fixer.py --summarize-only

reset:
	cd src && python auto_fixer.py --reset

test:
	REFIX_TURSO_DATABASE_URL= REFIX_TURSO_AUTH_TOKEN= PYTHONPATH=src $(PYTHON) -m pytest -q

ci: test

# --- Repomix ---
# コードベースを AI フレンドリーな単一ファイルにまとめます。
# 用途に合わせて 3 つのバリアントを提供します：
#   - full: 全ファイル（.gitignore 以外すべて）
#   - task: AI エージェントへの機能改修相談に最適化（src/ + tests/ + 基本定義ファイル）
#   - core: ロジックのみ（src/ のみ）
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
