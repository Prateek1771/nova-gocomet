# Same commands locally and in CI (docs/10 Phase 2). Windows: `winget install ezwinports.make`, or run the
# lines below directly.
COMPOSE = docker compose -f infra/docker-compose.yml --profile core

.PHONY: up up-full down logs seed test test-int lint fmt gen ci bols invoices reindex bench-llm redteam audit-verify

up:            ## core stack: postgres, redis, keycloak, temporal, api (+migrate/seed), engine, web
	$(COMPOSE) up -d --build

up-full:       ## core + ai profile (Langfuse on :3400, collector also exports to it)
	OTEL_COLLECTOR_CONFIG=collector.ai.yaml LANGFUSE_URL=http://localhost:3400 $(COMPOSE) --profile ai up -d --build

down:
	$(COMPOSE) --profile ai --profile data down

logs:
	$(COMPOSE) logs -f --tail=100

seed:          ## re-run migrations + seed against the running stack
	$(COMPOSE) run --rm migrate

test:          ## unit + engine (Temporal time-skipping) tests, no Docker
	uv run pytest tests/unit
	uv run coverage run -m pytest tests/engine
	uv run coverage report
	pnpm -C apps/web test

test-int:      ## testcontainers: Postgres (RLS, engine + API) + Keycloak realm
	uv run pytest tests/integration

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run lint-imports
	pnpm -C apps/web lint
	pnpm -C apps/web typecheck

gen:           ## regenerate DSL JSON Schema, OpenAPI and the web TS types
	uv run python -m nova_dsl.schema
	uv run python -m nova_api.openapi
	docker run --rm -v "$(CURDIR)/infra/openfga:/m:ro" openfga/cli:v0.7.3 model transform --file /m/model.fga > infra/openfga/model.json
	pnpm -C apps/web gen

bols:          ## render the 10 W1 seed BoLs (definitions/seed/bol_cases.json) to data/seed/bol
	uv run python scripts/gen_bols.py

invoices:      ## render the 8 W2 seed invoices (definitions/seed/invoice_cases.json) to data/seed/invoice
	uv run python scripts/gen_invoices.py

reindex:       ## re-embed every tenant's contract clauses into Weaviate (idempotent; needs the stack up)
	WEAVIATE_URL=http://localhost:8090 LLM_BASE_URL=http://localhost:4100 uv run python -m nova_api.clauses

bench-llm:     ## live LLM eval + timings against the running gateway (~$0.01 in cheap) -> docs/evals
	LLM_BASE_URL=http://localhost:4100 JEV_MODEL=typesafe/jev-1.13 uv run python scripts/eval_llm.py

redteam:       ## 10 prompt-injection BoLs through the live stack; fails on any unauthorised state change
	uv run python scripts/redteam_bols.py

audit-verify:  ## recompute every tenant's audit hash chain; non-zero exit on a break
	uv run python scripts/verify_audit.py

fmt:
	uv run ruff check --fix .
	uv run ruff format .

ci: lint test test-int
	pnpm -C apps/web build
