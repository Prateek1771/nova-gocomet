# 00 — Brainstorm & Decisions Log

Date: 2026-10-05. Phase: design only, no code.

## 1. What we're building, in one line

A working prototype of Nova's four pillars. The JD lists them as Workflow Orchestrator, Agents Orchestrator, No-Code App Builder, and Data Layer. The prototype runs three logistics workflows end-to-end on the JD's real stack, so the demo shows the actual system working, not slideware.

## 2. Clarifications captured

| Question | Decision | Consequence |
|---|---|---|
| Audience | GoComet application / demo | Docs use Nova's vocabulary (4 pillars, 5-stage agent pipeline, FDE). The demo is scripted as a story ([PRD §6](01-prd.md#6-demo-script-7-minutes)). |
| Workflows | All 3, fully built: **BoL intake**, **Invoice ↔ PO match**, **Shipment exception monitoring** | Covers the document path (extraction → validation → approval) and the event path (Kafka → ClickHouse → agent). |
| Stack depth | **Full JD stack** runs in docker-compose | Kafka, Debezium, ClickHouse, Temporal, OpenFGA, Weaviate, Langfuse, LiteLLM, Postgres, dbt are all real, not mocked. |
| Hardware | **32 GB RAM**, i5-1145G7 (4C/8T), Intel Iris Xe: **no usable GPU for LLMs** | The full stack plus local LLMs fit (WSL2 cap 24 GB). Local models are CPU-only, so they're slow, and we design around that (§6.3). |
| LLM provider | **OpenRouter via LiteLLM gateway** | One egress point. Model *aliases* in LiteLLM let us swap models without code changes. |
| Quick decisions | **Jev** (`typesafe/jev-1.13` on OpenRouter) | Becomes a first-class `decide` node type in the DSL: cheap, ~300 ms–2 s structured yes/no decisions for routing and triage. |
| Sample data | **Synthetic**, generated, with planted errors | Seed generator produces BoL/invoice PDFs, POs, rate contracts, and a shipment event stream. The errors are known, so every validator has something to catch. |
| Diagrams | archify skill (installed globally) | Diagrams go in `docs/diagrams/` as standalone interactive HTML. |

## 3. How each JD technology lands in the prototype

| Tech | Runs? | Role in prototype |
|---|---|---|
| React + React Flow | ✅ | Workflow Studio (graph ↔ YAML), live run view, task inbox |
| Python + FastAPI | ✅ | `nova-api` plus the worker processes |
| Postgres | ✅ | System of record, plus the Temporal / OpenFGA / LiteLLM / Langfuse metadata DBs |
| Temporal | ✅ | Durable execution for every workflow run (JD says "scheduling"; we use it for both scheduling and long-running human waits) |
| LangGraph | ✅ | All agents; the 5-stage pipeline is a shared graph template |
| LiteLLM | ✅ | Gateway: aliases, fallbacks, per-tenant virtual keys + budgets |
| OpenRouter + Jev | ✅ | Model provider; Jev for `decide` nodes |
| Langfuse | ✅ | LLM traces, cost, and evals; every agent run links to its trace |
| OpenTelemetry | ✅ | API + workers export traces; Temporal interceptors propagate context |
| OpenFGA | ✅ | Relationship authz, including approval limits via conditions |
| Kafka | ✅ | `shipment.events`, CDC topics, `nova.outbox` |
| Debezium | ✅ | Postgres CDC (outbox + selected tables) → Kafka |
| ClickHouse | ✅ | Analytical store; also Langfuse v3's backing store (shared instance, separate DB) |
| Weaviate | ✅ | SOPs, carrier playbooks, contract clauses; native multi-tenancy; bring-your-own vectors from `nova-embed` |
| Ollama | ✅ | `nomic-embed-text` embeddings (always); local LLMs in `LLM_MODE=local` (§6.3) |
| dbt (+ MetricFlow) | ✅ (CLI job) | Semantic layer over ClickHouse: `eta_slip_hours`, `invoice_variance_pct`, `touchless_rate` |
| DPT-2 | ⚙️ adapter | `Extractor` interface; default = vision LLM via LiteLLM, DPT-2 adapter switched on if a LandingAI key exists |
| PageIndex | ⚙️ stretch | Long rate-contract reasoning in the invoice workflow (tree index instead of chunked RAG) |
| Orkestra | 📄 documented | Model routing is covered by LiteLLM router + aliases for the prototype |
| DataHub / OpenMetadata | 📄 documented | Heavy (Elasticsearch + Kafka + several services) and adds no demo value; catalog role described in HLD and scaling doc |

