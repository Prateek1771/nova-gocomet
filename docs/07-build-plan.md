# 07 — Implementation Plan

**Status:** implementation baseline, design phase complete.
**Purpose:** the delivery plan for the prototype. Each milestone has a goal, tasks, tests and exit criteria, all traceable to the [PRD](01-prd.md), the [LLD](03-lld.md), the [workflow specs](04-workflow-specs.md) and the [domain model](09-standard-domain-model.md). The enterprise gates from [08](08-enterprise-execution-plan.md) are folded into each milestone's exit criteria, so "done" is defined in one place. The full capability checklist by area, including the CI pipeline, is [10-implementation-checklist](10-implementation-checklist.md).

---

## 1. Implementation objectives

1. A generic, versioned YAML workflow engine on Temporal, with no process-specific code.
2. A visual studio where graph and YAML stay in sync, with publish/versioning and a live run view.
3. Governed agents (5-stage pipeline) with evidence, confidence and per-run cost.
4. Human tasks with micro-apps, SLA, escalation and amount-based approval authority.
5. Tenant isolation enforced by every store, with two tenants diverging only in config.
6. A data layer: Kafka, Debezium CDC, ClickHouse, dbt metrics, event-triggered workflows.
7. Three workflows end-to-end: W1 BoL, W2 invoice match, W3 exceptions.
8. Near-zero LLM spend in development (`LLM_MODE`), best quality in the demo.
9. A 7-minute demo that runs clean, twice in a row, from `make demo`.

---

## 2. Engineering principles

