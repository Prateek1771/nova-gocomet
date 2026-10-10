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

## ADR-022: W1 build decisions (bbox, schema, reference data, LLM routing, Jev decisions API)

**Context.** M2 hit gaps the specs left open, and live runs showed the planned `decide` model didn't behave as assumed.
**Decision.**
- **bbox** = `[x0, top, x1, bottom]` in PDF points, top-left origin (pdfplumber's native frame). The UI renders the PDF and scales the overlay, so there's no page-image endpoint.
- **Confidence is deterministic**: the fuzzy score of matching each extracted value back to word boxes (dates and numbers compared as values). Values read by the vision model have no word box and are capped at 0.6, so scans always reach a human.
- **`bol_v1` gains `cargo_lines[]`** (one row per container line) so `WEIGHT_SUM` has data. UN/LOCODE reference data is a CSV in `definitions/reference/`; bookings are an RLS table seeded per tenant.
- **`LLM_MODE=cheap` means cheap cloud models**, not local qwen: `google/gemini-2.5-flash-lite` for extract/vision/reason (~$0.0003 per BoL, ~3 s), host Ollama for the decide fallback and embeddings. `local` stays fully offline; `demo` uses Claude Sonnet 5.5. This supersedes the `cheap` column of brainstorm §6.3 (the user chose speed over $0 for dev).
- **`decide` uses Jev's decisions API**, not chat. `typesafe/jev-1.13` is a decisions model (`POST /api/alpha/decisions`, typed `noul` answers = P(yes), ~1-2 s, ~$0.00002). `typesafe/jev-router` (chat) routes to a reasoning model: ~10 s, ~$0.0025, and it truncated JSON under a 300-token cap. LiteLLM forwards `/jev/decisions` as a pass-through so the key and egress stay in the gateway. A decide question carries `criteria` (what yes/no mean) and a `threshold` in the definition (DSL change); chat aliases with the same criteria are the fallback, then a human.
- **High- and medium-severity check failures route to review deterministically** in `bol_intake.yaml`; the model only judges the residue (low-severity issues). Found by the red-team design: with only `high` deterministic, a fooled or injected model could wave a weight discrepancy through.
- **Extraction prompt hardening** (red-team v1): the document is wrapped in a delimiter derived from its own sha256 (a document can't print the tag that closes it), printed tag-like text is defanged, and remarks addressed to an AI are declared non-values.
- **Gateway cache hygiene**: an unparseable answer is re-asked once past LiteLLM's Redis cache, and every Temporal retry attempt bypasses the cache. A provider once returned a cut-off body that the cache then replayed to all retries.
**Consequences.** All 10 seed BoLs route as specified live (4 touchless, 6 to review with the right evidence) at 2-8 s and ≤ $0.0004 per BoL. Bolt's W1 divergence stays in M4. LiteLLM's global `max_budget` isn't enforced without its database (M4 adds it with per-tenant keys).

## ADR-023: Studio edits YAML by splicing source ranges, not by re-serialising

**Context.** Rule 7 says YAML is canonical and graph edits must keep comments and untouched lines. The M2 spike (`apps/web/src/features/studio/yaml-graph.ts`) found that `parseDocument → mutate → toString()` in the `yaml` library is not byte-preserving, even with `keepSourceTokens`: flow maps are re-padded (`{type: string}` → `{ type: string }`) and folded `>-` scalars are re-wrapped.
**Decision.** The AST only *locates* things. Each graph edit (set field, move, add node/edge, remove node) is a text splice at the target node's source range; new content is generated with `yaml` and inserted with the collection's indent. Functions are text → text, keep CRLF/LF, and refuse invalid YAML.
**Alternatives.** CST-level editing (`yaml`'s `CST` API): exact, but much more code for the same result. Re-serialising with tuned options: fixes padding, not folding, and breaks again on the next style difference.
**Consequences.** Untouched bytes are identical by construction (vitest: no-op, one-line edit, append, remove, CRLF). M3's React Flow canvas calls these functions and re-renders from `toGraph(text)`.

## ADR-024: Studio build decisions (catalog contract, SSE by polling, Monaco pin)

**Context.** M3 needs palettes and reference checks for agents/actions/apps, a live run view, and a schema-aware YAML editor, without breaking rule 9 (services never import each other) or rule 7 (YAML canonical).
**Decision.**
- **Catalog as a checked-in contract.** `definitions/catalog.yaml` lists agents, actions and micro-apps with their `with` params. The API serves it (`/catalog/*`) and `_check` reports `unknown_agent|action|app` with the node id. `tests/unit/test_catalog.py` fails if it drifts from the engine's `ACTIONS` registry or the agents' `AGENTS` dict, so the API never imports either service.
- **SSE by polling the projection.** `GET /runs/{id}/stream` re-reads the run detail every 0.5 s and emits a `snapshot` only when it changes (event id = hash, so `Last-Event-ID` skips a duplicate), plus `end` on a terminal status. Measured step-to-browser latency through the Next.js proxy: max 0.6 s.
- **Studio edits stay text splices** (ADR-023), extended with `setPath` (nested values; flow collections are re-inlined as one line), `appendItem` / `removeItem` (rule cases), `removeEdge`, `moveNodes`. A seeded property test (250 random op sequences, LF + CRLF) checks comments and unrelated lines survive.
- **Monaco self-hosted, pinned to 0.54.** 0.55+ ships an `exports` map that breaks `monaco-yaml`'s worker import (`monaco-worker-manager` imports `monaco-editor/esm/vs/editor/editor.worker.js`). The DSL JSON Schema is copied into `apps/web/src/dsl/` by `pnpm gen:dsl` (the web image only sees `apps/web`).
**Alternatives.** Catalog via a `/catalog` call to the engine/agents (a runtime coupling for static data). Postgres `LISTEN/NOTIFY` for SSE (better at many viewers; polling is one indexed read per viewer per 0.5 s, fine for the prototype). Monaco from the CDN (no CSP-friendly self-hosting, and still needs the worker fix).
**Consequences.** Adding an action or agent = register it + add a catalog entry (the unit test reminds you). Upgrade path: swap the SSE loop's poll for `LISTEN run_steps_changed` without changing the event format; unpin Monaco when `monaco-yaml` supports the exports map.

## ADR-025: Free-tier LLM routing (Groq + OpenRouter `:free`) as the dev default

**Context.** Until paid models are approved, LLM spend must be $0. ADR-022 made `cheap` (paid OpenRouter models + Jev) the dev default.
**Decision.** Supersedes ADR-022's default mode only.
- New `LLM_MODE=free` (`infra/litellm/config.free.yaml`), now the default in settings, compose and `.env.example`. Text, reason and decide aliases go to Groq's free tier (`gpt-oss-120b` / `gpt-oss-20b` / `qwen3.8-27b`, `rpm: 30`). On a 429 or an error, LiteLLM `fallbacks` move to OpenRouter `:free` models (`nemotron-3-super-120b-a12b:free`, then `openrouter/free`). No free Groq model takes images, so `nova-extract-vision` goes straight to `gemma-4-31b-it:free` → `gemma-4-26b-a4b-it:free` → `openrouter/free`. Embeddings stay on Ollama.
- Jev's decisions API is paid, so `decide` skips it when the mode is `free` (as for `local`) and uses the chat aliases.
- `max_budget: 1` USD as a tripwire: free models report $0, so spend only appears if a paid model slips into the config.
- `cheap` and `demo` stay unchanged as the paid OpenRouter paths. Switching back is one `.env` line.
**Consequences.**
- Free tiers are rate-limited: Groq allows 30 RPM / 1K RPD per model, and OpenRouter free pools hit 429s upstream. The 7-day Redis cache and the fallback chain absorb most of this. A burst of uploads can still slow down or fail to a human task.
- Free OpenRouter providers may log prompts, so free mode is for synthetic demo documents only.
- `:free` ids churn. Re-list them with `curl https://openrouter.ai/api/v1/models` (keep `pricing.prompt == "0"`) and with Groq's `/openai/v1/models`, then re-pin.
- The ai-eval gates are unchanged. A free model that misses a gate is a finding, not a reason to loosen the gate.
**Measured (2026-10-09, `docs/evals/free-baseline.json`).** All gates pass: extraction micro-F1 0.986, validator recall 1.0 (5/5), decide accuracy 0.975 (40 cases). Text extraction takes about 2–10 s per BoL on Groq. The one scanned BoL took about 9 min, because both free Gemma vision models were rate-limited upstream and `openrouter/free` sometimes cut off its answer. Free vision is the weak spot; pay for vision first when budget appears. The cost figures in that report are LiteLLM pricing Groq at its paid list price. They predate zeroing Groq's price in the config, and the free plan doesn't bill.

## ADR-026: OpenFGA holds only the model; every fact is a contextual tuple

**Context.** ADR-019 sends roles as contextual tuples but persists Nova's own facts in OpenFGA: `workflow#tenant`, `run#workflow`, `task#run`, `task#assignee` and `task#approver with within_limit`. That is a second copy of rows Postgres already holds, written from the API *and* the engine. Every write is a dual write that can drift or half-fail, existing runs need a backfill, the engine gains an HTTP dependency, and tuples have to follow escalations and config edits.
**Decision.** Supersedes ADR-019's "persisted tuples" clause; the rest of ADR-019 stands. OpenFGA stores **only the authorization model** (`infra/openfga/model.json`, generated from `model.fga` by `make gen`). For each check, `nova_api.authz` reads the object's row under RLS and sends its structure as contextual tuples alongside the roles:
- `task → run → workflow → tenant`
- the assignee role userset
- for a task whose payload carries an `amount`: approver usersets with `within_limit{limit}`, with limits from the run's pinned `TenantConfig.approval_limits`

The approver set is the assigned role plus every role with an equal or higher limit (the approval hierarchy). A task assigned to the unlimited top role, the controller, stays with that role: dual control. An approval task gets no plain assignee tuple, so no role can bypass its limit. The inbox lists open rows and batch-checks `can_claim`; `ListObjects` isn't used. Model deltas: `task.can_claim = can_complete`, and `tenant#tenant_admin` is assignable, because needs_attention tasks go to it.
**Alternatives.** Persisted tuples (ADR-019 as written): the dual-write and backfill cost above. Authorization in SQL: violates rule 10 and duplicates the model.
**Consequences.**
- No sync, no backfill, and no engine change. The in-memory store is enough: the API re-creates it on first use or after an OpenFGA restart.
- A check costs one tenant-scoped SQL read plus one OpenFGA call; the inbox makes one batch call per 50 tasks.
- An object in another tenant doesn't resolve under RLS, so it is a 404 before OpenFGA is even asked.
- OpenFGA being unreachable fails closed (503).
- If tasks ever reach the thousands per user, revisit `ListObjects` with persisted tuples for that one query.

## ADR-027: Audit log hash chain in a Postgres trigger

**Context.** FR-X.1 asks for a tamper-evident audit log in which every entry carries the definition and config versions it acted under. Rows are written by the engine and by the API, and later by more writers.
**Decision.** Migration 0004 adds `seq`, `definition_version`, `config_version`, `prev_hash` and `hash` to `audit_log`.
- A `BEFORE INSERT` trigger takes a per-tenant advisory lock, assigns `seq = last + 1`, and sets `hash = sha256(prev_hash | row)` through one SQL function, `audit_digest`.
- `audit_verify()` recomputes the chain and returns the first broken link.
- Existing rows are backfilled in id order.
- `GET /audit` and `GET /audit/verify` need `can_read_audit`, and `make audit-verify` is the scheduled job.
- The engine stamps versions from the run; the API audits publish, config publish and upload in the same transaction as the change.
**Alternatives.** Hashing in application code: every writer must remember to do it, and concurrent writers race on `prev_hash`. An external ledger or WORM store: more infrastructure than the prototype needs.
**Consequences.** Every writer is chained without code. `seq` (not `id`) orders the chain, because identity ids are handed out before the lock. Writes to the audit log are serialised per tenant (fine at this volume). An owner can still edit or delete rows, but verification shows exactly where. Anchoring the head hash externally (e.g. nightly to object storage) is a stretch item.

## ADR-028: Per-tenant LiteLLM virtual keys, derived rather than stored

**Context.** Every tenant shared the gateway's master key, so there was no per-tenant spend or budget (FR-2.4). A budget refusal looked like any other error and was retried.
**Decision.** Each tenant's key is `sk-nova-t-` + `HMAC-SHA256(LLM_KEY_SECRET, tenant_id)` (`nova_core.llm_keys`). Nothing is stored; LiteLLM keeps only its hash.
- The API provisions the key (`/key/generate`, or `/key/update` when it exists) with `max_budget = TenantConfig.llm_budget_usd` and a 30-day period. It does this at startup (retrying while the gateway boots) and on every config publish.
- Agents derive the same key per call from the request's `tenant_id`.
- LiteLLM's `budget_exceeded` refusal becomes `BudgetExceeded`, re-raised as a non-retryable `ApplicationError(type="BudgetExceeded")`. The decide fallbacks don't swallow it, because every alias bills the same key.
- The engine's needs_attention task carries `error_type` and `message`, and the step-failure screen says "budget exhausted, raise it in Admin, then Retry".
- `/admin/llm-budget` (`can_manage_budgets`) reads spend from `/key/info`.
**Alternatives.** Store generated keys in a table (encrypted): more code and a secret at rest for no gain. Enforce budgets in Nova: duplicates what the gateway already meters.
**Consequences.** Rotating `LLM_KEY_SECRET` re-keys every tenant at once (re-run provisioning). **LiteLLM skips budget checks for zero-cost models**, so in `LLM_MODE=free` budgets never trip; the stop is real in `cheap`/`demo`. The mapping is tested with LiteLLM's error shape and the engine path with a time-skipping test. Spend from failed attempts is metered by the gateway but not booked on the run (follow-up).

## ADR-029: OTel Collector in core; Langfuse v3 in the `ai` profile via OTLP

**Context.** ADR-021 promised that the collector → Langfuse swap would be an endpoint change. M4 needs LLM traces with model, tokens and cost per run (FR-2.4, 08 §5).
**Decision.**
- **Collector:** `otel-collector` (contrib 0.135) joins `core`, and every service exports to it. `collector.yaml` forwards to Jaeger; `collector.ai.yaml` also forwards to Langfuse's OTLP endpoint.
- **`ai` profile:** Langfuse v3 (`langfuse-web` on :3400 + `langfuse-worker`) on the shared Postgres (db `langfuse`), ClickHouse (now in the `data` and `ai` profiles), Redis and MinIO (bucket `langfuse`). Headless init creates the org, project and API keys, and `langfuse-init` creates the database and bucket idempotently. `make up-full` runs it.
- **LLM spans:** they carry OTel GenAI attributes (`gen_ai.request.model`, `gen_ai.usage.*`, `gen_ai.usage.cost`, `langfuse.observation.type=generation`) plus `nova.tenant_id`/`nova.run_id`. They carry ids, model, tokens and cost, never prompts or document content.
- **Trace links:** `run_steps.trace_id` is written from the activity's span. The run page links the run's trace in Jaeger and, when `LANGFUSE_URL` is set, its LLM calls in Langfuse.
**Alternatives.** The LiteLLM Langfuse callback: it sees only the gateway's call, not the run, and needs Langfuse keys in the gateway. A separate ClickHouse for Langfuse: about 1 GB more RAM.
**Consequences.** The `ai` profile adds about 2.5 GB including ClickHouse. One trace per run spans API, workflow, activities, SQL and LLM calls; Jaeger shows all of it, and Langfuse the generations. Langfuse being down only backs up the collector's retry queue, and core keeps working.

## ADR-030: Weaviate in `core`, our own vectors from `nova-embed`, plain httpx

**Context.** W2 needs rate-contract clause search (docs/04 W2), and FR-4.3 asks for Weaviate tenants. The HLD put Weaviate in the `ai` profile, but W2 must run on a plain `make up`.
**Decision.** Weaviate 1.32 joins `core` (port 8090, about 300 MB).
- **Collection:** one, `ContractClause`, with native multi-tenancy (one Weaviate tenant per Nova tenant id) and `vectorizer: none`.
- **Vectors:** we bring them from the `nova-embed` alias (host Ollama nomic-embed-text, 768-dim), with ADR-015's `search_document:` / `search_query:` prefixes.
- **Client:** `nova_core.vectors` speaks REST + GraphQL over httpx, with no SDK.
- **Search:** hybrid (BM25 + vector, alpha 0.5), filtered by carrier.
- **Indexing:** idempotent, with ids derived from tenant + clause id. It runs in the API's startup task (retrying while the embedder comes up) and as `make reindex`.
**Alternatives.** The `ai` profile: W2 wouldn't run without it. pgvector: one store fewer, but no hybrid BM25 and no per-tenant shards. The Weaviate SDK: a large dependency for four calls.
**Consequences.**
- Tenant isolation holds at the shard level; an integration test shows another tenant's clause can't match.
- A model change means a reindex: `embed_model` per collection is still to add when a second model appears.
- Retrieval quality depends on the embedder being up. Indexing retries, and the matcher fails retryably (then needs_attention) if Weaviate or the embedder is down.

## ADR-031: Dwell facts from Postgres `container_events` until M6

**Context.** W2's accessorial check needs dwell facts (gate-out → gate-in). The design puts shipment events in ClickHouse via Kafka, which lands in M6.
**Decision.** M5 seeds a Postgres `container_events` table (RLS) and reads it through one function, `invoices.dwell_days(events, container)`. Free time is a property of the rate contract (`free_time_days`). Chargeable days are started days out minus free days, so a container out 3 d 7 h with 5 free days gives 4 days out and 0 chargeable. The matcher flags `DET_NOT_SUPPORTED` deterministically, and the `decide` node judges the accessorial facts as the residue (rule 3).
**Alternatives.** Pull ClickHouse and the event pipeline into M5: M6's whole data layer ahead of time. Ask the model to read events: violates rule 3.
**Consequences.** M6 swaps the source of `dwell_days` to the ClickHouse `dwell_time_hours` metric with the same contract. Until then, events are seed data, not a live feed.

## ADR-032: W2 build decisions

**Context.** docs/04 W2 fixed the graph and the approval matrix, but not the data contracts, the duplicate mechanics, citation trust, or what "wrong currency" does.
**Decision.**
- **Duplicate:** a deterministic agent `invoice_dedupe` (no model), placed before the matcher as in the diagram, finds an earlier document's extraction with the same carrier + invoice number. Identical bytes were already deduplicated at upload, and a duplicate run makes exactly one model call (extraction), which an integration test asserts.
- **Grounded citations:** Weaviate returns candidates per line, and one `nova-reason` call per invoice picks a clause per line. Code keeps a pick only if it is among that line's candidates and for the same charge code, and the rate always comes from the clause row in Postgres, never the model. An ungrounded pick becomes `NO_CONTRACT_RATE`.
- **Matcher checks:** `RATE_OVER_CONTRACT` (high, > 0.5% over the cited rate, after FX), `CURRENCY_MISMATCH` (high), `DET_NOT_SUPPORTED` (high), `LINE_NOT_ON_PO` / `QTY_MISMATCH` / `NO_CONTRACT_RATE` (medium), `PO_NOT_FOUND` / `NO_CONTRACT` / `FX_UNKNOWN` (high). Variance compares the invoice in USD with qty × contract rate.
- **Routing (approval rule):** any high finding, an unjustified accessorial, total > `invoice.l2_limit_usd` or variance > `invoice.l2_variance_pct` → L1 then L2. Otherwise, variance ≤ auto % and total ≤ auto limit → pay; else L1 only. So wrong currency goes to two levels.
  - L1-only and L1→L2 are separate task nodes, so the graph reads like the spec.
  - Every review task carries `amount`, so OpenFGA `within_limit` applies. An L1 above the ops lead's limit is taken by a higher-limit role (demo step 8: the ops lead gets a 403).
  - Bolt adds `controller_signoff` as a subflow after L2 when the total is above its L2 limit, and branches on the child's status.
- **Actions:**
  - `erp.post_payable` upserts `invoices` with status `posted`, unique per carrier + number.
  - `carrier.dispute_email` writes an `email.dispute` row to the outbox whose body quotes each disputed line's clause and the reviewer's reason (a dispute requires a reason, in `invoice_review`'s output_schema).
- **Extractor:** prompt hints and evidence rules moved into each schema (`x-nova-prompt`, `x-nova-evidence`), so the extractor holds no BoL-specific text.
- **Config:** the seed adds keys a milestone introduces (`invoice.*`) as a new TenantConfig version when the latest lacks them, never overwriting admin edits.
**Consequences.** All 8 cases route as specified for both tenants (unit routing on the real YAML rules, plus end to end on real Postgres, Weaviate, Temporal and OpenFGA). FX rates are seeded per tenant (`fx_rates`); a live FX feed is out of scope.

## ADR-033: `LLM_MODE=openai` for paid-quality testing

**Context.** `free` (ADR-025) is $0 but rate-limited. Its free vision models are weak, and because every model costs $0, LiteLLM skips budget checks (ADR-028), so the per-tenant budget stop can't be shown live. An OpenAI key is now available for testing.
**Decision.** A fifth gateway config, `infra/litellm/config.openai.yaml`:
- every chat alias (`nova-extract-text`, `nova-extract-vision`, `nova-reason`, `nova-decide`) → `gpt-4.1-mini`, which also reads images; `nova-decide-fallback` → Groq gpt-oss-120b
- not `gpt-4.1-nano` for decide: it scored 0.80 on the decide eval, under the 0.85 gate
- `nova-embed` stays on Ollama nomic-embed-text, because a new embedder means a reindex
- fallbacks to the free Groq / OpenRouter deployments; Redis cache; a gateway `max_budget` of 5 USD
- per-tenant virtual keys and budgets apply as in every mode

Code still names only aliases (rule 6). Jev's decisions API is reached only in `cheap`/`demo`, so `decide` uses the chat aliases here. The mode is chosen per run (`LLM_MODE=openai docker compose …`); `free` stays the dev default.
**Consequences.** Real cost per run shows in the UI and Langfuse, and the budget stop degrades to a needs_attention task live. Cost is about $0.001–0.003 per document at gpt-4.1-mini prices. Prompts go to OpenAI, so this mode is for synthetic documents only, like `free`. Moving to a more literal model exposed vague prompts (decide criteria) and a brittle contract (echoing `§` clause ids). The fixes are model-agnostic: criteria name the exact fields and cases, and the matcher picks a retrieved candidate by number.

## ADR-034: LandingAI DPT-2 for scanned documents, behind the gateway

**Context.** Pages without a text layer went to a multimodal chat model (`nova-extract-vision`, ADR-016). That gave no word boxes (no highlights on scans), a flat 0.6 confidence cap (ADR-022: every scan reached a human) and slow, flaky free vision models (about 9 min for one scan in the free eval). ADR-011 kept DPT-2 (LandingAI ADE) as the optional extractor. Every model call goes through LiteLLM (budgets, tenant keys, Langfuse), and LiteLLM has no LandingAI provider.
**Decision.**
- A new alias `nova-extract-scan`, served by a LiteLLM custom provider (`infra/litellm/ade_handler.py`, `custom_provider_map`). It runs DPT-2 Parse (`dpt-2-20260903`, pinned), then ADE Extract with the document's JSON schema (our `x-nova-*` hints and `$` keys stripped), and returns `{markdown, chunks, grounding, extraction, versions, credits}`.
- Services name only the alias (rule 6) and the LandingAI key lives only in the gateway (`DPT_LANDING_API_KEY`).
- Usage is reported as `completion_tokens = credits × 1000`, priced at $0.01 per 1000 in the config, so the gateway books $0.01 per ADE credit on the tenant's virtual key. Because the price is non-zero, budgets apply in every mode, `free` included.
- `num_retries: 0`, so a failed call isn't retried and billed for a second parse. The alias is in free/openai/cheap/demo; `local` stays offline.
- `doc_extractor`: a document with any scanned page goes to `nova-extract-scan`.
  - DPT-2 chunk boxes (normalised, top-left) become word boxes in PDF points (`matching.chunk_words`), so evidence and confidence come from the same value matching as text PDFs. Words inside `low_confidence_spans` carry that confidence.
  - An extraction that fails our schema is redone by `nova-extract-text` over the DPT-2 text.
  - On any other gateway error (alias absent, provider down) the extractor falls back to the vision path, with a `note` on the output. `BudgetExceeded` is not swallowed.
  - Text-layer PDFs are unchanged.
- Scans are trusted on evidence; the ADR-022 cap now applies only to the vision fallback. The planted scan (`bol_10`) carries an ink stain over a container number. DPT-2 omits the value rather than guessing, so the BoL shows 1 container against 2 booked, which is `BOOKING_MISMATCH` and a review.

**Consequences.**
- Scans get real highlights and about 20–35 s per page. They cost about $0.043 per page (3 parse credits + about 1.3 extract credits), cached by the gateway like any response.
- ADE Extract doesn't use our injection-hardened extraction prompt. Deterministic checks and human tasks still gate every state change (rules 3–4).
- Table-cell references in Extract's metadata don't resolve in the Parse response, so evidence comes from value matching, not references.
- Chunk boxes are split evenly by line and character, so highlights are approximate within a chunk. Synthetic documents only, as with every cloud mode.
