# Nova Prototype — Documentation

Nova is GoComet's governed AI platform for enterprise logistics. This repo builds a working prototype of it: a generic, YAML-driven workflow engine with a visual editor, a governed agent pipeline, a micro-app layer for human steps, and a tenant-isolated data layer, demonstrated end-to-end on three real logistics workflows.

**Status:** building milestone by milestone against [07](07-build-plan.md); M0 and M1 are done.

| # | Doc | What it answers |
|---|-----|-----------------|
| 00 | [Brainstorm & decisions](00-brainstorm.md) | What we agreed, why, what's still open |
| 01 | [PRD](01-prd.md) | Who it's for, what it must do, demo script, non-goals |
| 02 | [HLD](02-hld.md) | System context, components, data flows, deployment |
| 03 | [LLD](03-lld.md) | Modules, schemas, APIs, YAML DSL, agent pipeline, authz model, repo layout |
| 04 | [Workflow specs](04-workflow-specs.md) | The 3 demo workflows in full YAML + edge cases |
| 05 | [Scaling 1K → 1M users](05-scaling-1k-to-1m.md) | Capacity math and what changes at each tier |
| 06 | [ADRs](06-adrs.md) | Architecture decision records |
| 07 | [Implementation plan](07-build-plan.md) | Phased plan M0–M7: tasks, tests, exit criteria, DoD, traceability |
| 08 | [Enterprise execution plan](08-enterprise-execution-plan.md) | note.md plan → deliverables; security, AI governance, CI/CD, environments, SLOs, DR, client onboarding |
| 09 | [Standard domain model](09-standard-domain-model.md) | Generic engine vs per-process config, entities, run lifecycle, 5 process patterns |
| 10 | [Implementation checklist](10-implementation-checklist.md) | Full capability checklist by area (incl. CI), tagged to M0–M7 |
| — | [testing/m1-manual-test.md](testing/m1-manual-test.md) | Step-by-step manual test guide for M1 (DSL + engine) |
| — | [diagrams/](diagrams/) | Interactive architecture diagrams (archify) |

## One-paragraph architecture

Next.js (App Router) + React Flow front end with **Keycloak** login (OIDC BFF, org per tenant) → FastAPI (`nova-api`) → **Temporal** runs every workflow through one generic interpreter that reads the YAML definition → each step is an activity: a **LangGraph** agent, a **Jev** quick decision, a CEL rule, or a human task that waits on a Temporal signal → all LLM traffic goes out through **LiteLLM → OpenRouter** with per-tenant budgets and **Langfuse** tracing → **Postgres** is the system of record (RLS per tenant), **Debezium** streams its changes and shipment events through **Kafka** into **ClickHouse** for analytics, **Weaviate** holds SOP and contract embeddings, **OpenFGA** makes every RBAC/ReBAC decision (who can approve what, and up to how much).

## Diagrams (open in a browser)

| File | Type |
|---|---|
| [01-container-architecture.html](diagrams/01-container-architecture.html) | Container architecture |
| [02-bol-sequence.html](diagrams/02-bol-sequence.html) | BoL intake sequence |
| [03-exception-dataflow.html](diagrams/03-exception-dataflow.html) | Exception monitoring data flow |
| [04-scaling-cells-1m.html](diagrams/04-scaling-cells-1m.html) | 1M-user cell architecture |

Archify sources (`candidate.json`) live in `diagrams/.archify/`. Edit those and re-run `finalize` to regenerate.
