# 10 — Implementation Checklist

**Project:** Nova prototype, GoComet's governed AI platform for logistics
**Stack:** Next.js (App Router) + FastAPI + Temporal + LangGraph + LiteLLM + Postgres + Kafka/Debezium + ClickHouse/dbt + Weaviate + OpenFGA + Langfuse/OTel + MinIO + Ollama, on Docker Compose
**Status:** implementation baseline
**How to use:** this is the full capability checklist, organised by area the way the UniDocs plan is. Every phase is tagged with the milestone that delivers it. [07-build-plan](07-build-plan.md) gives the *order* (M0–M7) and the milestone exit gates. This doc gives *everything that must exist* to call each area done. Tick items here as you go. A milestone closes when its tagged items are ticked and its 07 exit criteria pass.

---

## 1. Objectives

Deliver the full prototype:

1. Repository, dev environment and **CI** from day one
2. Identity, tenancy and relationship-based authz
3. A configuration-driven definition system (DSL, doc types, schemas, checks, TenantConfig)
4. A generic Temporal workflow engine
5. Document intake, storage and dedupe
6. A governed agent platform behind the LiteLLM gateway
7. Human tasks, SLA/escalation and micro-apps
8. The Next.js web app: studio, runs, inbox, documents, exceptions, admin
9. The data layer: Kafka, Debezium CDC, ClickHouse, dbt metrics
10. Three workflows: W1 BoL, W2 invoice, W3 exceptions
11. Audit, privacy and data lifecycle
12. Security engineering and AI governance
13. Testing, observability, backup/restore and demo readiness

