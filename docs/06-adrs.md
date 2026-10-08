# 06 — Architecture Decision Records

Format: Context → Decision → Alternatives → Consequences. Status for all: **Accepted (2026-10-05)** unless noted.

---

## ADR-001: Modular monorepo, grouped by runtime profile

**Context.** There are four pillars and a team of one to three people. The JD stack is already operationally heavy.
**Decision.** One monorepo. Pillars are Python packages with explicit interfaces. Deployables are grouped by runtime profile: `nova-api` (request/response), `engine-worker` and `agents-worker` (Temporal workers), `ingest` (stream).
**Alternatives.** Microservice per pillar: too much operational load and premature boundaries. Single process: can't scale agents and API independently.
**Consequences.** Extract a service only when its scaling or team need is proven. The worker split by task queue already lets compute scale independently ([scaling doc](05-scaling-1k-to-1m.md)).

## ADR-002: Temporal is the workflow execution substrate; one generic interpreter workflow

**Context.** Approvals wait hours or days. Runs must survive crashes. Clients define their own logic, and "zero hardcoded business logic" is a requirement.
**Decision.** A single Temporal workflow type `NovaWorkflow` interprets an immutable, versioned YAML definition. Human tasks are signals, SLAs are durable timers, and schedules trigger detection.
**Alternatives.** LangGraph as the workflow engine: good for agent loops, but weak for multi-day human waits, SLAs, and versioned process definitions. A custom state machine on Postgres: we'd be rebuilding Temporal. Airflow/Prefect: batch-oriented and wrong for human-in-the-loop.
**Consequences.** Client logic changes never touch workflow code, so there's no determinism-versioning pain for clients. Engine code changes use `workflow.patched`. The interpreter must stay deterministic: CEL inside, everything else in activities.

## ADR-003: LangGraph agents run as Temporal activities

**Context.** Agents need multi-step tool loops. Workflows need durability.
**Decision.** Each agent invocation is one activity. LangGraph owns the inner loop (bounded steps) and Temporal owns retries and timeouts of the whole invocation.
**Consequences.** There are no LangGraph checkpointers in the prototype, because Temporal retry re-runs the agent from scratch. That's acceptable because each agent takes seconds and its side effects happen only in `deliver`. Revisit if an agent ever runs for minutes.

## ADR-004: All LLM traffic through LiteLLM → OpenRouter, code only names aliases

**Decision.** Aliases `nova-extract-text`, `nova-extract-vision`, `nova-reason`, `nova-decide`, `nova-embed` are defined in LiteLLM config. LiteLLM provides virtual keys per tenant with budgets, fallbacks, and a Langfuse callback.
**Alternatives.** Direct provider SDKs: no central budget or routing. Orkestra: documented as the future routing layer; LiteLLM's router covers the prototype.
**Consequences.** Swapping a model, or later pointing an alias at self-hosted vLLM, is a config change. Adds one network hop (~5–20 ms).

## ADR-005: Jev for `decide` nodes, with a fallback

**Context.** Most workflow branching questions are cheap booleans ("is this material?", "is this actionable?"). Frontier models are slow and expensive for that.
**Decision.** A `decide` node type calls `nova-decide` (Jev, `typesafe/jev-1.13`) with structured yes/no questions. The fallback alias is a small fast model. On parse failure, the run goes to a human task.
**Risks.** Jev is a Labs model. The fallback chain and a per-question eval set protect us.

## ADR-006: CEL for rule expressions

**Decision.** Use CEL (`cel-python`) for `rule.when` and template expressions. Expressions are compiled at publish.
**Alternatives.** Python `eval`/`simpleeval`: harder to sandbox and to edit visually. JSONLogic: verbose YAML. JMESPath: queries only, no logic.
**Consequences.** Safe, deterministic, and runs inside Temporal workflow code. Kubernetes and OpenFGA conditions use the same language, so there's one expression language across the stack. A visual rule builder can emit CEL.

## ADR-007: Postgres is the system of record; ClickHouse is fed by Debezium CDC + Kafka

**Decision.** Business writes go only to Postgres (with an outbox table). Debezium streams selected tables and the outbox into Kafka, and ClickHouse consumes them via Kafka engine + materialised views. Shipment events go straight to Kafka.
**Alternatives.** Dual writes from the app to ClickHouse: inconsistent on failure.
**Consequences.** Analytics lag by seconds, which is acceptable. CDC also gives us generic "table change → trigger workflow" for free.

## ADR-008: Tenant isolation by construction, at every store

**Decision.** `tenant_id` goes on every row, key, and topic message. Isolation is enforced by the store itself: Postgres RLS (app role is not the owner), ClickHouse row policies, Weaviate native multi-tenancy, MinIO prefixes, OpenFGA tenant relations, LiteLLM per-tenant keys.
**Consequences.** A forgotten `WHERE tenant_id` returns nothing instead of leaking data. Slight per-request overhead from `SET app.tenant_id`.

## ADR-009: OpenFGA for authorization, including approval limits

