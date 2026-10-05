# 09 — Standard Domain Model

**Purpose:** this doc separates the parts of Nova that are written once (the engine) from the parts that are configured per process and per client (definitions). Every new logistics process a client brings should land as configuration. A new extension is needed only when the data has real structure the generic model can't hold.

> **Principle:** one generic run engine, configuration-driven process definitions, and specialised extensions only where the domain data is genuinely structured.

## 1. Source basis

Nova's prototype builds three processes ([04-workflow-specs](04-workflow-specs.md)): **W1** BoL intake, **W2** freight invoice ↔ PO match + approval, **W3** shipment exception monitoring. The model also has to hold the processes clients ask for next. The first discovery calls will bring up at least these:

| # | Process | Trigger | Built in prototype |
|---|---|---|---|
| 1 | Bill of Lading intake | document | ✅ W1 |
| 2 | Air Waybill intake | document | — |
| 3 | Packing list intake | document | — |
| 4 | Commercial invoice intake | document | — |
| 5 | Certificate of origin check | document | — |
| 6 | Arrival notice / delivery order | document | — |
| 7 | Freight invoice ↔ PO + contract match | document | ✅ W2 |
| 8 | Detention & demurrage invoice audit | document | — |
| 9 | Customs duty invoice check | document | — |
| 10 | ETA slip | event | ✅ W3 |
| 11 | Long dwell at port | event | ✅ W3 |
| 12 | Missed transhipment | event | ✅ W3 |
| 13 | Rollover | event | ✅ W3 |
| 14 | Reefer temperature excursion | event | — |
| 15 | Customs hold | event | — |
| 16 | Monthly carrier scorecard | schedule | — |
| 17 | Spot-rate / booking-amendment approval | manual | — |

## 2. Core design decision

Do **not** create a workflow class, a table or a page per process.

```text
                         WORKFLOW RUN  (one Temporal type: NovaWorkflow)
                              │
                ┌─────────────┴─────────────┐
                │                           │
       PROCESS DEFINITION                SUBJECT
     (what to do, versioned)     (document · shipment · invoice · exception)
                │
   ┌──────────┬─┴────────┬───────────┬──────────────┐
   │          │          │           │              │
Workflow   Doc type   Extraction   Approval      Micro-app
  YAML    + checks     schema      policy        (human step UI)
                                (tenant config)
```

The **run** is generic. The **process definition** decides the steps, the fields, the checks, who approves what, and what the human sees. That is "zero hardcoded business logic" made concrete: the engine never contains the word *invoice*.

## 3. Standard domain model

