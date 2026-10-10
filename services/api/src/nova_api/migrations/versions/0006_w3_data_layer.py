"""M6 (data layer + W3): outbox rows get an aggregate id (the Kafka key, LLD §6); shipments get the route
and vessel the simulator plays; the notifier's mock sink (`notifications`, RLS) and the trigger router's
consumer-lag readings (`kafka_lag`, ops data, no tenant). The CDC role and publication need a superuser,
so infra/kafka-connect/register.py creates them (idempotent), not this migration.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"

_TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"

STATEMENTS = [
    "alter table outbox add column aggregate_id text",
    # M5 wrote aggregate = 'invoice:<no>'; ':' is not legal in a Kafka topic (nova.outbox.<aggregate>).
    # Forced RLS hides every row from the owner too, so lift it for this one cross-tenant fix-up.
    "alter table outbox no force row level security",
    """update outbox set aggregate_id = split_part(aggregate, ':', 2), aggregate = split_part(aggregate, ':', 1)
         where aggregate like '%:%'""",
    "alter table outbox force row level security",
    "alter table outbox add constraint outbox_aggregate_topic_safe check (aggregate ~ '^[a-z0-9_.-]+$')",
    """alter table shipments
         add column lane text, add column vessel text, add column voyage text,
         add column ts_port text""",
    """create table notifications (
      id uuid primary key default gen_random_uuid(),
      tenant_id uuid not null references tenants,
      outbox_id uuid not null,
      topic text not null,
      type text not null,
      recipient text not null,
      subject text not null,
      body text not null,
      refs jsonb not null default '{}',
      received_at timestamptz not null default now(),
      unique (tenant_id, outbox_id)
    )""",
    "create index notifications_recent on notifications (tenant_id, received_at desc)",
    """create table kafka_lag (
      group_id text not null,
      topic text not null,
      lag bigint not null,
      alerting boolean not null default false,
      measured_at timestamptz not null default now(),
      primary key (group_id, topic)
    )""",
    "alter table notifications enable row level security",
    "alter table notifications force row level security",
    f"""create policy tenant_isolation on notifications
          using (tenant_id = {_TENANT}) with check (tenant_id = {_TENANT})""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("drop table kafka_lag")
    op.execute("drop table notifications")
    op.execute(
        "alter table shipments drop column lane, drop column vessel, drop column voyage, drop column ts_port"
    )
    op.execute("alter table outbox drop column aggregate_id")
