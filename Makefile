# =============================================================================
# Shortcuts. Every target is a command you could type yourself -- the Makefile
# is a reminder, not a layer.
# =============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help install env lint format typecheck test test-integration coverage check \
        catalogue init-db demo demo-ready assess findings readiness posture risks \
        controls evidence export history serve doctor reset \
        up down clean-volumes logs psql docker-build clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Development -------------------------------------------------------------

install: ## Install the package and its development dependencies
	python -m pip install --upgrade pip
	pip install -e ".[dev]"

env: ## Create .env from the example if it does not exist
	@test -f .env || (cp .env.example .env && echo "created .env -- edit it before running")

lint: ## Run ruff (lint + format check)
	python -m ruff check .
	python -m ruff format --check .

format: ## Apply ruff formatting and safe fixes
	python -m ruff check --fix .
	python -m ruff format .

typecheck: ## Run mypy on src/
	python -m mypy

test: ## Run the unit tests (no database needed)
	python -m pytest tests/unit -v

test-integration: ## Run the full suite (requires a live PostgreSQL)
	SENTINEL_RUN_INTEGRATION=1 python -m pytest tests -v

coverage: ## Full suite with a coverage report
	SENTINEL_RUN_INTEGRATION=1 python -m pytest tests --cov --cov-report=term-missing

check: lint typecheck test ## Everything CI runs, minus the database

# --- The ISMS ----------------------------------------------------------------

doctor: ## Check configuration, the database, the catalogue and the registers
	sentinel doctor

catalogue: ## Show the control catalogue
	sentinel catalogue

init-db: ## Create the ISMS schema and its tables
	sentinel init-db

demo: ## Seed the simulated organisation mid-programme (gaps planted on purpose)
	sentinel demo --profile gaps

demo-ready: ## Seed the same organisation after remediation
	sentinel demo --profile ready

assess: ## Run every rule and the gate
	sentinel assess

findings: ## Show the findings from the last assessment
	sentinel findings

readiness: ## Would the ISMS survive an audit today? (exit 3 when not)
	sentinel readiness

posture: ## Coverage by capability, and what is weakest
	sentinel posture

risks: ## Show the risk register
	sentinel risks

controls: ## Show the applicability decisions
	sentinel controls

evidence: ## Show evidence coverage and freshness
	sentinel evidence --stale

export: ## Write the SoA, the treatment plan, the findings and the dossier
	sentinel export

history: ## Past assessments -- the trend is the point
	sentinel history

serve: ## Run the read-only posture API
	sentinel serve --host 0.0.0.0 --port 8000

reset: ## Drop the ISMS schema (asks first)
	sentinel reset

# --- Docker ------------------------------------------------------------------

up: env ## Start the stack: database, one assessment, then the API
	docker compose up --build

down: ## Stop the stack (keeps the database volume)
	docker compose down

clean-volumes: ## Stop the stack AND delete the database volume
	docker compose down -v

logs: ## Follow the container logs
	docker compose logs -f

psql: ## Open a psql shell on the compose database
	docker compose exec postgres psql -U $${SENTINEL_DB_USER:-sentinel_app} -d $${SENTINEL_DB_NAME:-sentinel}

docker-build: ## Build the application image only
	docker build -t iso27001-cybersecurity-copilot:local .

# --- Housekeeping ------------------------------------------------------------

clean: ## Remove caches and generated documents (keeps .env)
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml junit-*.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -f data/exports/*.md data/exports/*.csv