## 4. Product brainstorm — what makes this demo land

The JD's own language gives the evaluation criteria: *"Zero hardcoded business logic"*, *"governed business context, not ad-hoc prompting"*, *"tenant-isolated by construction"*, *"flag exceptions before humans notice"*. Every demo beat should prove one of these.

1. **Two tenants, same engine.** Tenant A requires 2 approval levels above $10K; Tenant B requires 3 above $5K plus a finance check. Same code, different YAML. This proves "zero hardcoded logic" more convincingly than anything else.
2. **Edit live.** Drag a node in React Flow and the YAML diff appears. Publish v2, and new runs use v2 while in-flight runs finish on v1 (Temporal pins the version).
3. **Evidence, not answers.** Every agent output carries *evidence*: page + bounding box for extracted fields, PO line IDs for matches, SQL + rows for analytics claims, SOP chunk IDs for recommendations. The UI renders all of it.
4. **Cheap brain / expensive brain.** Show the Langfuse cost panel. Jev makes about 90% of the routing decisions for fractions of a cent; the big model runs only for extraction and reasoning.
5. **Flag before humans notice.** The exception monitor raises an ETA-slip exception *before* the simulated customer email arrives.
6. **Kill a worker mid-run.** Restart it and the run resumes (Temporal). A 5-second proof of production thinking.

## 5. Competitive / reference landscape

The space is funded and moving fast. These are worth tracing for UX patterns:

| Product | What to borrow |
|---|---|
| **Pallet (CoPallet)** — $27M Series B 2025, ~$50M total | "AI workforce inside existing TMS/ERP" framing; agents that complete whole workflows, not single steps |
| **Vooma** — $16.6M (Index, Craft) | Multi-channel agents (email/text/voice) for brokers; intake-from-email UX |
| **n8n / Retool Workflows** | Node editor ergonomics: side panel config, run-data inspection per node, pinned test data |
| **Temporal Web UI** | Event-history timeline for a run; we mirror it in "Run detail" |
| **Langfuse UI** | Trace tree + cost; link out instead of rebuilding |
| **Linear / Vercel dashboards** | Visual polish bar: dense, keyboard-first, quiet colors, fast |

