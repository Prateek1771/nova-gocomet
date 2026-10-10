"""M5 (W2 invoice match): purchase orders, rate contracts + clauses, container events (dwell facts until
ClickHouse takes them in M6, ADR-031), FX rates; invoices get a status and a duplicate key. RLS on every
new table, like 0003.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"

_TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
TABLES = ("purchase_orders", "po_lines", "rate_contracts", "contract_clauses", "container_events", "fx_rates")

STATEMENTS = [
    """create table purchase_orders (
      id uuid primary key default gen_random_uuid(),
      tenant_id uuid not null references tenants,
      po_number text not null,
      carrier_scac text not null,
      currency char(3) not null,
      bol_number text,
      unique (tenant_id, po_number)
    )""",
    """create table po_lines (
      id uuid primary key default gen_random_uuid(),
      tenant_id uuid not null references tenants,
      po_id uuid not null references purchase_orders on delete cascade,
      charge_code text not null,
      qty numeric(12,3) not null,
      unit_rate numeric(14,2) not null,
      unique (po_id, charge_code)
    )""",
    """create table rate_contracts (
      id uuid primary key default gen_random_uuid(),
      tenant_id uuid not null references tenants,
      contract_no text not null,
      carrier_scac text not null,
      currency char(3) not null,
      free_time_days int not null default 0,
      valid_from date not null,
      valid_to date not null,
      unique (tenant_id, contract_no)
    )""",
    """create table contract_clauses (
      id uuid primary key default gen_random_uuid(),
      tenant_id uuid not null references tenants,
      contract_id uuid not null references rate_contracts on delete cascade,
      clause_id text not null,
      charge_code text not null,
      title text not null,
      text text not null,
      rate numeric(14,2) not null,
      unit text not null,
      unique (tenant_id, clause_id)
    )""",
    """create table container_events (
      id uuid primary key default gen_random_uuid(),
      tenant_id uuid not null references tenants,
      container_no text not null,
      event_type text not null,
      location text,
      event_time timestamptz not null,
      unique (tenant_id, container_no, event_type, event_time)
    )""",
    "create index container_events_lookup on container_events (tenant_id, container_no, event_time)",
    """create table fx_rates (
      tenant_id uuid not null references tenants,
      currency char(3) not null,
      usd_rate numeric(12,6) not null,
      as_of date not null default current_date,
      primary key (tenant_id, currency)
    )""",
    """alter table invoices
         add column carrier_scac text, add column status text not null default 'received',
         add column run_id uuid, add column posted_at timestamptz,
         add constraint invoices_number unique (tenant_id, carrier_scac, invoice_no)""",
    *[stmt for t in TABLES for stmt in (
        f"alter table {t} enable row level security",
        f"alter table {t} force row level security",
        f"create policy tenant_isolation on {t} using (tenant_id = {_TENANT}) with check (tenant_id = {_TENANT})",
    )],
]  # fmt: skip


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute(
        "alter table invoices drop constraint invoices_number, drop column carrier_scac, drop column status,"
        " drop column run_id, drop column posted_at"
    )
    for t in reversed(TABLES):
        op.execute(f"drop table {t}")
