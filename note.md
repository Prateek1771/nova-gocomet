## the plan
the plan is to build a working prototype out of the current project info listed below, this is the project which i want to build. follow standard folder structure, modular programming, plan is to build a working prototype with 2 working example workflows. 

use architect skill to genrate docs for the project, create a docs folder in that add the files, dont forget to create HLD, lld, after completing use tt-a1i/archify skill to generate the architecture also how would i scale from 1K to 1M users.

## The short version
We’re building Nova — a governed AI platform that rewires how enterprise logistics actually
works. Not dashboards. Not chatbots. Real agentic workflows that extract documents, validate
data, orchestrate multi-step approvals, and make decisions — all running on structured
knowledge, not vibes.
You’ll write production code, sit in front of enterprise clients, and ship AI-powered workflows
from day one. If you want a role where you build and think and ship — not just one of the three
— keep reading.

## What you’ll actually do
- Build real things. You’ll write production code across the full stack — React frontends,
Python/Node backends, ClickHouse queries, Kafka event pipelines, LangGraph agent
workflows, and YAML-driven process definitions. Not prototypes. Not demos. Stuff that runs in
production for real companies moving real cargo across real oceans.
- Talk to enterprise clients. You’ll sit across from logistics managers, freight forwarders, and
operations leads. You’ll listen to their messy, exception-filled processes — 6 approval levels, 4
document types, 3 systems that don’t talk to each other — and turn that chaos into structured
workflows they can actually use. This isn’t optional. It’s the job.
- Think AI-first. Every problem you encounter, your first question should be: “Can an agent
handle this?” Not “let me build a CRUD app.” You’ll design agents that extract fields from Bills of
Lading, validate invoices against purchase orders, auto-route approvals based on business
rules, and flag exceptions before humans even notice them. You’ll work with LLMs, RAG
pipelines, document parsers, and agent orchestration frameworks daily.
- Own problems end-to-end. You won’t hand off a spec and wait. You’ll identify the problem,
design the solution, build it, configure it for the client, deploy it, watch it in production, and fix
what breaks. The FDE model means you own outcomes, not tasks.

## What Nova actually is
Nova is GoComet’s platform transformation. Four pillars:
- *Workflow Orchestrator*: Configurable, visual, multi-step business processes. Clients define
their own approval chains, routing logic, and automation triggers through a drag-and-drop graph
editor. YAML under the hood. React Flow on the screen. Zero hardcoded business logic - the
engine is completely generic.
- *Agents Orchestrator*: AI agents that handle analytics, document extraction, validation,
monitoring, and recommendations. Built on LangGraph with a five-stage pipeline: scope
resolution → context compilation → schema routing → plan + execute → evidence delivery.
Every agent runs with governed business context, not ad-hoc prompting.
- *No-Code App Builder*: Custom micro-apps attached to workflow steps. When a workflow
node needs more than Approve/Reject — a rate comparison panel, a document review
interface, an exception dashboard — it’s built here.
- *Data Layer*: Unified ingestion from GoComet products, external sources, and client uploads.
ClickHouse as the analytical store. Everything tagged, mapped, and queryable. Tenant-isolated
by construction.

## The tech you’ll work with
- Frontend: React, React Flow (node-based graph editors) (https://reactflow.dev/)
- Backend: Python, FastAPI
- Data: ClickHouse, Kafka (event streaming), Debezium (CDC)
- AI/ML: LangGraph (agent orchestration), LLMs (prompt engineering, RAG, tool use),
DPT-2 (document extraction), PageIndex (long-document reasoning), Weaviate (vector
search)
- Permissions: OpenFGA (Zanzibar-based relationship access control)
- Observability: Langfuse (LLM tracing), OpenTelemetry
- Cost control: LiteLLM (gateway + budgets), Orkestra (model routing)
- Infrastructure: dbt (semantic layer / MetricFlow), Temporal (scheduling), DataHub /
OpenMetadata (catalog)
- DB: Postgres
- Docker