Sources: [Pallet competitor map](https://yespress.io/pallet/who-competes-with-pallet-mapping-the-freight-ai-automation-field), [Vooma raise (BusinessWire)](https://www.businesswire.com/news/home/20241202150186/en/Vooma-Scores-Over-$16-Million-in-Seed-and-Series-A-Funding-Led-by-Index-and-Craft-Ventures), [Jev on OpenRouter](https://openrouter.ai/labs/jev), [archify](https://agentconn.com/skills/archify).

## 6. Open questions: resolved 2026-10-05

| # | Question | Resolution |
|---|---|---|
| Q1 | Jev availability (OpenRouter Labs) | **Resolved.** OpenRouter access confirmed in use. Keep the LiteLLM fallback `nova-decide: jev → local/cheap model` as a safety net, not because access is in doubt. |
| Q2 | Embeddings provider | **Resolved.** Default `nova-embed` = **Ollama `nomic-embed-text`** (274 MB, 768-dim, free, local). Alternatives behind the same alias: OpenRouter has 37 embedding models, including `openai/text-embedding-3-small` and the free `nvidia/nemotron-3-embed-1b:free`, or an OpenAI key. See §6.1. |
| Q3 | Langfuse v3 needs ClickHouse + Redis + S3 | **Resolved.** Everything runs in Docker. Langfuse shares our ClickHouse, Redis and MinIO with a separate DB, key prefix and bucket. Ollama also runs as a container. |
| Q4 | React Flow ↔ YAML round-trip fidelity | **Resolved by design.** See §6.2. |
| Q5 | LLM spend | **Resolved.** Spend as little as possible: three LLM modes with local Ollama models, response caching and hard budget caps. See §6.3. |
| Q6 | Docker on Windows: WSL2 memory | **Resolved.** 32 GB machine. `.wslconfig` `memory=24GB`, `processors=8`, `swap=8GB`. Budget in [HLD §7](02-hld.md#7-deployment-prototype). |
| Q7 | Real GoComet integrations | Out of scope; the simulator emits GoComet-shaped events. |

### 6.1 Embeddings: decision details

- Weaviate runs **bring-your-own-vectors**. No Weaviate vectorizer module: we embed through LiteLLM alias `nova-embed`, so every embedding is traced and budgeted like any other call.
- Default: `ollama/nomic-embed-text`. It needs task prefixes: `search_document: ` when indexing, `search_query: ` when querying. The embed helper adds them.
- **Pick once per collection.** Vectors from different models aren't comparable, so changing the model means re-embedding. Each collection stores `embed_model` metadata, and `make reindex` rebuilds it. The SOP corpus is small (~15 docs), so this takes seconds.
- Fallback order behind `nova-embed`: Ollama → OpenRouter `nvidia/nemotron-3-embed-1b:free` → `openai/text-embedding-3-small`. A fallback must use the same model as the collection, so switching is a reindex, not a silent runtime fallback.

### 6.2 React Flow ↔ YAML sync: how we avoid losing comments and order

The risk: users edit both the graph and the YAML. If graph edits re-serialise the whole document, comments, key order and formatting are lost, and diffs between versions become noise.

Design:
1. **YAML text is the only source of truth.** The graph is a projection of it. No separate graph JSON is saved.
2. **Parse with eemeli/`yaml` `parseDocument()`** (a CST/AST that keeps comments and blank lines), never `YAML.parse()` → object → `stringify()`.
3. **Graph edits = targeted Document mutations, keyed by node `id`.** Adding a node appends one item to `nodes`. Connecting two nodes adds one item to `edges`. Renaming a field calls `setIn(['nodes', idx, 'title'], v)`. Then `doc.toString()`. Untouched lines come back byte-identical, comments included.
4. **Positions live in the `layout:` block**, which the engine ignores. Dragging a node rewrites only `layout.<id>`. Nodes with no position get placed by `elkjs`.
5. **YAML edits → debounced re-parse (300 ms).** If the YAML is invalid, the graph keeps the last valid state and shows an error banner plus Monaco markers. A typo never wipes the canvas.
6. **Stable identity:** nodes are matched by `id`, never by array index, so reordering YAML doesn't remount nodes.
7. **Round-trip test in CI:** a property test parses fixtures, applies random graph ops, serialises, and asserts that comments survive and that unrelated lines are unchanged.

Prior art: Kestra's flow editor (YAML + live topology view) uses the same "YAML canonical, graph derived" model. `enhanced-yaml` and `yaml-transmute` are fallback options if the Document API is ever too low-level.

### 6.3 Cost: three LLM modes, mapped to the models you already have

Installed Ollama models: `qwen3:8b` (5.2 GB), `qwen2.5:7b` (4.7 GB), `gemma3:4b` (3.3 GB, **the only vision-capable one**), `gemma3:1b` (815 MB), `nomic-embed-text` (274 MB). All of them run on the CPU.

**Biggest lever: read the text layer before using vision.** Our synthetic PDFs, and most carrier-issued PDFs, are *digital*: they have a text layer. The `doc_extractor` therefore:
1. Reads words and their coordinates with `pdfplumber`.
2. Sends **text only** to a text model and asks for JSON matching the schema (Ollama `format`, i.e. structured output).
3. Recovers each field's **bbox** by matching the extracted value back to the word coordinates. The evidence highlights still work, without any vision model.
4. Uses a vision model only when a page has no text layer (scans; one seed BoL is a deliberately blurry scan).

This is several times faster and more accurate than vision on CPU, and it's the right design in the cloud too, since text tokens cost far less than image tokens. See [ADR-016](06-adrs.md#adr-016-text-layer-first-extraction-vision-only-for-scans).

One env var, `LLM_MODE`, selects the LiteLLM config. Code only ever names aliases.

| Alias | `local` ($0, offline) | `cheap` (**dev default**, ≈ $0) | `demo` (best quality) |
|---|---|---|---|
| `nova-extract-text` | `qwen2.5:7b` (reliable JSON) | `qwen2.5:7b` | Claude Sonnet via OpenRouter |
| `nova-extract-vision` (scans only) | `gemma3:4b` | cheap OpenRouter vision model | Claude Sonnet |
| `nova-reason` | `qwen2.5:7b`; `qwen3:8b` for invoice/contract reasoning, with `/no_think` | same as local | Claude Sonnet |
| `nova-decide` | `gemma3:4b` | **Jev** (~$0.0002/call, ~1 s) | **Jev** |
| `nova-embed` | `nomic-embed-text` | `nomic-embed-text` | `nomic-embed-text` |

Why `cheap` is the dev default: it's local everywhere except Jev, which is effectively free and far faster than a CPU model for routing, and the rare scanned page. Expected spend is cents per month.

**Realistic CPU timings.** These are estimates for a 4-core i5-1145G7; benchmark them in M2 with `make bench-llm`.

| Task | Model | Estimate |
|---|---|---|
| Embed one SOP chunk | nomic-embed-text | < 0.5 s |
| `decide` (1K-token context, short JSON) | gemma3:4b | 5–15 s · Jev: ~1 s |
| Extract a 2-page digital BoL (~2K tokens in, ~400 out) | qwen2.5:7b | 60–120 s |
| Validator / matcher reasoning (~1.5K in, ~300 out) | qwen2.5:7b | 40–90 s |
| Same with qwen3:8b (`/no_think`) | qwen3:8b | 60–150 s; never leave thinking on (minutes) |
| Extract one *scanned* page with vision | gemma3:4b | 1–3 min, lower accuracy |
| First call after idle (model load from disk) | any 7–8B | +10–30 s |

Consequences:
- One BoL run in `local`/`cheap` takes **~2–4 minutes** end to end, versus ~15–30 s in `demo`. That's fine for development (Temporal doesn't care), but **record the live demo in `demo` mode**.
- Set activity timeouts per mode (`start_to_close` 5 min local vs 2 min demo), so slow CPU runs aren't killed and retried.
- Ollama settings: `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=30m`. On 4 cores, parallel requests only slow each other down, so the agents worker runs **1 concurrent LLM activity** in local modes.
- `gemma3:1b` is too weak for decisions with real context. Use it only for trivial classification, such as identifying the document type of an upload.

Cost guards, in every mode:
- **LiteLLM Redis response cache.** Re-running the same document costs $0 and returns instantly. That's a big deal when a local call takes 90 s.
- **Extraction dedupe by `sha256`** before any model call.
- **Deterministic checks before the LLM** (ISO 6346, LOCODE, sums), so only the residue goes to a model.
- **Hard caps:** a global LiteLLM `max_budget` ($10/month) plus per-tenant virtual keys. On exhaustion the run goes to a human task, never fails silently.

**Ollama in Docker, reusing your downloaded models.** The `ollama/ollama` container mounts the host model store (`%USERPROFILE%\.ollama` → `/root/.ollama`), so the ~14 GB isn't downloaded again. Note: the first load reads through the slower Windows→WSL file bridge. If that's painful, copy the models once into a Docker volume. If you stop the host Ollama service, there's no port clash on 11434.

Sources: [OpenRouter embedding models](https://openrouter.ai/collections/embedding-models), [Best embedding models 2026 (OpenRouter)](https://openrouter.ai/blog/insights/best-embedding-models-2026/), [eemeli/yaml](https://docsearch.algolia.com/mcp/docs/repo/eemeli/yaml), [parseDocument example](https://redirect.github.com/eemeli/yaml/discussions/538), [enhanced-yaml](https://npmjs.com/package/enhanced-yaml), [yaml-transmute](https://npmjs.org/package/yaml-transmute).
