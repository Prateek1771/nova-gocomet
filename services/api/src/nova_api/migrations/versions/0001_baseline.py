"""Baseline schema (LLD §4 + Phase 0 deltas), RLS on every tenant table.

ponytail: master data (carriers, ports, bookings, POs, rate contracts) gets its own migrations in
M2/M5, when the generators fix their shape.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None

SCHEMA = """
create table tenants (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,               -- == Keycloak organization alias
  name text not null,
  keycloak_org_id text unique              -- bound on first login (ADR-018)
);

create table tenant_configs (
  tenant_id uuid not null references tenants,
  version int not null,
  config jsonb not null,                   -- thresholds, approval matrix, approval_limits, currencies
  published_by text not null,
  published_at timestamptz not null default now(),
  primary key (tenant_id, version)
);

create table users (
  id uuid primary key,                     -- Keycloak sub
  tenant_id uuid not null references tenants,
  email text,
  name text
);

create table workflow_definitions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  key text not null,
  version int not null,
  status text not null default 'draft' check (status in ('draft','published','archived')),
  yaml text not null,
  compiled jsonb,
  published_by text,
  published_at timestamptz,
  unique (tenant_id, key, version)
);

create table workflow_runs (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  definition_id uuid not null references workflow_definitions,
  config_version int not null,
  subject_type text,
  subject_id text,
  temporal_workflow_id text not null unique,
  status text not null default 'pending' check (status in
    ('pending','running','waiting_human','completed','rejected','cancelled','failed','needs_attention')),
  input jsonb not null default '{}',
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  cost_usd numeric(12,6) not null default 0,
  foreign key (tenant_id, config_version) references tenant_configs (tenant_id, version)
);

create table run_steps (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references workflow_runs,
  tenant_id uuid not null references tenants,
  node_id text not null,
  node_type text not null,
  status text not null check (status in ('running','completed','failed','skipped','waiting')),
  input jsonb, output jsonb, evidence jsonb,
  trace_id text,
  started_at timestamptz not null default now(),
  ended_at timestamptz
);

create table human_tasks (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  run_id uuid not null references workflow_runs,
  node_id text not null,
  title text not null,
  app_key text not null,
  assignee_role text,
  assignee_user uuid,
  status text not null default 'open' check (status in ('open','claimed','done','escalated')),
  due_at timestamptz,
  decision text,
  payload jsonb not null default '{}',
  completed_by uuid
);

create table documents (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  doc_type text not null,
  storage_key text not null,
  sha256 text not null,
  pages int,
  uploaded_by uuid,
  uploaded_at timestamptz not null default now(),
  unique (tenant_id, sha256)
);

create table extractions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  document_id uuid not null references documents,
  schema_key text not null,
  fields jsonb not null, confidence jsonb, evidence jsonb,
  model text
);

create table shipments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  container_no text not null,
  carrier_id uuid,
  pol text, pod text,
  etd timestamptz, eta_planned timestamptz, eta_current timestamptz,
  status text not null
);

create table invoices (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  document_id uuid references documents,
  carrier_id uuid,
  invoice_no text not null,
  currency char(3) not null,
  total numeric(14,2) not null,
  lines jsonb not null default '[]'
);

create table exceptions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  shipment_id uuid not null references shipments,
  type text not null,
  severity text not null check (severity in ('low','medium','high','critical')),
  detected_at timestamptz not null default now(),
  status text not null default 'open' check (status in ('open','acknowledged','resolved')),
  facts jsonb not null default '{}'
);
create unique index exceptions_one_open on exceptions (tenant_id, shipment_id, type) where status = 'open';

create table micro_apps (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  key text not null,
  version int not null,
  definition jsonb not null,
  unique (tenant_id, key, version)
);

create table audit_log (
  id bigint generated always as identity primary key,
  tenant_id uuid not null references tenants,
  actor_type text not null check (actor_type in ('user','agent','system')),
  actor_id text not null,
  action text not null,
  subject text not null,
  evidence jsonb,
  at timestamptz not null default now()
);

create table outbox (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  aggregate text not null,
  type text not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create table action_executions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants,
  idempotency_key text not null unique,
  run_id uuid not null references workflow_runs,
  node_id text not null,
  action text not null,
  status text not null default 'pending' check (status in ('pending','succeeded','failed')),
  request jsonb, response jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);
"""

TENANT_TABLES = [
    "tenant_configs",
    "users",
    "workflow_definitions",
    "workflow_runs",
    "run_steps",
    "human_tasks",
    "documents",
    "extractions",
    "shipments",
    "invoices",
    "exceptions",
    "micro_apps",
    "audit_log",
    "outbox",
    "action_executions",
]

# unset app.tenant_id -> NULL -> no rows (fail closed)
_TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    for stmt in SCHEMA.split(";"):  # asyncpg runs one statement per call
        if stmt.strip():
            op.execute(stmt)
    for t in TENANT_TABLES:
        op.execute(f"alter table {t} enable row level security")
        op.execute(f"alter table {t} force row level security")
        op.execute(
            f"create policy tenant_isolation on {t} using (tenant_id = {_TENANT}) "
            f"with check (tenant_id = {_TENANT})"
        )
    # tenants is the registry the API resolves organizations against; nova_app may only bind the org id
    op.execute("revoke insert, update, delete on tenants from nova_app")
    op.execute("grant update (keycloak_org_id) on tenants to nova_app")
    # audit log is append-only for the app
    op.execute("revoke update, delete on audit_log from nova_app")


def downgrade() -> None:
    for t in reversed(["tenants", *TENANT_TABLES]):
        op.execute(f"drop table if exists {t} cascade")