The architecture stays a **modular monorepo** ([ADR-001](06-adrs.md#adr-001-modular-monorepo-grouped-by-runtime-profile)), not microservices.

---

## 2. Architecture baseline

```text
                 ┌──────────────────────────────┐
                 │  nova-web · Next.js          │
                 │  Studio · Runs · Inbox ·     │
                 │  Documents · Exceptions ·    │
                 │  Admin                       │
                 └──────────────┬───────────────┘
                                │ BFF proxy (Bearer) · SSE   ◄── OIDC ──► Keycloak
                                ▼
                 ┌──────────────────────────────┐
                 │  nova-api · FastAPI          │
                 │  auth · definitions · runs · │
                 │  tasks · documents · catalog │
                 └───┬──────────┬──────────┬────┘
                     │          │          │
        start/signal │   check  │          │ SQL (RLS)
                     ▼          ▼          ▼
              ┌──────────┐ ┌─────────┐ ┌──────────┐   ┌────────┐
              │ Temporal │ │ OpenFGA │ │ Postgres │──►│Debezium│
              └────┬─────┘ └─────────┘ └──────────┘   └───┬────┘
                   │ task queues                          ▼
        ┌──────────┴──────────┐                      ┌────────┐   ┌────────────┐
        ▼                     ▼                      │ Kafka  │──►│ ClickHouse │◄─ dbt
 ┌──────────────┐   ┌──────────────────┐             └───┬────┘   └────────────┘
 │engine-worker │   │  agents-worker   │                 │
 │NovaWorkflow  │   │  LangGraph × 5   │──► Weaviate     ▼
 │CEL · decide  │   │  stages          │──► MinIO    ingest (simulator,
 └──────┬───────┘   └────────┬─────────┘             trigger router)
        └────────► LiteLLM ◄─┘──► OpenRouter (Claude · Jev) / Ollama
                      └──► Langfuse
```

Details: [02-hld](02-hld.md) · [03-lld](03-lld.md) · [09-standard-domain-model](09-standard-domain-model.md).

---

## 3. Engineering principles

1. **The engine is generic.** No process, doc type or tenant names in `services/engine`.
2. **Configuration over code.** Thresholds and approval chains live in versioned TenantConfig + OpenFGA.
3. **The backend is authoritative.** FastAPI validates and authorises. Next.js holds no business logic.
4. **Deterministic before probabilistic.** Code checks run first, then the LLM gets the residue.
5. **Agents recommend and never approve.**
6. **Every decision carries evidence and a trace.**
7. **Tenant isolation is enforced by the store.**
8. **Every side effect is idempotent.**
9. **Every state transition is audited.**
10. **Uploaded documents are untrusted**, both as files and as prompts.
11. **CI is a gate, not a report.** Main is always green and deployable to the demo.

---

# Phase 0 — Design baseline · *done*

See [07 §3](07-build-plan.md#3-phase-0--design-baseline). Remaining items:

- [ ] Data-model deltas in migration 0001 ([09 §3](09-standard-domain-model.md#3-standard-domain-model)).
- [ ] JSON Schemas for `shipment.events` and `nova.outbox.*` payloads.

---

# Phase 1 — Repository & dev foundation · *M0*

**Goal:** a newcomer gets the stack running from the README in under 30 minutes.

### Backend
- [ ] uv workspace: `packages/nova_core`, `packages/nova_dsl`, `services/{api,engine,agents,ingest}`, `src/` layout.
- [ ] `nova_core`: `pydantic-settings` config, async SQLAlchemy 2, tenancy context (`SET app.tenant_id`), Alembic.
- [ ] Module shape: `api.py` · `models.py` · `service.py` · `repo.py` · `errors.py`.
- [ ] FastAPI: error envelope, request/correlation ID, `/healthz`, `/readyz`, OpenAPI.
- [ ] Structured JSON logging with `tenant_id`, `run_id`, `trace_id`; redaction filter.

### Frontend
- [ ] pnpm `apps/web`: Next.js App Router, React 19, TS strict, `output: 'standalone'`.
- [ ] Tailwind v4 + shadcn/ui + lucide; app shell, navigation, `cmdk` palette.
- [ ] `/api/v1/[...path]` BFF proxy to `nova-api`; OpenAPI-generated client (server + browser).
- [ ] Shared components: table, status badge, drawer, upload, empty/error states.

### Infra & tooling
- [ ] `infra/docker-compose.yml` profiles `core` / `data` / `ai`; healthchecks on every service.
- [ ] Postgres init: DBs for nova, temporal, openfga, litellm, langfuse; `nova_app` non-owner role.
- [ ] Ollama container mounting the host model store.
- [ ] Makefile: `up`, `up-full`, `up-local`, `down`, `seed`, `demo`, `test`, `lint`, `fmt`.
- [ ] `.env.example`; `.wslconfig` guidance; README quickstart.
- [ ] Pre-commit: ruff, mypy, eslint, prettier, gitleaks, conventional-commit check.

### Exit criteria
- [ ] `make up` → all core containers healthy; web shows login.
- [ ] Migrations run cleanly on an empty DB.
- [ ] CI Phase 2 "M0 scope" is green on main.

---

# Phase 2 — Continuous Integration · *M0, extended every milestone*

**Goal:** every PR is proven correct, secure and in-contract before merge, and main stays demo-ready.

Platform: **GitHub Actions**. CI runs on `ubuntu-latest`; dev happens on Windows/WSL2, so CI is the reference environment.

### 2.1 Workflows

| File | Trigger | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | `pull_request`, `push: main` | Lint, types, unit, contracts, integration, security, build |
| `.github/workflows/ai-eval.yml` | PR touching `services/agents/**`, `prompts/**`, `infra/litellm/**`, `definitions/schemas/**`; nightly | Live eval in `cheap` mode against the seed set |
| `.github/workflows/e2e.yml` | `push: main`, nightly, manual | Compose up `core+data+ai` → Playwright demo script |
| `.github/workflows/release.yml` | tag `v*` | Build, SBOM, sign, push images, changelog |
| `.github/workflows/docs.yml` | PR touching `docs/**` or `*.md` | Markdown lint + internal link/anchor check |

### 2.2 `ci.yml` jobs

```text
changes (paths-filter)
   ├── python ───────── uv sync --frozen · ruff check · ruff format --check · mypy --strict
   │                    · import-linter · pytest tests/unit --cov
   ├── web ──────────── pnpm install --frozen-lockfile · eslint (+ boundaries) · tsc --noEmit
   │                    · vitest · next build
   ├── definitions ──── nova validate definitions/**  (DSL schema, graph, CEL compile,
   │                    references, YAML round-trip fixtures)
   ├── contracts ────── DSL JSON Schema → TS types: git diff --exit-code
   │                    · OpenAPI diff vs main (breaking = fail) · event JSON Schemas
   ├── workflow-tests ─ Temporal time-skipping tests (engine, every node type)
   ├── integration ──── testcontainers: Postgres (RLS cross-tenant), Keycloak (realm import, token flows),
   │                    OpenFGA (RBAC matrix),
   │                    MinIO, Weaviate; Kafka + ClickHouse from M6
   ├── ai-recorded ──── VCR-recorded agent tests + injection red-team (no network, no keys)
   ├── security ─────── gitleaks · dependency-review · Trivy fs + config · pip-audit · pnpm audit
   └── images ───────── docker buildx (api, engine, agents, ingest, web) with GHA cache
                        · Trivy image scan (fail on HIGH/CRITICAL with a fix) · Syft SBOM
```

### 2.3 Tasks
- [ ] `ci.yml` with the jobs above. Jobs not yet relevant are skipped by `changes`, not deleted.
- [ ] `concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }`.
- [ ] Caches: uv (`astral-sh/setup-uv` cache), pnpm store, Next.js `.next/cache`, Docker layers (`type=gha`).
- [ ] Pinned action versions by SHA; Renovate keeps them current.
- [ ] Least-privilege `permissions:` per workflow (`contents: read` by default).
- [ ] Secrets: `OPENROUTER_API_KEY` only in `ai-eval.yml`, in a protected `eval` environment; never available to fork PRs.
- [ ] LiteLLM in CI uses a dedicated virtual key with `max_budget: 2` USD/month.
- [ ] Test reports (JUnit) + coverage uploaded as artifacts; coverage summary in the PR.
- [ ] Playwright traces/videos uploaded on failure.
- [ ] Branch protection on `main`: required checks = `python`, `web`, `definitions`, `contracts`, `workflow-tests`, `integration`, `ai-recorded`, `security`, `images`; linear history; squash merge.
- [ ] `CODEOWNERS` (engine, agents, infra, definitions) and a PR template (problem, change, evidence, rollback).
- [ ] Renovate weekly: grouped minor/patch; majors separately.
- [ ] `make ci` runs the same steps locally (same commands, no CI-only logic).

### 2.4 Growth by milestone

| M | CI adds |
|---|---|
| M0 | python, web, security, images, docs, branch protection |
| M1 | definitions, contracts (DSL ↔ TS), workflow-tests, integration: Postgres RLS |
| M2 | ai-recorded, ai-eval live, injection red-team v1, MinIO + upload checks |
| M3 | YAML round-trip property test, axe a11y in Playwright, OpenAPI diff |
| M4 | integration: OpenFGA matrix, Weaviate tenants, audit hash-chain test |
| M5 | approval-limit matrix (role × amount band) |
| M6 | integration: Kafka + ClickHouse (row policies, replay idempotency), event contracts |
| M7 | e2e.yml required on main; release.yml |

### 2.5 Targets
- [ ] PR pipeline p50 < 12 min, p95 < 20 min.
- [ ] Flaky test rate < 1%. A flaky test is quarantined with an issue, never just retried.
- [ ] Coverage ≥ 80% `nova_dsl`/`nova_core`, ≥ 70% services; 100% branch coverage of node handlers.
- [ ] Zero `# type: ignore` / `@ts-expect-error` without a linked issue.

### Exit criteria
- [ ] No PR can merge with a red required check.
- [ ] A forked PR runs without secrets and still gets a full non-LLM signal.
- [ ] `main` has been green for every merge of the milestone.

---

# Phase 3 — Identity, tenancy & authorization · *M0 / M4*

**Goal:** build the security boundary before business workflows.

### Authentication: Keycloak (M0; MFA policy M4)
- [ ] Keycloak 26.x in `core` on its own Postgres DB; healthcheck.
- [ ] `infra/keycloak/realm-nova.json`: realm `nova`, clients `nova-web` / `nova-api` / `nova-admin`, 9 client roles, Organizations Acme + Bolt with the org claim mapper, dev users (one per role per tenant).
- [ ] Token policy: access 5 min, refresh rotation, SSO idle 30 min; brute-force detection; password policy.
- [ ] MFA (OTP/WebAuthn) required for `platform_admin`, `tenant_admin`, `finance`, `controller` in the staging realm.
- [ ] Login + admin events enabled.
- [ ] Next.js BFF: `openid-client` PKCE login, callback (state + nonce), RP-initiated logout; Redis session; httpOnly `SameSite=Lax` cookie; `proxy.ts` guard.
- [ ] `/api/v1/[...path]` proxy: refresh when < 60 s remain, attach bearer, stream SSE, Origin check on mutations.
- [ ] FastAPI: JWKS validation (`iss`, `aud`, `exp`, `azp`), principal, org → `tenant_id`, `users` mirror upsert, `GET /me`.

### Tenancy
- [ ] `tenant_id` on every table, key, topic message and object prefix.
- [ ] Postgres RLS with `nova_app` as a non-owner; tenant set per transaction.
- [ ] MinIO `tenant/{id}/` prefixes; Weaviate tenant per Nova tenant; ClickHouse row policies (M6).
- [ ] LiteLLM virtual key per tenant (M4).

### Authorization: RBAC + ReBAC in OpenFGA (M4)
- [ ] Model from [LLD §7](03-lld.md#7-authorization-openfga): roles on `tenant`, capability relations, `workflow` / `run` / `task`, `within_limit`.
- [ ] Roles from the token sent as contextual tuples on every Check/ListObjects; no role tuples stored.
- [ ] `require(capability, object)` FastAPI dependency on every route (capability table in LLD §8).
- [ ] Engine writes ownership, assignee and approver tuples; approver `limit` from `TenantConfig.approval_limits` of the pinned config version.
- [ ] Inbox via `ListObjects`; task completion via `can_complete` with `{amount}`.
- [ ] UI hides nav/actions by role (cosmetic only).

### Tests
- [ ] Token: expired, wrong `aud`/`iss`/`azp`, bad signature, missing org → 401; refresh and logout flows; no token visible to browser JS (E2E check).
- [ ] RBAC matrix: every role × capability against the allow/deny table; `platform_admin` gets 403 on tenant data.
- [ ] Role removed in Keycloak → access denied after the next refresh (≤ 5 min).
- [ ] Cross-tenant suite on every store.
- [ ] IDOR tests (ID swapping across tenants).
- [ ] Approval-limit bypass via direct API call → 403.

### Exit criteria
- [ ] No cross-tenant read possible even with a missing `WHERE`.
- [ ] No role can act outside its relations or limits.

---

# Phase 4 — Core domain & database · *M0 / M1*

**Goal:** the authoritative data model from [LLD §4](03-lld.md#4-data-model-postgres) and [09](09-standard-domain-model.md).

### Entities
- [ ] tenants, tenant_configs (versioned), users
- [ ] workflow_definitions, workflow_runs (`config_version`, `subject_*`), run_steps
- [ ] human_tasks, documents, extractions
- [ ] action_executions, audit_log, outbox
- [ ] Master data: carriers, ports, bookings, purchase_orders, po_lines, rate_contracts, contract_clauses, shipments, invoices, exceptions, micro_apps

### Database engineering
- [ ] UUID PKs; `timestamptz` everywhere (UTC).
- [ ] FKs, uniques (`(tenant_id,key,version)`, `(tenant_id,sha256)`, one open exception per shipment+type).
- [ ] CHECK constraints on every status enum ([09 §7](09-standard-domain-model.md#7-standard-run-lifecycle)).
- [ ] Indexes led by `tenant_id` for the inbox, run list and document list queries.
- [ ] Outbox written in the same transaction as the state change.
- [ ] Migrations follow expand → migrate → contract.

### Tests
- [ ] Migration up/down on an empty DB in CI.
- [ ] Constraint tests for the invariants above.

### Exit criteria
- [ ] Schema matches LLD §4; invariants are enforced by the DB, not just by code.

---

# Phase 5 — Definition system · *M1*

**Goal:** a new process is configuration, not code ([09 §13](09-standard-domain-model.md#13-implementation-principle)).

- [ ] `nova_dsl` Pydantic models for all 10 node types → JSON Schema → TS types.
- [ ] YAML loader + graph validator (reachability, dangling edges, termination, unique ids).
- [ ] CEL compile at publish; AST cached per version.
- [ ] Draft → validate → publish → immutable version API.
- [ ] DocType registry (`definitions/doc_types/*.yaml`).
- [ ] Extraction schema registry (`bol_v1`, `invoice_v1`), versioned.
- [ ] Check registry (`@check(code, severity, fields)`) with tenant severity overrides.
- [ ] TenantConfig publish → new version, validated by `tenant_config_v1.json`.
- [ ] Micro-app definition registry + output schema validation.
- [ ] `nova validate` CLI (also used by CI).

### Tests
- [ ] Property tests for the validator; golden valid/invalid YAML fixtures.
- [ ] Each invalid class has a fixture and a clear error message.

### Exit criteria
- [ ] Adding a doc type or tenant variant needs no code change outside `definitions/`.

---

# Phase 6 — Workflow engine · *M1*

**Goal:** one deterministic interpreter for every process.

- [ ] `NovaWorkflow` loop; `load_definition` returns the (definition, config) snapshot.
- [ ] Handlers: `rule`, `human_task`, `action`, `parallel`, `wait`, `subflow`, `end`; `agent` + `decide` in M2.
- [ ] Signals (`task_completed`), queries (`state`), cancel.
- [ ] SLA timers + escalation; `continue_as_new` above 1,000 events.
- [ ] Run/task state machine owned by the engine; `_project` → `run_steps` + status + audit.
- [ ] Idempotent actions via `action_executions`.
- [ ] Per-`LLM_MODE` activity timeouts; retry policies per node.
- [ ] Engine code changes guarded with `workflow.patched()`.

### Tests
- [ ] Time-skipping: every node type, branch, SLA escalation, signal resume, cancel, `continue_as_new`.
- [ ] Config pinning mid-run; worker kill → resume with no duplicate action.

### Exit criteria
- [ ] No invalid transition is reachable; every transition is projected and audited.

---

# Phase 7 — Document intake & storage · *M2*

- [ ] `POST /documents` multipart → MinIO `tenant/{id}/`.
- [ ] Extension + MIME + magic-byte checks, size and page limits.
- [ ] sha256 dedupe per tenant → returns the existing run.
- [ ] Doc-type classification (`gemma3:1b` or rules) → `DocType.default_workflow` trigger.
- [ ] Page image rendering for the bbox overlay (`/documents/{id}/pages/{n}`).
- [ ] Access only via API (no public bucket); safe storage keys.

### Tests
- [ ] Malformed / oversized / wrong-magic uploads rejected; duplicate upload → same run.

---

# Phase 8 — Agent platform & LLM gateway · *M2 → M6*

- [ ] `governed_agent` template: scope → context → route → execute → deliver.
- [ ] `run_agent` activity with Langfuse callback; trace id saved on the step.
- [ ] `Extractor` interface: `LlmTextLayerExtractor` (pdfplumber → text model, bbox recovery); vision only for scans.
- [ ] Agents: `doc_extractor`, `bol_validator` (M2) · `invoice_matcher` (M5) · `exception_analyst`, `action_recommender` (M6).
- [ ] `decide` node → `nova-decide` (Jev) → fallback → human task.
- [ ] LiteLLM configs `local` / `cheap` / `demo`; Redis cache; global `max_budget`; per-tenant keys.
- [ ] `nova-embed` via Ollama; `embed_model` stored per Weaviate collection; `make reindex`.
- [ ] Output schema validation with one repair retry → `needs_attention`.
- [ ] `make bench-llm`.

### Tests
- [ ] VCR-recorded agent tests; bbox matching test set.
- [ ] Jev eval set (~40 questions); fallback path test.

---

# Phase 9 — Human tasks & micro-apps · *M2 / M5 / M6*

- [ ] Task create / claim / complete; FGA check → Temporal signal.
- [ ] SLA due dates, escalation, delegation.
- [ ] JSON micro-app renderer + component registry: DocumentViewer, FieldForm, ComparisonTable, ExceptionPanel, DecisionBar.
- [ ] Apps: `bol_review` (M2), `invoice_review` (M5), `exception_panel` (M6).
- [ ] Mandatory reason for reject/dispute; decision validated against `output_schema`.

### Tests
- [ ] Completing without the relation → 403; invalid decision payload → 422.

---

# Phase 10 — Web app (Next.js) · *M2 / M3 / M7*

### Studio (M3)
- [ ] React Flow canvas (client island) + elkjs; Monaco + `monaco-yaml`.
- [ ] Graph edits as `parseDocument()` mutations; `layout:` block.
- [ ] Palettes from `/catalog`; node side panel; validation badges; publish + version history + YAML diff.

### Runs (M3)
- [ ] Run list (Server Component) with filters; live run graph via SSE; step inspector with evidence and trace link.

### Inbox (M2)
- [ ] My tasks / team tasks; claim; micro-app host; keyboard shortcuts.

### Documents · Exceptions · Admin (M2 / M6 / M4)
- [ ] Document list + viewer; exceptions dashboard (Recharts); admin: tenants, config versions, budgets, users.

### Quality
- [ ] Keyboard navigation, focus states, `cmdk` palette.
- [ ] axe clean on studio, inbox, runs; WCAG 2.1 AA contrast.
- [ ] Desktop-first (≥ 1280 px), usable at 1024 px; mobile is a non-goal.

---

# Phase 11 — Data layer · *M6*

- [ ] Kafka KRaft; topics and keys from [LLD §6](03-lld.md#6-kafka-topics).
- [ ] Kafka Connect + Debezium: outbox router + `exceptions`, `run_steps`.
- [ ] ClickHouse Kafka-engine tables, MVs, `step_facts`, `llm_cost_facts`, row policies.
- [ ] dbt-clickhouse project + MetricFlow metrics (`eta_slip_hours`, `dwell_time_hours`, `invoice_variance_pct`, `touchless_rate`, `cost_per_run`).
- [ ] Simulator (50 shipments, 6 incidents, accelerated time).
- [ ] Temporal schedule → `detect_exceptions`; CDC trigger router.
- [ ] `query_metric` tool: read-only user, tenant setting per query.

### Tests
- [ ] Consumer replay idempotency; event schema validation; ClickHouse cross-tenant test.

---

# Phase 12 — Workflows · *M2 / M5 / M6*

| Workflow | Acceptance ([04](04-workflow-specs.md)) | M |
|---|---|---|
| W1 BoL | 4 clean touchless; 6 planted issues → task with correct evidence | M2 |
| W2 Invoice | 8 cases route correctly for Acme **and** Bolt; disputes cite clause | M5 |
| W3 Exceptions | 6 incidents → exceptions in one cycle, no dupes, SOP cited, notified | M6 |

- [ ] W1 definitions (Acme + Bolt) + seed + acceptance test.
- [ ] W2 definitions (Acme + Bolt incl. L3 `subflow`) + seed + acceptance test.
- [ ] W3 definitions + simulator script + SOP corpus + acceptance test.

---

# Phase 13 — Actions & notifications · *M2 / M5 / M6*

- [ ] Action registry: `tms.upsert_shipment`, `erp.post_payable`, `carrier.dispute_email`, `notify_customer`, `auto_close`.
- [ ] Mock sinks with a UI view (what would have been sent).
- [ ] Notifications from outbox events only (task created, SLA breach, escalation).
- [ ] Notification failure never changes run state.

---

# Phase 14 — Audit, privacy & data lifecycle · *M4 / M7*

### Audit
- [ ] Entries for login, upload, publish (with diff), run start/end, every step, every human decision, every action, config change, budget stop.
- [ ] Actor type (user/agent/system), evidence, `definition_version`, `config_version`, request/trace ID.
- [ ] Append-only + hash chain; verification job.

### Privacy
- [ ] PII-tagged columns drive redaction in logs, traces and prompts.
- [ ] Retention per DocType (`retention:`); purge job (documents, extractions, Weaviate objects, Langfuse traces).
- [ ] Erasure job; audit entries pseudonymised, not deleted.
- [ ] `llm_egress: local_only` tenant option routes to Ollama aliases.

---

# Phase 15 — Security engineering · *continuous, gate at M7*

See the threat model in [08 §4](08-enterprise-execution-plan.md#4-security).

- [ ] OWASP ASVS L2 checks: injection, XSS, CSRF (cookie auth → SameSite + origin check), IDOR, broken access control.
- [ ] OWASP LLM Top 10: prompt injection red-team (~30 docs), excessive agency (agents have no approve/pay tools), output validation.
- [ ] File security: magic bytes, size/page limits, parsing in the agents worker with a read-only FS.
- [ ] Secrets: none in the repo (gitleaks), env-only, rotation documented.
- [ ] Supply chain: lockfiles, Renovate, SBOM, Trivy, digest-pinned base images, cosign-signed images.
- [ ] Containers: non-root, read-only root FS where possible, resource limits.

### Exit criteria
- [ ] No unresolved critical/high finding; the red-team set causes zero unauthorised state changes.

---

# Phase 16 — AI governance & evals · *M2 → M7*

- [ ] Model registry = LiteLLM configs in git, reviewed.
- [ ] Langfuse datasets from planted errors; per-field F1 and validator recall baselines.
- [ ] `ai-eval.yml` blocks regressions below baseline.
- [ ] Every decision-relevant field has evidence, otherwise schema validation fails.
- [ ] Model card README per agent.
- [ ] Cost per run in the UI; budget at 80% → alert; 100% → `needs_attention`.

---

# Phase 17 — Testing strategy · *continuous*

| Layer | Covers | Runs in |
|---|---|---|
| Unit | DSL, CEL, checks, state machines, approval matrix, bbox matching | `ci.yml` python / web |
| Workflow | Every node type, SLA, signals, versioning | `ci.yml` workflow-tests |
| Integration | RLS, FGA, MinIO, Weaviate, Kafka, ClickHouse | `ci.yml` integration |
| Contract | DSL ↔ TS, OpenAPI, event schemas | `ci.yml` contracts |
| AI | Recorded + live eval, red-team | `ci.yml` ai-recorded, `ai-eval.yml` |
| E2E | Demo script, 3 workflows × 2 tenants | `e2e.yml` |
| Accessibility | axe on key pages | `e2e.yml` |
| Performance (light) | 50 concurrent runs, SSE fan-out to 100 clients, inbox p95 | manual, M7 |

Critical E2E journeys:
1. Login per role and tenant.
2. Upload BoL → touchless completion.
3. Upload BoL with planted error → review → approve → TMS action.
4. Invoice → L1 → L2 → pay (Acme); same invoice → L3 (Bolt).
5. Invoice dispute with cited clause.
6. L1 approval above limit is refused.
7. Exception detected → triaged → notified.
8. Publish v2 while a v1 run is in flight.
9. Kill the agents worker mid-run → resume.
10. Cross-tenant URL access → 404/403.

---

# Phase 18 — Observability · *M4 / M7*

- [ ] OTel in API, workers and the Next.js server; context propagated through Temporal interceptors.
- [ ] Logs: JSON with timestamp, level, service, tenant_id, run_id, trace_id; never secrets or PII.
- [ ] Metrics: RED per endpoint, Temporal schedule-to-start, Kafka lag, LLM latency/cost/errors per alias, SLA breaches, DLQ size.
- [ ] Dashboards: platform health · workflow throughput · AI quality & cost · tenant view.
- [ ] Alerts: 5xx rate, latency, Temporal backlog, Kafka lag, provider errors, budget 80%.
- [ ] "Open trace" link from every run step.

---

# Phase 19 — Backup & recovery · *M7*

- [ ] Postgres dump/PITR scripted (`make backup`, `make restore`).
- [ ] MinIO bucket versioning.
- [ ] ClickHouse and Weaviate rebuild from Kafka/CDC and `data/sops` (`make rebuild-analytics`, `make reindex`).
- [ ] One restore drill: restore → E2E green → RPO/RTO recorded.

---

# Phase 20 — Demo readiness · *M7*

- [ ] `make demo` deterministic reset; recorded `demo`-mode backup video.
- [ ] 7-minute script ([PRD §6](01-prd.md#6-demo-script-7-minutes)) clean twice in a row.
- [ ] Runbooks: LLM outage, stuck runs, budget exhausted.
- [ ] README quickstart verified on a fresh clone; diagrams refreshed from code.
- [ ] Acceptance targets met ([07 §6](07-build-plan.md#6-final-demo-gate)).

---

## Definition of Done · Release gate · Traceability

These aren't repeated here, to keep one source:
- Definition of Done per task → [07 §4](07-build-plan.md#4-definition-of-done-per-task)
- Final demo gate + acceptance targets → [07 §6](07-build-plan.md#6-final-demo-gate)
- Traceability format + FR → milestone map → [07 §7](07-build-plan.md#7-traceability-rule)
- Staging/prod environments, SLOs, DR targets → [08 §7–8](08-enterprise-execution-plan.md#7-environments--release-management)