All tables carry `tenant_id uuid not null` with RLS ([LLD §4](03-lld.md#4-data-model-postgres)). Items marked **(new)** are deltas this doc adds to the LLD.

### Tenant and TenantConfig

```text
Tenant                    TenantConfig (new, versioned)
------                    ------------
id                        tenant_id
slug                      version
name                      config jsonb      -- approval matrix, thresholds, currencies, llm_egress
                          published_by
                          published_at
```

Today the LLD keeps `tenants.config` as one mutable jsonb. That means an audit can't show *which* approval matrix approved a payment. Versioned config fixes it: `load_definition` snapshots `(definition_version, config_version)` at run start, so an in-flight run never changes routing because someone edited a threshold.

### User and roles

```text
User
----
id, tenant_id, email, name
```

Identity and roles are **owned by Keycloak**, not Nova tables. The tenant is a Keycloak Organization (`tenants.keycloak_org_id`), and roles are `nova-api` client roles: `tenant_admin`, `process_designer`, `ops_exec`, `ops_lead`, `finance`, `controller`, `auditor`, `viewer`, plus `platform_admin`. `users` is a thin local mirror (sub, tenant, email, name), upserted on first login so audit rows and assignments can reference it.

What a role may do (the RBAC matrix) lives in the **OpenFGA model**. How much it may approve lives in **TenantConfig `approval_limits`** ([LLD §7](03-lld.md#7-authorization-openfga), [ADR-019](06-adrs.md#adr-019-rbac--roles-in-keycloak-every-decision-in-openfga-via-contextual-tuples)).

### DocType (new: definitions as code)

```yaml
# definitions/doc_types/bill_of_lading.yaml
key: bill_of_lading
schema: bol_v1                     # → definitions/schemas/bol_v1.json
checks: [CNTR_CHECK_DIGIT, LOCODE_UNKNOWN, BOOKING_MISMATCH, WEIGHT_SUM, DATE_ORDER, HS_FORMAT, SEMANTIC_GOODS]
default_workflow: bol_intake       # what a document_upload trigger starts
classifier_hints: ["BILL OF LADING", "B/L No", "Shipped on board"]
retention: 7y
```

Without this file, "add the Air Waybill" means touching the extractor, the validator and the trigger. With it, it means one YAML file, one schema file and one workflow file, plus any new check codes.

### WorkflowDefinition

```text
WorkflowDefinition
------------------
id, tenant_id, key, version
status        draft | published | archived
yaml text     -- canonical (ADR-012)
compiled jsonb
published_by, published_at
unique(tenant_id, key, version)
```

### WorkflowRun: the generic case

```text
WorkflowRun
-----------
id, tenant_id
definition_id
config_version       (new)  -- TenantConfig snapshot used by this run
subject_type         (new)  -- document | shipment | invoice | exception
subject_id           (new)
temporal_workflow_id
status                      -- see §7
input jsonb
started_at, ended_at, cost_usd
```

Keep it generic. Don't add `invoice_variance` or `bol_number` columns. Those belong in step outputs or in the subject's own table.

### RunStep, HumanTask, Document, Extraction

Same as [LLD §4](03-lld.md#4-data-model-postgres). `run_steps` is the workflow history (UniDocs' `WorkflowHistory`). `human_tasks.status` gets an explicit enum (§7).

### ActionExecution (new)

```text
ActionExecution
---------------
id, tenant_id
idempotency_key unique     -- run_id:node_id
run_id, node_id
action                     -- tms.upsert_shipment, erp.post_payable, carrier.dispute_email, notify_customer
status                     pending | succeeded | failed
request jsonb, response jsonb
created_at, completed_at
```

[HLD §6](02-hld.md#6-cross-cutting-concerns) promises an idempotency key on every side effect, but nothing stores it. Without this table, a worker crash between "ERP accepted" and "activity completed" pays an invoice twice.

### AuditLog and Outbox

Unchanged. `audit_log` is append-only and hash-chained ([08 §4.1](08-enterprise-execution-plan.md#41-threat-model-stride-focused-on-nova-specific-risks)). Each entry now carries `definition_version` **and** `config_version`.

## 4. Dynamic schemas

Every variable payload is JSON validated against a **versioned** schema, never free-form:

| Payload | Schema | Validated where |
|---|---|---|
| Run `input` | DSL `inputs:` block | `POST /runs` (API) |
| `extractions.fields` | `definitions/schemas/<key>_vN.json` (Pydantic → JSON Schema) | `deliver` stage of the agent pipeline |
| Agent output | `AgentSpec.output_schema` | `deliver` stage; one repair retry, then a human task |
| Human-task decision | micro-app `output_schema` | `POST /tasks/{id}/complete` (API) |
| Tenant config | `definitions/schemas/tenant_config_v1.json` | config publish |

The web app renders forms from the same schemas (`@rjsf/core`), and the server always re-validates. Bumping a schema creates a new version (`bol_v2`), and old extractions keep their `schema_key`.

## 5. Validation check registry

Checks play the role of UniDocs' "required documents". They're registered once and then referenced per doc type:

```python
@check("CNTR_CHECK_DIGIT", severity="high", fields=["container_numbers"])
def iso6346(fields, ctx) -> list[Issue]: ...
```

- Deterministic checks run first. Only the residue goes to the LLM (`SEMANTIC_GOODS`).
- Severity can be overridden in tenant config (Bolt downgrades `low` issues to auto-pass).
- Check codes double as eval labels: every planted error in the seed maps to one code ([ADR-013](06-adrs.md#adr-013-synthetic-data-with-planted-labelled-errors)).

## 6. Approval policy

Approval policy plays the role of UniDocs' FeeRule: configuration, never conditionals in code.

```yaml
# TenantConfig.config (Acme, version 4)
invoice:
  auto_variance_pct: 2
  auto_limit_usd: 2000
  l2_limit_usd: 10000
  l2_variance_pct: 5
approval_limits:          # per role, USD; copied onto approver tuples at task creation
  ops_lead: 10000
  finance: 50000
  controller: null        # unlimited
exceptions:
  eta_slip_hours: 24
llm_egress: cloud
```

It's enforced in two places, and both read data:
1. **Routing:** CEL in the workflow's `rule` node reads `tenant.invoice.*` ([04 W2](04-workflow-specs.md#w2--freight-invoice--po-match--multi-level-approval)).
2. **Authority:** OpenFGA `within_limit` at task completion, so the API refuses an L1 approval above the user's limit even if the UI is bypassed.

## 7. Standard run lifecycle

The engine owns these state machines. No other code writes `status`.

```text
WorkflowRun.status

pending ──► running ──► completed
               │  ▲
               ▼  │ signal
          waiting_human ──► (SLA timer) escalate ──► waiting_human
               │
running ──► rejected            (end node status: rejected)
running ──► cancelled           (POST /runs/{id}/cancel)
running ──► failed              (non-retryable engine error)
running ──► needs_attention     (retries exhausted, budget exhausted,
                                 invalid LLM output after repair)
                                 → creates a human task; resolve → running
```

```text
HumanTask.status

open ──► claimed ──► done
  │         │
  └─────────┴──► escalated ──► claimed ──► done
```

Both enums get a `CHECK` constraint. Every transition writes a `run_steps` row and an `audit_log` entry in the same transaction as the projection update.

## 8. Process patterns

The 17 processes in §1 reduce to five recipes made from the same ten node types.

### A. Document intake: W1

BoL, AWB, packing list, commercial invoice, certificate of origin, arrival notice.

```text
trigger(document_upload) → agent:doc_extractor → agent:<validator>
  → decide(material?) → rule(confidence / severity) → human_task(review) → action(push) → end
```

**New process = config:** DocType + schema + checks + workflow YAML. New code is needed only for a new check code.

### B. Match & approve: W2

Freight invoice, D&D invoice, customs duty invoice.

```text
trigger(document_upload) → agent:doc_extractor → rule(duplicate?)
  → agent:<matcher> → decide(justified?) → rule(approval matrix)
  → human_task L1 [→ human_task L2 → subflow L3] → action(pay | dispute) → end
```

**New process = config** plus reference data (POs, contracts). The approval chain depth comes from tenant config and `subflow`.

### C. Event-triggered exception: W3

ETA slip, dwell, missed transhipment, rollover, reefer excursion, customs hold.

```text
schedule → action:detect_exceptions (dbt metric) → exceptions row → CDC → trigger router
  → agent:exception_analyst → decide(actionable?) → agent:action_recommender
  → human_task(exception_panel) → action(notify) → end
```

**New exception type = a dbt metric + a threshold in tenant config + SOP docs.** The workflow YAML doesn't change.

### D. Scheduled analytics (not built)

Carrier scorecard, monthly cost review: `schedule → agent:exception_analyst (metric tools) → action(report)`. It needs no new node types.

### E. Request / approval without a document (not built)

Spot-rate approval, booking amendment: `trigger(manual, inputs schema) → rule → human_task chain → action`. This is pattern B without extraction.

## 9. Specialised extensions

These are the only places where the generic JSON payload isn't enough, because the data is queried relationally or analytically:

| Extension | Why it's structured | Where |
|---|---|---|
| `invoices` + `lines jsonb` ↔ `po_lines` | Line-level matching, variance per charge code | Postgres |
| `rate_contracts` + `contract_clauses` | Clause citation is evidence | Postgres + Weaviate |
| `shipments` + `shipment_events` | Time-series metrics (ETA slip, dwell) | Postgres (master) + ClickHouse (events) |
| `exceptions` | Stable ID, one open per (shipment, type), audit | Postgres |
| Carrier / port master data | ISO 6346, UN/LOCODE lookups | Postgres (seeded) |

Everything else goes in step outputs, extractions or config.

## 10. Standard relationship

```text
Tenant ═══ Keycloak Organization (users, roles, MFA, IdP)
 ├── TenantConfig (versions) ── approval_limits per role
 ├── Users (mirror of Keycloak sub) ── roles → OpenFGA contextual tuples
 ├── DocTypes ── ExtractionSchema · Checks · default Workflow
 ├── WorkflowDefinitions (versions) ── MicroApps
 │
 └── WorkflowRun ── pinned: definition_version + config_version
       ├── Subject (document | shipment | invoice | exception)
       ├── RunSteps ── evidence · trace_id (Langfuse)
       ├── HumanTasks ── decision · completed_by
       ├── ActionExecutions ── idempotency_key
       ├── Extractions
       └── AuditLog · Outbox → Kafka → ClickHouse
```

## 11. Generic vs specialised

| Component | Generic? | Notes |
|---|---:|---|
| Tenant / TenantConfig | Yes | Versioned config holds all thresholds |
| User / roles | Yes | Identity + roles in Keycloak; capability matrix in OpenFGA; limits in TenantConfig |
| WorkflowDefinition | Yes | YAML DSL, one interpreter |
| WorkflowRun / RunStep | Yes | One model for every process |
| HumanTask | Yes | Behaviour comes from the micro-app + output schema |
| DocType / ExtractionSchema | Yes | Configuration |
| Checks | Mostly | Registry; a new code needs a small function |
| Agents | Mostly | Shared 5-stage template; agents only plug in tools, context and schema |
| ActionExecution | Yes | All side effects |
| AuditLog / Outbox | Yes | Shared |
| Invoice lines / PO lines | Specialised | Line-level matching |
| Shipments / events | Specialised | Time series in ClickHouse |
| Contract clauses | Specialised | Retrieval + citation |
| Exceptions | Specialised | Dedupe and lifecycle |

## 12. Configuration layer

```text
PROCESS DEFINITION (per tenant, versioned, in git under definitions/)
     │
     ├── DocType           doc_types/*.yaml
     ├── Extraction schema schemas/*.json
     ├── Checks            referenced by code
     ├── Workflow          workflows/{tenant}/*.yaml
     ├── Approval policy   TenantConfig
     ├── Micro-app         apps/*.json
     └── Trigger           in the workflow YAML
```

## 13. Implementation principle

Build the **definition system first** and the workflow-specific screens last. That's why [07 M1](07-build-plan.md#m1--dsl--engine) is DSL + engine + config, and why W1 isn't started before it:

```text
DSL + validator → DocType / schema registry → Check registry → TenantConfig versions → Interpreter → (then) W1, W2, W3
```

The test of success: adding the Air Waybill (pattern A) or a reefer excursion (pattern C) needs **no change** in `services/engine` and no new table.

## 14. Important boundary

`bol_v1` / `invoice_v1` fields, the check list and the Acme/Bolt thresholds are **prototype values** built on synthetic data. A real client's schemas, checks and approval matrix are finalised from their own documents and process during FDE onboarding ([08 §10](08-enterprise-execution-plan.md#10-client-onboarding-playbook-fde)). This doc fixes the **model**, not an exhaustive field list.
