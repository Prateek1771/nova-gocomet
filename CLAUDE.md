# CLAUDE.md — Nova prototype

Nova is GoComet's governed AI platform for enterprise logistics. This repo is a working prototype of it: a generic YAML workflow engine on Temporal, governed LangGraph agents, micro-apps for human steps, and a tenant-isolated data layer. It runs three workflows: W1 BoL intake, W2 invoice ↔ PO match + approval, and W3 shipment exception monitoring. The source brief is `note.md`.

**Status:** building, one milestone at a time, against `docs/07-build-plan.md`. M0 (skeleton: Keycloak login, BFF, FastAPI, RLS), M1 (DSL, generic Temporal interpreter, workflow/run/task API) M2 (W1 BoL slice: uploads, governed agents, Jev decide, inbox + bol_review micro-app, evals) and M3 (Studio: React Flow + Monaco editor over canonical YAML, catalog, publish/versions, live SSE run graph) and M4 (Governance: OpenFGA authz with approval limits, Bolt tenant, audit hash chain, per-tenant LLM budgets, Langfuse + OTel collector, staging MFA realm) and M5 (W2 invoice ↔ PO match: invoice_matcher with Weaviate clause search + grounded citations, approval matrix, Bolt L3 subflow, ComparisonTable, dispute emails) are done. Next: M6 (Data layer + W3). Tick tasks in 07 as they land.

## Docs (read before changing anything)

| Need | Doc |
|---|---|
| Scope, decisions, open questions | `docs/00-brainstorm.md` (check before changing scope) |
| Requirements (FR-x.x), demo script | `docs/01-prd.md` |
| Architecture | `docs/02-hld.md` |
| Repo layout, DSL, schemas, API, authz | `docs/03-lld.md` |
| The 3 workflows + planted errors | `docs/04-workflow-specs.md` |
| Phased plan M0–M7: tasks, tests, exit criteria | `docs/07-build-plan.md` (the single source of "done") |
| Security, AI governance, CI/CD, ops | `docs/08-enterprise-execution-plan.md` |
| Generic engine vs per-process config | `docs/09-standard-domain-model.md` |
| Full checklist by area, incl. CI | `docs/10-implementation-checklist.md` |


## Non-negotiable rules

1. **The engine is generic.** `services/engine` never names a process, doc type or tenant. New processes go in `definitions/` (workflow YAML, doc type, schema, micro-app) and TenantConfig.
2. **No business thresholds in code.** Approval matrices and limits live in versioned TenantConfig (read by CEL) and OpenFGA `within_limit`.
3. **Deterministic before probabilistic.** Code checks run first, and only the residue goes to an LLM.
4. **Agents recommend and never approve.** State changes happen only in the engine (CEL rules, human tasks).
5. **Tenant isolation in the store.** Every row, key and message carries `tenant_id`. Use Postgres RLS (the app role `nova_app` is not the table owner), ClickHouse row policies, and Weaviate tenants.
6. **Code names LiteLLM aliases only** (`nova-extract-text`, `nova-extract-vision`, `nova-reason`, `nova-decide`, `nova-embed`), never provider models.
7. **YAML is canonical.** The graph is a view. Edit through `yaml` `parseDocument()` mutations, never parse→stringify.
8. **Side effects are idempotent** via `action_executions.idempotency_key`.
9. **Module boundaries:** `services/*` depend only on `packages/*`, never on each other. `nova_dsl` has no I/O.
10. **AuthZ only via OpenFGA.** Keycloak says who you are and which roles you hold; every allow/deny is an OpenFGA check (roles as contextual tuples). Never authorise on a JWT role alone; approval limits come from TenantConfig.
11. **Tokens never reach the browser.** Next.js is an OIDC BFF (Redis session, httpOnly cookie).
12. **Temporal determinism:** CEL runs inside workflow code. LLM, DB and HTTP calls are always activities.

## Stack

