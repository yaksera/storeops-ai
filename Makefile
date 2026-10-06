.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND := cd backend &&
FRONTEND := cd frontend &&

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Docker (recommended) ---------------------------------------------------

.PHONY: dev
dev: ## Start the full stack (Postgres, Redis, API, worker, web) with Docker Compose
	docker compose up --build

.PHONY: down
down: ## Stop the stack
	docker compose down

.PHONY: reset
reset: ## Stop the stack and delete its volumes (database included)
	docker compose down -v

.PHONY: logs
logs: ## Tail logs from all services
	docker compose logs -f --tail=100

# --- Native (no Docker for the app processes) -------------------------------

.PHONY: install
install: ## Install backend and frontend dependencies
	$(BACKEND) uv sync
	$(FRONTEND) npm install

.PHONY: infra
infra: ## Start only Postgres and Redis in Docker
	docker compose up -d postgres redis

.PHONY: migrate
migrate: ## Apply database migrations
	$(BACKEND) uv run alembic upgrade head

.PHONY: api
api: ## Run the API with autoreload on :8000
	$(BACKEND) uv run uvicorn app.main:app --reload --port 8000

.PHONY: worker
worker: ## Run the background worker with autoreload
	$(BACKEND) uv run arq app.worker.main.WorkerSettings --watch app

.PHONY: web
web: ## Run the web app on :3000
	$(FRONTEND) npm run dev

# --- Quality ----------------------------------------------------------------

.PHONY: lint
lint: ## Lint and type-check everything
	$(BACKEND) uv run ruff check . && uv run ruff format --check . && uv run mypy app tests
	$(FRONTEND) npm run lint && npm run typecheck && npm run format:check

.PHONY: format
format: ## Auto-format backend and frontend
	$(BACKEND) uv run ruff check --fix . && uv run ruff format .
	$(FRONTEND) npm run format

.PHONY: seed
seed: ## Create demo@northbound.example and a seeded demo store
	$(BACKEND) uv run python -m app.cli seed

.PHONY: bench
bench: ## Measure event-pipeline throughput against the local database
	$(BACKEND) uv run python -m app.cli bench -n 1000 -c 20

.PHONY: test
test: ## Run backend (needs Postgres; see backend/README.md) and frontend unit tests
	$(BACKEND) uv run pytest --cov --cov-report=term-missing
	$(FRONTEND) npm test

.PHONY: e2e
e2e: ## Run Playwright end-to-end tests against a running stack on :3000
	$(FRONTEND) npm run test:e2e

.PHONY: check
check: lint test ## Everything CI runs
