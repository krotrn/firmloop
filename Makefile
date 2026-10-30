# FirmLoop task runner. Everything runs through docker compose v2.
# Run `make` or `make help` to list targets.

PROVIDER ?= gemini
TASKS ?=

# Run prefixes for backend/frontend checks against the working tree (not the baked image copy).
# Source is bind-mounted over /app; dependencies stay in anonymous volumes seeded from the image.
# Frontend calls node_modules/.bin tools directly (same commands as the package.json scripts) because
# the corepack pnpm cache lives under /root and is unreadable to the host user.
# Runs as the host user with HOME and tool caches in /tmp so nothing lands in the host tree; the
# mkdir pre-creates the anonymous-volume mountpoints so Docker does not create them root-owned.
# shared/ is mounted read-only so the backend tests can check the committed schemas are current.
HOST_USER := $(shell id -u):$(shell id -g)
BACKEND_RUN := mkdir -p backend/.venv && docker compose run --rm --user $(HOST_USER) -v $(CURDIR)/backend:/app -v /app/.venv -v $(CURDIR)/shared:/shared:ro -v $(CURDIR)/.env.example:/.env.example:ro \
	-e HOME=/tmp -e UV_NO_SYNC=1 -e UV_CACHE_DIR=/tmp/uv -e RUFF_CACHE_DIR=/tmp/ruff backend
FRONTEND_RUN := mkdir -p frontend/node_modules frontend/.next && docker compose run --rm --user $(HOST_USER) -v $(CURDIR)/frontend:/app -v /app/node_modules -v /app/.next \
	-e HOME=/tmp -e XDG_CACHE_HOME=/tmp/cache -e COREPACK_HOME=/tmp/corepack frontend

.DEFAULT_GOAL := help
.PHONY: help build up up-ollama ollama-pull try-understand down logs doctor test test-backend test-builder test-firmware test-frontend lint schemas bench bench-validate db-reset migrate

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

test: test-backend test-firmware test-frontend ## Run all tests

test-backend: ## Backend pytest inside the backend container
	$(BACKEND_RUN) uv run pytest -q -p no:cacheprovider

# Builder-marked integration tests (slow, real arduino-cli). The builder is started explicitly here
# instead of being a backend dependency, so `make test-backend` and `make up` stay fast.
test-builder: ## Builder integration tests (starts the builder service, ESP32 compile)
	docker compose up -d --wait builder
	$(BACKEND_RUN) uv run pytest -q -p no:cacheprovider -m builder tests/integration/test_builder_build.py

# The runner image bakes firmware/lib/FirmLoop/src and firmware/native; after changing them run
# `docker compose build runner` first, or the image tests stale headers and harness objects.
test-firmware: ## Firmware harness + library self-tests in the runner image
	docker compose run --rm --no-deps runner sh /repo/firmware/native/selftest/run.sh

test-frontend: ## Frontend vitest inside the frontend container
	$(FRONTEND_RUN) node_modules/.bin/vitest run

lint: ## ruff check/format, pyright, eslint, tsc --noEmit
	$(BACKEND_RUN) uv run ruff check .
	$(BACKEND_RUN) uv run ruff format --check .
	$(BACKEND_RUN) uv run pyright
	$(FRONTEND_RUN) node_modules/.bin/eslint
	$(FRONTEND_RUN) node_modules/.bin/tsc --noEmit --incremental false

schemas: ## Export OpenAPI + JSON Schemas to shared/schemas
	mkdir -p backend/.venv shared/schemas && docker compose run --rm --user $(HOST_USER) -v $(CURDIR)/backend:/app -v /app/.venv -v $(CURDIR)/shared:/shared -v $(CURDIR)/.env.example:/.env.example:ro \
		-e HOME=/tmp -e UV_NO_SYNC=1 -e UV_CACHE_DIR=/tmp/uv backend uv run python scripts/export_schemas.py /shared/schemas

try-understand: ## Understand step on real requests: make try-understand [PROVIDER=ollama] [MODEL=qwen2.5:3b] [DELAY=20] [REQ='"turn the fan on above 30"'] (default: sample file)
	$(BACKEND_RUN) uv run python scripts/try_understand.py $(if $(PROVIDER),--provider $(PROVIDER)) $(if $(MODEL),--model $(MODEL)) $(if $(DELAY),--delay $(DELAY)) $(if $(REQ),$(REQ),--file scripts/understand_samples.txt)

ollama-pull: ## Pull a model into the Ollama container: make ollama-pull MODEL=qwen2.5-coder:7b
	docker compose --profile ollama exec ollama ollama pull $(MODEL)

bench: ## Run benchmark: make bench PROVIDER=gemini [TASKS=t01,t02] (not implemented yet, benchmark tasks)
	@echo "bench: not implemented yet (benchmark tasks)"
	@exit 1

bench-validate: ## Check benchmark reference logic and mutants (not implemented yet, benchmark tasks)
	@echo "bench-validate: not implemented yet (benchmark tasks)"
	@exit 1

db-reset: ## Delete ./data (asks first) (not implemented yet)
	@echo "db-reset: not implemented yet"
	@exit 1

migrate: ## Apply Alembic migrations (alembic upgrade head)
	mkdir -p data
	$(BACKEND_RUN) uv run alembic upgrade head