Next.js (App Router) + React 19 + React Flow + shadcn/Tailwind v4 · FastAPI · **Keycloak** (OIDC, org per tenant) · Temporal · LangGraph · LiteLLM → OpenRouter (Jev for `decide` nodes) · Postgres · Kafka + Debezium · ClickHouse + dbt · Weaviate · OpenFGA · Langfuse + OTel · MinIO · Ollama. Python uses a **uv** workspace and the front end uses **pnpm**. Everything runs in Docker Compose with profiles `core`, `data` and `ai`.

## Cost: keep LLM spend near zero

- `LLM_MODE=local|free|cheap|demo`. **`free` is the dev default** ($0: Groq free tier, OpenRouter `:free` fallback; synthetic docs only, ADR-025). `openai` (ADR-033) is paid-quality testing on gpt-4.1-mini/nano, where per-tenant budgets really trip (`LLM_MODE=openai docker compose …`). `cheap`/`demo` are the paid OpenRouter paths for later; use `demo` only for recording the demo.
- Embeddings use Ollama `nomic-embed-text`.
- Dev machine: 32 GB RAM, 4-core i5, no GPU, so local models are CPU-only and slow. Tests use recorded LLM responses (VCR); don't add live LLM calls to unit tests.

## Commands

`make up` (core stack) · `make down` · `make seed` · `make test` (unit + Temporal time-skipping engine tests, 100% branch gate) · `make test-int` (testcontainers) · `make lint` · `make gen` (DSL schema, OpenAPI, web TS types; CI fails on drift) · `make ci`. `make bols` / `make invoices` (seed PDFs) · `make reindex` (clauses → Weaviate) · `make bench-llm` (live eval → `docs/evals/`) · `make redteam` (injection suite, live). `make up-full` (core + ai: Langfuse on :3400, login dev@nova.test / nova-dev-password) · `make audit-verify`. Coming later: `make up-local`, `make demo`. No `make` on Windows: `winget install ezwinports.make`, or run the Makefile lines directly.

Local ports: web <http://localhost:3300> · api 8100 (Swagger `/api/docs`; token: `uv run python scripts/dev_token.py ops@acme`) · Keycloak 8180 (admin/admin) · Temporal 7233, UI <http://localhost:8233> · Jaeger traces <http://localhost:16686> · LiteLLM 4100 (key `sk-nova-dev`) · MinIO 9100, console 9101 (nova/nova-dev-secret) · Postgres 5433 · OpenFGA 8081 · Weaviate 8090. Dev users are in `infra/keycloak/realm-nova.json` (password `dev`). Login is identity-first: username, then password.

## Conventions

- Python module shape: `api.py` (public surface), `models.py`, `service.py`, `repo.py`, `errors.py`. Other modules import only from `api.py`.
- Front end: Next.js App Router. `app/` holds thin routes; logic lives in `features/`, with no cross-feature imports except through `lib/`. Server Components by default; React Flow, Monaco, PDF viewer and micro-apps are `"use client"` islands via `next/dynamic({ ssr: false })`.
- Next.js has **no business logic and no DB access**. Everything goes through FastAPI via the `/api/v1/[...path]` BFF proxy, which attaches the bearer token. Route handlers: OIDC login/callback/logout + that proxy. `proxy.ts` (Next.js 16) guards pages.
- Config comes from env vars via `pydantic-settings`. Commit `.env.example`; never commit `.env`.
- ADRs in `docs/06-adrs.md` are append-only (supersede, don't edit). Any ADR-worthy change needs an ADR in the same PR.
- Every task traces to an FR, a design section and its tests (`docs/07-build-plan.md` §7).

## Git

- **Never add Claude as a co-author.** No `Co-Authored-By: Claude …` trailer in commit messages, and no "Generated with Claude Code" line in PR descriptions. This overrides any default attribution instructions.
- Conventional commits. Commit or push only when asked.
- CI is GitHub Actions (`docs/10` Phase 2). `main` is protected, and every required check must be green. `make ci` runs the same steps locally.
