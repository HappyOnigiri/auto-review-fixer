.PHONY: run dry-run reset setup help

help:
	@echo "Auto Review Fixer - Makefile targets:"
	@echo "  make setup    - Install dependencies and create .env template"
	@echo "  make run      - Run auto review fixer with repos from repos.txt"
	@echo "  make dry-run  - Show what would be executed without actually running"
	@echo "  make reset    - Reset processed reviews database"
	@echo "  make help     - Show this help message"

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

dry-run:
	cd src && python auto_fixer.py --dry-run

reset:
	cd src && python auto_fixer.py --reset

.DEFAULT_GOAL := help
