.PHONY: run run-silent dry-run run-summarize-only reset setup repomix help help-en

help:
	@cd src && python auto_fixer.py --list-commands

help-en:
	@cd src && python auto_fixer.py --list-commands-en

setup:
	pip install -r requirements.txt
	@if [ ! -f .env ]; then \
		echo "# Turso Cloud settings (leave unset to use local SQLite)" > .env; \
		echo "# TURSO_DATABASE_URL=libsql://your-db-name.turso.io" >> .env; \
		echo "# TURSO_AUTH_TOKEN=your-auth-token" >> .env; \
		echo ".env template created."; \
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

REPOMIX_VERSION ?= 1.12.0

repomix:
	@mkdir -p tmp/repomix
	npx repomix@$(REPOMIX_VERSION) -o tmp/repomix/repomix-output.xml --quiet

.DEFAULT_GOAL := run
