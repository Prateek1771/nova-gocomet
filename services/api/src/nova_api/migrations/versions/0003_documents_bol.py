"""M2 (W1 BoL): document metadata, shipment upsert key, bookings master data (RLS like every tenant table).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"

_TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"

STATEMENTS = [
    "alter table documents add column filename text, add column mime text",
    "alter table extractions add column run_id uuid, add column created_at timestamptz not null default now()",
    "create index extractions_document on extractions (document_id, created_at desc)",
    # one shipment per BoL: the mock TMS action upserts on it
    "alter table shipments add column bol_number text, add column fields jsonb",
    "alter table shipments add constraint shipments_bol unique (tenant_id, bol_number)",
    """create table bookings (
      id uuid primary key default gen_random_uuid(),
      tenant_id uuid not null references tenants,
      booking_ref text not null,
      container_count int not null,
      pod text not null,
      consignee text not null,
      booking_date date not null,
      unique (tenant_id, booking_ref)
    )""",
    "alter table bookings enable row level security",
    "alter table bookings force row level security",
    f"create policy tenant_isolation on bookings using (tenant_id = {_TENANT}) with check (tenant_id = {_TENANT})",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("drop table bookings")
    op.execute(
        "alter table shipments drop constraint shipments_bol, drop column bol_number, drop column fields"
    )
    op.execute("drop index extractions_document")
    op.execute("alter table extractions drop column run_id, drop column created_at")
    op.execute("alter table documents drop column filename, drop column mime")