**Decision.** ReBAC model with conditions (`within_limit`) for amount-based approval authority.
**Alternatives.** Role checks in code: hardcoded business logic. OPA: policy-centric, weaker for relationship queries like "list tasks I can act on".
**Consequences.** Approval matrices live in data. Inbox uses `ListObjects`.

## ADR-010: Compose profiles (originally sized for 16 GB; machine is 32 GB)

**Decision.** Profiles `core` (~4 GB), `data` (~3.5 GB), `ai` (~2.8 GB). Langfuse v3 shares our ClickHouse, Redis, and MinIO. WSL2 capped at 24 GB on the 32 GB machine. Profiles stay so `make up` is fast to start and the local LLMs have room.
**Consequences.** Workflows 1–2 work on `core` alone. The full demo needs all three profiles.

## ADR-011: Extraction behind an `Extractor` interface; vision LLM default, DPT-2 optional

**Decision.** `Extractor.extract(pages, schema) -> Extraction` with implementations `LlmTextLayerExtractor` (default: pdfplumber + `nova-extract-text`; `nova-extract-vision` for scans, see ADR-016) and `Dpt2Extractor` (LandingAI, enabled when its key exists).
**Consequences.** We can benchmark both on the synthetic set and later add a `TemplateExtractor` (scaling tier 3) without touching agents.

## ADR-012: YAML is canonical; the graph is a view

**Decision.** The studio edits the YAML document (comment-preserving) and stores node positions in a `layout` block that the engine ignores.
**Consequences.** Git-friendly definitions, readable diffs between versions, and no hidden state in the editor.

## ADR-013: Synthetic data with planted, labelled errors

**Decision.** Generators produce documents, master data, and event streams with a manifest of planted errors.
**Consequences.** The planted errors double as an eval set (precision/recall per validator code) and make the demo deterministic.

## ADR-014: `LLM_MODE` (local / cheap / demo) to keep spend near zero

**Context.** The goal is to spend as little as possible during development, while the demo still shows best-quality extraction.
**Decision.** Three LiteLLM config files with the same aliases: `local` (all Ollama), `cheap` (cheap OpenRouter models + Jev, the dev default), `demo` (Claude-class models + Jev). Always on: Redis response cache, a global `max_budget`, and per-tenant virtual keys.
**Consequences.** No code changes between modes. `local`/`cheap` are CPU-only on this machine (~2–4 min per BoL), so the live demo is recorded in `demo` mode. Activity timeouts are per mode.

## ADR-015: Embeddings via Ollama `nomic-embed-text`, bring-your-own vectors in Weaviate

**Decision.** The `nova-embed` alias points to Ollama `nomic-embed-text` (768-dim). Weaviate uses no vectorizer module. Each collection records its `embed_model`, and changing the model means a reindex.
**Alternatives.** OpenRouter embeddings (`text-embedding-3-small`, the free `nemotron-3-embed-1b`) or an OpenAI key. Both stay available behind the same alias.
**Consequences.** Embeddings are free, local and fast (274 MB model). Indexing uses the `search_document:` prefix and queries use `search_query:`.

## ADR-016: Text-layer-first extraction; vision only for scans

**Context.** No GPU. Vision models on CPU take minutes per page and are less accurate. Most carrier PDFs, and all our synthetic ones apart from the planted scan, have a text layer.
**Decision.** `doc_extractor` reads text and word coordinates with `pdfplumber`, extracts fields with a text model using structured JSON output, and recovers bboxes by matching values back to word coordinates. `nova-extract-vision` is called only for pages without a text layer.
**Consequences.** Several times faster and cheaper in every mode, and the evidence highlights still work. Fuzzy value→bbox matching (dates and numbers reformatted by the model) needs normalisation and a test set. Scans remain the slow path.

## ADR-017: Next.js App Router for the web app; FastAPI stays the only backend

**Context.** The web app was specified as a Vite SPA with TanStack Router. The project now standardises on Next.js.
**Decision.** `apps/web` is Next.js (App Router, TypeScript, `output: 'standalone'`). Pages are Server Components that read from `nova-api`. Interactive surfaces (React Flow studio, Monaco, PDF viewer, micro-apps) are client islands loaded with `next/dynamic({ ssr: false })`. `/api/v1/*` is proxied to `nova-api` by a route handler (originally `rewrites`; changed by ADR-020).
**Alternatives.** Vite SPA + TanStack Router: simpler, but no server rendering or file routing, and not the chosen standard. Next.js as a BFF with its own data access: it would split validation and authz across two backends, which breaks "the backend is authoritative".
**Consequences.** One more Node container (~150–250 MB). Heavy editors must stay client-only. SSE goes through the ADR-020 route-handler proxy, which streams the upstream body. Supersedes the Vite/TanStack Router rows of LLD §10 (now updated).

## ADR-018: Keycloak for authentication; one realm, one Organization per tenant

