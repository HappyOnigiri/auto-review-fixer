.PHONY: run run-silent dry-run run-summarize-only reset setup test ci repomix repomix-full repomix-task repomix-core prep-repomix install-hooks help help-en sync-ruler

# venv の Python が利用可能な場合はそれを使用する（activate なしで make test/ci を実行するため）
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
REPOMIX_VERSION ?= 1.12.0
.DEFAULT_GOAL := run

sync-ruler:
	$(PYTHON) scripts/sync_ruler.py

help:
	@cd src && python auto_fixer.py --list-commands

help-en:
	@cd src && python auto_fixer.py --list-commands-en

setup:
	$(PYTHON) -m pip install -r requirements.txt
	@if [ ! -f .env ]; then \
		cp .env.sample .env && echo ".env created from .env.sample"; \
	else \
		echo ".env already exists, skipping."; \
	fi
	@if [ ! -f .refix.yaml ]; then \
		cp .refix.yaml.sample .refix.yaml && echo ".refix.yaml created from sample"; \
	else \
		echo ".refix.yaml already exists, skipping."; \
	fi

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

ci: test

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
