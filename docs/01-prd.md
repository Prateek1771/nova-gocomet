# 01 — Product Requirements (Prototype)

## 1. Problem

Enterprise logistics runs on exception-heavy processes: 6 approval levels, 4 document types, 3 systems that don't talk to each other. Today a human rekeys a Bill of Lading into the TMS, eyeballs an invoice against a PO and a rate contract, and finds out about a delayed container when the customer calls. Each client's process is different, so hardcoded software can't keep up, and "add a chatbot" doesn't move work forward.

Nova's answer: **configurable workflows whose steps are governed agents**, with humans in the loop only where the rules or the confidence say so.

## 2. Personas

| Persona | Goal | Touches |
|---|---|---|
| **Ops executive** (freight forwarder) | Clear my queue fast; trust what the agent pre-filled | Task Inbox, micro-apps |
| **Ops lead / approver** | Approve only what needs me, with evidence in front of me | Inbox, exception dashboard |
| **Finance controller** | No overpayments; disputes backed by evidence | Invoice workflow approvals |
| **Process admin / FDE** | Model the client's process without engineering tickets | Workflow Studio, App Builder, agent config |
| **Platform admin** | Tenants, roles, budgets, audit | Admin, Langfuse, LiteLLM UI |

## 3. Scope — functional requirements

### Pillar 1 · Workflow Orchestrator
- **FR-1.1** Workflows are defined in YAML (canonical) and edited visually in React Flow. The two stay in sync.
- **FR-1.2** Node types: `trigger`, `agent`, `decide` (Jev), `rule` (CEL), `human_task`, `action`, `parallel`, `wait`, `subflow`, `end`.
- **FR-1.3** Definitions are versioned. Publishing creates an immutable version, and running instances stay pinned to the version they started on.
- **FR-1.4** Triggers: manual, document upload, Kafka event, schedule (Temporal schedule).
- **FR-1.5** Human tasks support assignment by role/relationship (OpenFGA), SLA, escalation, and delegation.
- **FR-1.6** Validation before publish: JSON-Schema of the DSL, graph checks (reachability, no dangling edges, all branches terminate), CEL compile check, and referenced agents/apps exist.
- **FR-1.7** Live run view: the same graph with per-node status, inputs/outputs, timings, and agent trace links.

### Pillar 2 · Agents Orchestrator
- **FR-2.1** Every agent runs the 5-stage pipeline: scope resolution → context compilation → schema routing → plan + execute → evidence delivery.
- **FR-2.2** Agents in prototype: `doc_extractor`, `bol_validator`, `invoice_matcher`, `exception_analyst`, `action_recommender`.
- **FR-2.3** Every output is structured (Pydantic schema) and carries an `evidence[]` array and a `confidence` per field.
- **FR-2.4** All LLM calls go through LiteLLM, are traced in Langfuse, and are charged to the tenant's budget.
- **FR-2.5** `decide` nodes call Jev with N yes/no questions and branch on the answers. Fallback model on failure.

### Pillar 3 · No-Code App Builder (prototype depth)
- **FR-3.1** Micro-apps are JSON definitions (layout + bindings to run context) rendered by a component registry.
- **FR-3.2** Registry components: `DocumentViewer` (PDF + bbox highlights), `FieldForm` (editable extracted fields), `ComparisonTable` (invoice vs PO vs contract), `ExceptionPanel` (timeline + map + metrics), `DecisionBar` (approve/reject/dispute + reason).
- **FR-3.3** A `human_task` node references a micro-app. The app's output is validated against the node's `output_schema`.
- **FR-3.4** *Stretch:* a drag-and-drop builder for app layouts. The JSON editor and preview are in scope.

### Pillar 4 · Data Layer
- **FR-4.1** Ingestion: client uploads (MinIO), shipment event stream (Kafka), Postgres CDC (Debezium).
- **FR-4.2** ClickHouse holds events and facts. dbt models define governed metrics, and agents query metrics rather than raw tables.
- **FR-4.3** Tenant isolation by construction: Postgres RLS, ClickHouse row policies, Weaviate tenants, OpenFGA tenant scoping, MinIO prefixes, LiteLLM virtual keys per tenant.

### Cross-cutting
- **FR-X.1** Audit log of every decision (human or agent) with actor, evidence, and definition version.
- **FR-X.2** Two seeded tenants with different YAML for the same process.
- **FR-X.3** One-command bring-up: `make up` (core) / `make up-full`.

## 4. Non-functional requirements (prototype targets)

| NFR | Target |
|---|---|
| Doc extraction latency | p50 < 15 s per 2-page BoL in `demo` mode; < 2 min on CPU in `local`/`cheap` |
| `decide` latency | p50 < 2 s |
| UI interaction | < 100 ms for local state; run view updates via SSE < 1 s |
| Durability | Kill any worker mid-run → run resumes, no duplicate side effects |
| Cost | < $0.05 per BoL end-to-end; visible per run |
| Footprint | Full stack ≤ 11 GB RAM; + local LLMs ≤ 20 GB (32 GB machine, WSL cap 24 GB) |
| Security | No cross-tenant read possible even with a buggy query (RLS enforced) |

## 5. Non-goals

- Real GoComet/carrier API integrations (simulator instead)
- SSO / SAML (seeded users + JWT)
- Production Kubernetes manifests (described in the scaling doc only)
- DataHub/OpenMetadata deployment
- Mobile UI

## 6. Demo script (7 minutes)

1. **(0:00)** Workflow Studio: open `bol_intake` for *Acme Forwarding*. Toggle the YAML pane to show graph ↔ YAML.
2. **(0:45)** Upload a synthetic BoL with a planted container-number check-digit error. The run graph lights up live: extract → validate → `decide` (Jev: "is mismatch material?") → human task.
3. **(1:45)** Inbox: the DocumentViewer highlights the bad field's bbox, the validator's evidence says *"ISO 6346 check digit fails"*, and the user fixes and approves.
4. **(2:30)** Invoice: upload a carrier invoice 7% over the contract rate. `invoice_matcher` matches against the PO and contract (clause cited), CEL routes to L2 + finance, the ComparisonTable shows the variance, and finance disputes.
5. **(3:45)** Switch to *Bolt Logistics*: the same invoice routes differently (3 levels, $5K threshold). Same engine, different YAML.
6. **(4:30)** Exceptions: start the event simulator. ClickHouse detects an ETA slip of more than 24 h, the triage agent (Jev severity + analyst SQL evidence) and recommender (SOP from Weaviate) run, and the ops lead gets the ExceptionPanel.
7. **(5:45)** Kill the agents worker mid-run, restart it, and the run continues. Open Langfuse: cost per run, with Jev at fractions of a cent.
8. **(6:30)** Edit `bol_intake` live: add a `decide` node and publish v2. In-flight runs stay on v1.

## 7. Success criteria

- All 3 workflows run end-to-end from a clean `make up-full` on the 32 GB dev machine, in both `cheap` and `demo` modes.
- The two-tenant divergence is shown with zero code changes.
- Every planted error in the synthetic data is caught (validator recall = 100% on the seed set).
- Every agent decision is traceable: run → node → Langfuse trace → evidence.
