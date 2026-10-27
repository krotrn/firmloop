# FirmLoop task runner. Everything runs through docker compose v2 (see CLAUDE.md §4).
# Run `make` or `make help` to list targets.

PROVIDER ?= gemini
TASKS ?=

.DEFAULT_GOAL := help
.PHONY: help build up up-ollama down logs doctor test test-backend test-firmware test-frontend lint schemas bench bench-validate db-reset migrate

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-15s %s\n", $$1, $$2}'

build: ## Build all images
	docker compose build

up: ## Start all services in the background (creates ./data)
	mkdir -p data
	docker compose up -d

up-ollama: ## Start all services plus the Ollama container (profile ollama)
	mkdir -p data
	docker compose --profile ollama up -d

down: ## Stop all services
	docker compose down

logs: ## Follow service logs
	docker compose logs -f

doctor: ## Environment check, runs on the host
	uv run --project backend python backend/scripts/doctor.py

test: test-backend test-frontend ## Run all tests (backend + frontend for now)

test-backend: ## Backend pytest inside the backend container
	docker compose run --rm backend uv run pytest -q

test-firmware: ## Firmware harness self-tests (not implemented yet, task 1.8)
	@echo "test-firmware: not implemented yet (task 1.8)"
	@exit 1

test-frontend: ## Frontend vitest inside the frontend container
	docker compose run --rm frontend pnpm test

lint: ## ruff check/format, pyright, eslint, tsc --noEmit
	docker compose run --rm backend uv run ruff check .
	docker compose run --rm backend uv run ruff format --check .
	docker compose run --rm backend uv run pyright
	docker compose run --rm frontend pnpm lint
	docker compose run --rm frontend pnpm typecheck

schemas: ## Export OpenAPI + JSON Schemas and regenerate frontend types (not implemented yet, task 5.8)
	@echo "schemas: not implemented yet (task 5.8)"
	@exit 1

bench: ## Run benchmark: make bench PROVIDER=gemini [TASKS=t01,t02] (not implemented yet, benchmark tasks)
	@echo "bench: not implemented yet (benchmark tasks)"
	@exit 1

bench-validate: ## Check benchmark reference logic and mutants (not implemented yet, benchmark tasks)
	@echo "bench-validate: not implemented yet (benchmark tasks)"
	@exit 1

db-reset: ## Delete ./data (asks first) (not implemented yet, task 4.1)
	@echo "db-reset: not implemented yet (task 4.1)"
	@exit 1

migrate: ## Apply Alembic migrations (alembic upgrade head) (not implemented yet, task 4.1)
	@echo "migrate: not implemented yet (task 4.1)"
	@exit 1