**Context.** The prototype planned seeded users and a home-grown `/auth/token`. Enterprise logistics clients expect SSO, MFA, session control and, eventually, their own IdP. It's the first thing a security review asks about.
**Decision.** Keycloak 26.x is the OIDC provider in every environment, local included. There's one realm, `nova`, and each tenant is a **Keycloak Organization** (GA since 26), mapped through `tenants.keycloak_org_id`. Roles are `nova-api` client roles. Clients: `nova-web` (confidential, PKCE), `nova-api` (audience), `nova-admin` (service account). The realm is defined as code (`infra/keycloak/realm-nova.json`).
**Alternatives.** Realm per tenant: strong isolation, but N realms to configure and upgrade, and awkward for cross-tenant platform users; Organizations are the current B2B model. Auth0/Clerk/WorkOS: hosted, but an external SaaS with per-MAU cost that can't be self-hosted inside a residency cell. Home-grown JWT: no MFA, SSO or federation, so it won't pass an enterprise review.
**Consequences.** One more container (~0.7 GB) in `core`. Per-tenant IdP federation (the client's Azure AD/Okta) becomes configuration on the organization (stretch). A user belongs to one organization in the prototype; multi-org users (FDEs) need org-scoped groups later. If Keycloak is down, new logins fail, while existing sessions keep working until their tokens expire.

## ADR-019: RBAC — roles in Keycloak, every decision in OpenFGA via contextual tuples

**Context.** We need role-based access (who may design, publish, operate, approve, audit) *and* object-level ReBAC with amount limits, without two authz systems that drift and without a Keycloak→OpenFGA sync job.
**Decision.** Keycloak is the source of truth for **who has which role**. OpenFGA holds the **capability matrix** (role → capability relations on `tenant`/`workflow`/`run`/`task`) and decides every request. Roles from the token are sent as **contextual tuples** on each Check/ListObjects (OpenFGA's token-claims pattern) and are never stored. Persisted tuples are only Nova's own facts: ownership, assignment, approver + `within_limit`. Approval limits are per-role values in versioned TenantConfig, copied onto approver tuples at task creation.
**Alternatives.** RBAC in Keycloak only (role checks in Python): approval logic gets hardcoded and per-object rules become impossible. Keycloak Authorization Services: a second policy engine, and weak for "tasks I can act on" queries. Syncing roles into FGA through a Keycloak event-listener SPI (e.g. keycloak-openfga-event-publisher): a Java extension to operate, with sync lag and new failure modes.
**Consequences.** One authz source (the FGA model) and one identity source (Keycloak). Revoking a role takes effect within the 5-min access-token TTL. Background code without a user token (the engine assigning tasks) uses role usersets (`tenant:acme#finance`), so it needs no user list. Notifying "all finance users" resolves members through the Keycloak Admin API. `platform_admin` manages tenants but has no tenant-data capability.

## ADR-020: Next.js as a BFF; tokens stay server-side

**Context.** The earlier web plan put a session cookie in the browser and proxied with `rewrites`, which can neither attach nor refresh a bearer token.
**Decision.** Next.js route handlers implement the OIDC client with `openid-client` (auth code + PKCE): `/api/auth/login`, `/api/auth/callback`, `/api/auth/logout` (RP-initiated logout). Tokens are stored in Redis under a random session ID, and the browser only gets an httpOnly `SameSite=Lax` cookie. `app/api/v1/[...path]` proxies to `nova-api`: it refreshes tokens near expiry, attaches `Authorization: Bearer`, and streams responses (SSE included). `proxy.ts` (Next.js 16's name for middleware) guards pages.
**Alternatives.** Auth.js v5 with the Keycloak provider: less code, but by default it keeps tokens in an encrypted JWT cookie, and refresh handling is DIY anyway. Browser-held tokens (keycloak-js): access and refresh tokens are exposed to XSS.
**Consequences.** No token is reachable from browser JS. Same origin, so no CORS. Redis becomes a hard dependency of web login (it's already in `core`). CSRF is covered by `SameSite=Lax` plus an Origin check on mutating `/api/v1` calls.

## ADR-021: Jaeger as the dev trace backend until the M4 collector

**Context.** The OTel bootstrap existed since M0, but nothing produced or received spans: no backend in compose, an empty `OTEL_EXPORTER_OTLP_ENDPOINT`, no instrumentation, and the engine never started tracing. The M0/M1 deep test (2026-10-08) caught it.
**Decision.** `core` runs Jaeger all-in-one (in-memory, UI on `:16686`), and every Python process exports OTLP/HTTP to it via `nova_core.telemetry.configure_tracing`. Spans come from FastAPI (`FastAPIInstrumentor`, health probes excluded), the asyncpg driver (`AsyncPGInstrumentor`; the SQLAlchemy instrumentor doesn't support SQLAlchemy 2.1 yet), and Temporal (`temporalio.contrib.opentelemetry.TracingInterceptor` on the shared client, which also applies to workers built on it). One trace covers API request → `StartWorkflow` → workflow → activities → SQL. structlog stamps `trace_id` on every log line.
**Alternatives.** Wait for the M4 collector + Langfuse: no trace visibility through M2/M3, exactly when the agent pipeline is built. Console exporter: unreadable for multi-service traces.
**Consequences.** ~100 MB extra; traces are lost on restart (fine for dev). Services speak plain OTLP, so the M4 OTel Collector → Langfuse swap is an endpoint change, not a code change. Tests keep the endpoint empty, so tracing stays a no-op there.