1. **The engine is generic.** `services/engine` never names a process, doc type or tenant ([09 §2](09-standard-domain-model.md#2-core-design-decision)).
2. **Configuration over code.** Thresholds, approval chains, checks and schemas live in `definitions/` and TenantConfig.
3. **Deterministic before probabilistic.** Code checks run first, and only the residue goes to an LLM.
4. **Agents recommend and never approve.** State changes happen only in the engine (CEL, human tasks).
5. **The backend is authoritative.** Validation, authz (OpenFGA) and transitions happen server-side, and the UI is never trusted.
6. **Every decision carries evidence**: page + bbox, PO line, SQL + rows, SOP chunk, clause ID.
7. **Tenant isolation lives in the store**, not in `WHERE` clauses (RLS, row policies, Weaviate tenants).
8. **Side effects are idempotent** (`action_executions.idempotency_key`).
9. **Cost is visible per run.** Budgets are hard stops that degrade to a human task.
10. **YAML is canonical.** The graph is a view.
11. **Modular monorepo, not microservices.** Boundaries are enforced by import-linter.

---

## 3. Phase 0 — Design baseline

**Goal:** remove ambiguity before code.

### Tasks
- [x] Stack and topology frozen ([02-hld](02-hld.md), [ADR-001…016](06-adrs.md)).
- [x] DSL v1 shape and node types ([LLD §2](03-lld.md#2-workflow-dsl)).
- [x] API surface + error envelope ([LLD §8](03-lld.md#8-api-fastapi-apiv1)).
- [x] OpenFGA model incl. `within_limit` ([LLD §7](03-lld.md#7-authorization-openfga)).
- [x] Kafka topics and keys ([LLD §6](03-lld.md#6-kafka-topics)).
- [x] Three workflow specs with planted errors and acceptance checks ([04](04-workflow-specs.md)).
- [x] Generic vs configured split ([09](09-standard-domain-model.md)).
- [x] Run + human-task state machines ([09 §7](09-standard-domain-model.md#7-standard-run-lifecycle)).
- [ ] Data-model deltas reflected in migration 0001: `tenant_configs`, `workflow_runs.{config_version,subject_type,subject_id}`, `action_executions`, status CHECKs ([LLD §4](03-lld.md#4-data-model-postgres)).
- [ ] Kafka event payload schemas written (JSON Schema) for `shipment.events` and `nova.outbox.*`.

### Exit criteria
- [x] Docs 00–09 consistent with each other.
- [ ] Every FR in PRD §3 maps to at least one milestone task (§7 traceability).

---

## M0 — Skeleton

**Goal:** a reproducible environment that a newcomer can start from the README.

### Tasks
- [x] uv workspace (`packages/*`, `services/*`, `src/` layout) + pnpm `apps/web` (Next.js App Router, React 19, TS, `output: 'standalone'`, OIDC BFF route handlers, `/api/v1/[...path]` proxy, `proxy.ts` guard).
- [x] `infra/docker-compose.yml` `core` profile, M0 slice: postgres, redis, keycloak, migrate (Alembic + seed), api, web. Each remaining service joins when its first user lands: temporal + ui and engine-worker in M1, minio in M2, agents-worker and litellm/ollama (`ai`) in M2, openfga in M4. Host ports: web 3300, api 8100, keycloak 8180, postgres 5433.
- [x] `infra/postgres/init/*.sql`: DBs nova + keycloak, `nova_owner` / `nova_app` (non-owner) roles. The temporal/openfga/litellm/langfuse DBs are added with their services.
- [x] Alembic baseline migration (incl. Phase 0 deltas), RLS + `FORCE` on every tenant table; master-data tables follow in M2/M5.
- [x] `nova_core`: settings (`pydantic-settings`), async SQLAlchemy, tenancy context (`SET app.tenant_id`), structured JSON logging with `tenant_id`/`run_id`/`trace_id`, OTel bootstrap.
- [x] FastAPI app: `/healthz`, `/readyz`, request ID, error envelope, OpenAPI at `/api/openapi.json`.
- [ ] OpenAPI → generated TS client (M1, when there are routes to call from client islands).
- [x] Keycloak in `core` with `infra/keycloak/realm-nova.json` (realm `nova`, clients, 9 roles, orgs Acme + Bolt, dev users) (FR-X.4).
- [x] Next.js BFF: `openid-client` login/callback/logout, Redis session, bearer-attaching `/api/v1` proxy.
- [x] FastAPI JWKS validation → principal (`sub`, `tenant_id`, roles); `GET /me`.
- [x] Makefile: `up`, `down`, `seed`, `test`, `test-int`, `lint`, `ci`.
- [x] `.env.example`, `.wslconfig` note in README.
- [x] Pre-commit: ruff, mypy, eslint + tsc, gitleaks (prettier skipped; eslint covers the web app).
- [x] CI: lint, type check, unit tests, import-linter contracts, integration (RLS + Keycloak), Trivy, Syft SBOM, docs lint.

### Tests
- [x] RLS smoke: a query as tenant A over tenant B rows returns 0 (`tests/integration/test_rls.py`).
- [x] Token validation: unit (`tests/unit/test_auth.py`) + real Keycloak realm (`tests/integration/test_keycloak.py`).
- [ ] Health endpoints in CI via compose.

### Exit criteria
- [x] `make up` → all core containers healthy; web shows login.
- [ ] CI stages live; import-linter contracts enforced; SBOM produced.

---

## M1 — DSL + engine

**Goal:** the definition system and the generic interpreter, before any workflow-specific screen ([09 §13](09-standard-domain-model.md#13-implementation-principle)).

### Tasks
- [ ] `nova_dsl`: Pydantic models for every node type → JSON Schema export → TS types.
- [ ] Graph validator: reachability, no dangling edges, all branches terminate, unique ids (FR-1.6).
- [ ] CEL compile at publish; compiled AST cached per version.
- [ ] Registries: node handlers, actions, checks, agents, micro-app components (decorator-based).
- [ ] DocType loader (`definitions/doc_types/*.yaml`) + schema registry.
- [ ] TenantConfig publish → new version; `load_definition` returns `(definition, config)` snapshot.
- [ ] `NovaWorkflow`: `rule`, `human_task` (signal + SLA timer + escalate), `action`, `parallel`, `wait`, `subflow`, `end`; `continue_as_new` over 1,000 events.
- [ ] `_project` → `run_steps` + `workflow_runs.status` per [09 §7](09-standard-domain-model.md#7-standard-run-lifecycle).
- [ ] `action_executions` idempotency wrapper for every action.
- [ ] API: workflows CRUD, validate, publish, versions; runs start/get/cancel; tasks claim/complete → signal.
- [ ] **Spike:** `data` profile (Kafka + Debezium + ClickHouse) up on WSL2.

### Tests
- [ ] Property tests for the graph validator; golden YAML fixtures (valid + each invalid class).
- [ ] Temporal time-skipping tests: branching, SLA escalation, signal resume, `continue_as_new`, cancel.
- [ ] Idempotency: replaying an action activity produces one `action_executions` row.
- [ ] Config pinning: editing TenantConfig mid-run doesn't change that run's routing.

### Exit criteria
- [ ] A YAML with a human task runs, waits, resumes on signal and escalates on SLA in tests.
- [ ] 100% branch coverage across node types; DSL schema ↔ TS types sync check in CI.
- [ ] Data-profile spike result recorded (works / workaround).

---

## M2 — W1 BoL slice

**Goal:** the first full vertical slice: upload → extract → validate → review → push.

### Tasks
- [ ] Synthetic BoL generator (reportlab): 10 BoLs with the planted errors in [04 W1](04-workflow-specs.md#w1--bill-of-lading-intake), plus a manifest.
- [ ] `POST /documents`: MinIO `tenant/{id}/`, sha256 dedupe, MIME + magic-byte + size/page limits, auto-trigger from `DocType.default_workflow`.
- [ ] `governed_agent` template (5 stages) + `run_agent` activity with Langfuse callback (FR-2.1).
- [ ] `doc_extractor`: pdfplumber text layer → `nova-extract-text`; vision only for pages with no text; value→bbox matching (ADR-016).
- [ ] `bol_validator`: check registry codes + `SEMANTIC_GOODS` via `nova-reason`.
- [ ] `decide` node → `nova-decide` (Jev) with fallback, then human task (FR-2.5).
- [ ] LiteLLM configs `local` / `cheap` / `demo`; Redis cache; global `max_budget`.
- [ ] Inbox + `bol_review` micro-app: DocumentViewer (bbox overlay) + FieldForm + DecisionBar.
- [ ] `tms.upsert_shipment` mock action.
- [ ] Jev eval set (~40 labelled yes/no questions from the seed).
- [ ] **Spike:** React Flow ↔ YAML `parseDocument()` round-trip.

### Tests
- [ ] Unit: each check code against its planted error and against a clean doc.
- [ ] VCR-recorded LLM tests for extractor/validator; bbox match test set (dates/numbers reformatted).
- [ ] Langfuse eval baseline: per-field F1, validator recall.
- [ ] Injection red-team v1: 10 adversarial BoLs → zero unauthorised state changes.
- [ ] PII redaction verified in logs and traces.

### Exit criteria
- [ ] Upload → extract → review → approve → mock TMS works in the UI.
- [ ] 4 clean BoLs complete touchless; all 6 planted issues produce a human task with the right evidence highlighted.
- [ ] Eval baseline recorded; `make bench-llm` timings recorded for `cheap`.

---

## M3 — Studio

**Goal:** model a process visually, publish it, watch it run.

### Tasks
- [ ] React Flow canvas + elkjs layout as a `next/dynamic` client island; Monaco + `monaco-yaml` with the DSL schema.
- [ ] Graph edits = targeted `yaml` Document mutations keyed by node id; `layout:` block for positions.
- [ ] Node palettes from `/catalog/agents`, `/catalog/actions`; side-panel node config.
- [ ] Validation badges from `/workflows/{key}/validate`; publish → new version (FR-1.3).
- [ ] Live run view: same graph, per-node status via SSE, step inspector, Langfuse trace links (FR-1.7).

### Tests
- [ ] SSE through the Next.js `/api/v1` proxy delivers events < 1 s.
- [ ] YAML round-trip property test: random graph ops keep comments and leave unrelated lines byte-identical.
- [ ] Invalid YAML keeps the last valid graph and shows markers.
- [ ] axe a11y check on studio and inbox.

### Exit criteria
- [ ] Edit W1 visually, publish v2; an in-flight run stays on v1.

---

## M4 — Governance

**Goal:** tenancy, authority, budgets and tracing, all provable.

### Tasks
- [ ] OpenFGA model with the RBAC capability matrix ([LLD §7](03-lld.md#7-authorization-openfga)); `require(capability, object)` on every route with contextual role tuples (FR-X.5).
- [ ] Engine writes assignee/approver tuples; approver limits from `TenantConfig.approval_limits`.
- [ ] Keycloak MFA policy for privileged roles (staging realm); login/admin events on.
- [ ] Inbox via `ListObjects`; `can_complete` checked on `/tasks/{id}/complete` (FR-1.5).
- [ ] Second tenant (Bolt) with diverging YAML + TenantConfig (FR-X.2).
- [ ] LiteLLM virtual key per tenant; budget-exhausted → `needs_attention` human task.
- [ ] `ai` profile: Langfuse (shared ClickHouse/Redis/MinIO), OTel collector; cost per run in the UI (FR-2.4).
- [ ] Audit log hash chain; `definition_version` + `config_version` on every entry (FR-X.1).

### Tests
- [ ] Cross-tenant suite: Postgres, MinIO, Weaviate, FGA (ClickHouse is added in M6).
- [ ] RBAC matrix: every role × capability against the allow/deny table in LLD §7, incl. `platform_admin` has no tenant data access.
- [ ] Token tests: wrong `aud`/`iss`, expired, missing org, tampered signature → 401.
- [ ] Audit chain verification job.

### Exit criteria
- [ ] A Bolt user can't see Acme runs; L1 can't approve above their limit through the API.
- [ ] Cost per run visible; budget stop degrades to a human task.

---

## M5 — W2 Invoice

**Goal:** match & approve (pattern B) for both tenants.

### Tasks
- [ ] `invoice_v1` schema + DocType; 8 invoices with planted errors; POs, rate contracts, clauses seeded.
- [ ] `invoice_matcher`: deterministic line mapping + FX → clause search in Weaviate (`nova-embed`) → LLM confirms rate and cites the clause ID.
- [ ] Accessorial `decide` using dwell facts.
- [ ] Approval matrix in TenantConfig; Bolt L3 via `subflow`.
- [ ] ComparisonTable micro-app; `erp.post_payable` and `carrier.dispute_email` actions.

### Tests
- [ ] Each planted invoice routes to the expected branch for Acme **and** Bolt.
- [ ] Approval-limit matrix: every role × amount band.
- [ ] Duplicate invoice → auto-reject with no LLM call after extraction.

### Exit criteria
- [ ] All 8 cases route correctly for both tenants; disputes carry the cited clause in the email body.

---

## M6 — Data layer + W3

**Goal:** event-triggered exceptions (pattern C) on the real data stack.

### Tasks
- [ ] `data` profile: Kafka KRaft, Kafka Connect + Debezium (outbox + `exceptions`, `run_steps`), ClickHouse, dbt one-shot.
- [ ] ClickHouse tables, MVs, row policies ([LLD §5](03-lld.md#5-clickhouse)).
- [ ] Simulator: 50 shipments, 6 lanes, 6 scripted incidents, 1 sim-day = 1 real minute.
- [ ] dbt metrics: `eta_slip_hours`, `dwell_time_hours`, `invoice_variance_pct`, `touchless_rate`, `cost_per_run`.
- [ ] Temporal schedule → `detect_exceptions`; CDC trigger router → `exception_triage`.
- [ ] `exception_analyst` (`query_metric`, read-only, tenant setting); `action_recommender` (SOP search).
- [ ] SOP corpus (~15 docs) indexed per Weaviate tenant; ExceptionPanel + analytics page.

### Tests
- [ ] Idempotent consumer replay: re-delivering offsets creates no duplicate runs or exceptions.
- [ ] ClickHouse cross-tenant row-policy test (completes the M4 suite).
- [ ] Event payloads validated against the Phase 0 schemas.

### Exit criteria
- [ ] 6 scripted incidents → exceptions within one cycle → triaged → notified, with no duplicates and SOP cited.
- [ ] Kafka lag visible; lag alert configured.

---

## M7 — Demo polish

**Goal:** the [PRD §6](01-prd.md#6-demo-script-7-minutes) script, reliable and repeatable.

### Tasks
- [ ] `make demo`: deterministic seed reset for both tenants.
- [ ] Playwright demo script: 3 workflows × 2 tenants.
- [ ] Kill-worker resilience beat; Langfuse cost beat.
- [ ] UI polish pass (dense, keyboard-first, `cmdk`).
- [ ] README quickstart; archify diagrams refreshed from code.
- [ ] Runbooks: LLM provider outage, stuck runs, budget exhausted.
- [ ] SLO dashboard (08 §8.1) live in staging.
- [ ] Backup recorded `demo`-mode run.

### Tests
- [ ] E2E suite green twice in a row from a clean `make up-full`.
- [ ] One Postgres restore drill, passing E2E afterwards.

### Exit criteria
- [ ] The 7-minute script runs clean twice in a row in `demo` mode, and once in `cheap` mode.

---

## 4. Definition of Done (per task)

- [ ] Traced to a PRD FR / LLD section / workflow acceptance check (§7).
- [ ] Server-side validation and authz in place; tenant context set.
- [ ] Migration included if the schema changed (expand → migrate → contract).
- [ ] Audit entry for every state change or decision.
- [ ] Errors use the envelope; failures degrade to `needs_attention`, never silently.
- [ ] Logs/traces carry `tenant_id`, `run_id`, `trace_id`; no PII or secrets.
- [ ] Unit tests; integration/workflow tests where it crosses a store or Temporal.
- [ ] No process-specific code in `services/engine` (import-linter + review).
- [ ] UI: keyboard reachable, axe clean.
- [ ] Docs/ADR updated in the same PR. CI green.

Staging/prod release criteria (SLOs, DR, signed images) are in [08 §6–8](08-enterprise-execution-plan.md#6-quality-gates--cicd).

---

## 5. Delivery order

```text
Phase 0 Design ✅
   ↓
M0 Skeleton
   ↓
M1 DSL + engine ──── spike: data profile on WSL2
   ↓
M2 W1 BoL ────────── spike: YAML round-trip · Jev eval set
   ↓
M3 Studio
   ↓
M4 Governance
   ↓
M5 W2 Invoice
   ↓
M6 Data layer + W3
   ↓
M7 Demo polish
```

Parallel once foundations exist:

```text
After M1:  web shell + inbox ── alongside agents work in M2
After M2:  synthetic data generators (W2, W3) ── alongside M3
Always:    eval sets · tests · docs/ADRs
```

---

## 6. Final demo gate

The prototype is **not** demo-ready just because runs complete. It also needs:

```text
3 workflows × 2 tenants green
  + zero process-specific engine code
  + cross-tenant suite green
  + approval authority enforced by API
  + every planted error caught
  + evidence + trace on every decision
  + cost per run visible
  + kill-worker resume with no duplicate side effects
  + clean run twice from make demo
  = Demo ready
```

### Acceptance targets (from [PRD §4, §7](01-prd.md#4-non-functional-requirements-prototype-targets))

| Target | Value |
|---|---|
| BoL extraction p50 | < 15 s (`demo`), < 2 min (`cheap`/`local`) |
| `decide` p50 | < 2 s |
| Run view update via SSE | < 1 s |
| Cost per BoL | < $0.05, visible per run |
| Validator recall on seed | 100% |
| Cross-tenant reads | 0 |
| Footprint | full stack ≤ 11 GB; + local LLMs ≤ 20 GB |
| Durability | worker kill → resume, no duplicate actions |

---

## 7. Traceability rule

Every task maps to a requirement, a design section and its tests:

```text
TASK-M2-04
Requirement: FR-2.3 (structured output + evidence)
Design:      LLD §3.1, 09 §4
Implement:   services/agents/nova_agents/pipeline/deliver.py
Tests:       test_deliver_rejects_missing_evidence, eval:bol_field_f1
Status:      Not started | In progress | Review | Done
```

### FR → milestone map

| FR | Milestone |
|---|---|
| FR-1.1 YAML ↔ graph | M3 |
| FR-1.2 node types | M1 (+ `agent`, `decide` in M2) |
| FR-1.3 versioning | M1 (API), M3 (UI) |
| FR-1.4 triggers | M1 manual · M2 upload · M6 event + schedule |
| FR-1.5 assignment / SLA / escalation | M1 (engine), M4 (FGA) |
| FR-1.6 publish validation | M1 |
| FR-1.7 live run view | M3 |
| FR-2.1 5-stage pipeline | M2 |
| FR-2.2 agents | M2 extractor, validator · M5 matcher · M6 analyst, recommender |
| FR-2.3 structured output + evidence | M2 |
| FR-2.4 LiteLLM + Langfuse + budget | M2 (gateway), M4 (budgets, tracing) |
| FR-2.5 `decide` + fallback | M2 |
| FR-3.1–3.3 micro-apps | M2 (bol_review), M5 (invoice_review), M6 (exception_panel) |
| FR-3.4 drag-and-drop builder | Stretch |
| FR-4.1 ingestion | M2 (uploads), M6 (Kafka, CDC) |
| FR-4.2 ClickHouse + dbt | M6 |
| FR-4.3 tenant isolation | M0 (RLS), M4, M6 |
| FR-X.1 audit | M1 (entries), M4 (hash chain) |
| FR-X.2 two tenants | M4 |
| FR-X.3 one-command bring-up | M0, M7 |
| FR-X.4 Keycloak login + MFA | M0 (login), M4 (MFA policy) |
| FR-X.5 RBAC via OpenFGA | M4 |

---

## 8. Stretch (after M7)

PageIndex for long contracts, a DPT-2 adapter benchmark, a drag-and-drop micro-app builder, a layout-template extractor (proof of the scaling tier-3 cost lever), and Air Waybill as a config-only pattern-A proof ([09 §13](09-standard-domain-model.md#13-implementation-principle)).

## 9. Risks to watch during build

- **Jev response format.** Build the eval set in M2 before relying on it in W2/W3.
- **Debezium + ClickHouse on Windows/WSL.** Spike in M1 even though it's used in M6.
- **React Flow ↔ YAML sync.** Spike in M2 so M3 isn't blocked.
- **Value→bbox matching** for reformatted dates and numbers. Build the test set early in M2.
