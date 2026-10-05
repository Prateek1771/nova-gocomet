# Same commands locally and in CI (docs/10 Phase 2). Windows: `winget install ezwinports.make`, or run the
# lines below directly.
COMPOSE = docker compose -f infra/docker-compose.yml --profile core

.PHONY: up down logs seed test test-int lint fmt ci

up:            ## core stack: postgres, redis, keycloak, api (+migrate/seed), web
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

seed:          ## re-run migrations + seed against the running stack
	$(COMPOSE) run --rm migrate

test:          ## unit tests (no Docker)
	uv run pytest tests/unit
	pnpm -C apps/web test

test-int:      ## testcontainers: Postgres RLS + Keycloak realm
	uv run pytest tests/integration

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run lint-imports
	pnpm -C apps/web lint
	pnpm -C apps/web typecheck

fmt:
	uv run ruff check --fix .
	uv run ruff format .

ci: lint test test-int
	pnpm -C apps/web build
