"""Debezium setup for M6 (ADR-035), idempotent: connect-init runs it on every `up --profile data`.

1. Postgres (as the superuser): a dedicated CDC role (REPLICATION, BYPASSRLS so the initial snapshot sees
   every tenant's rows), a publication for the captured tables, and cleanup of the M1 spike's slot.
2. Kafka Connect: PUT the `nova-cdc` connector config (create or update), remove the spike connector.

Topics: outbox rows -> nova.outbox.<aggregate> (EventRouter, key aggregate_id, tenant_id/type headers);
exceptions, run_steps, workflow_runs -> cdc.nova.public.<table> as plain rows with `__op`."""

import asyncio
import os

import asyncpg
import httpx

PG = os.environ.get("PG_ADMIN_URL", "postgresql://postgres:postgres@postgres:5432/nova")
CONNECT = os.environ.get("CONNECT_URL", "http://connect:8083")
TABLES = ["outbox", "exceptions", "run_steps", "workflow_runs"]

CONNECTOR = {
    "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
    "plugin.name": "pgoutput",
    "database.hostname": "postgres",
    "database.port": "5432",
    "database.user": "nova_cdc",
    "database.password": "nova_cdc",
    "database.dbname": "nova",
    "topic.prefix": "cdc.nova",
    "table.include.list": ",".join(f"public.{t}" for t in TABLES),
    "publication.name": "nova_cdc",
    "publication.autocreate.mode": "disabled",
    "slot.name": "nova_cdc",
    "decimal.handling.mode": "string",
    "predicates": "isOutbox",
    "predicates.isOutbox.type": "org.apache.kafka.connect.transforms.predicates.TopicNameMatches",
    "predicates.isOutbox.pattern": "cdc\\.nova\\.public\\.outbox",
    "transforms": "outbox,unwrap",
    "transforms.outbox.type": "io.debezium.transforms.outbox.EventRouter",
    "transforms.outbox.predicate": "isOutbox",
    "transforms.outbox.table.field.event.id": "id",
    "transforms.outbox.table.field.event.key": "aggregate_id",
    "transforms.outbox.table.field.event.payload": "payload",
    "transforms.outbox.table.fields.additional.placement": "tenant_id:header,type:header",
    "transforms.outbox.route.by.field": "aggregate",
    "transforms.outbox.route.topic.replacement": "nova.outbox.${routedByValue}",
    "transforms.outbox.table.expand.json.payload": "true",
    "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
    "transforms.unwrap.predicate": "isOutbox",
    "transforms.unwrap.negate": "true",
    "transforms.unwrap.drop.tombstones": "true",
    "transforms.unwrap.add.fields": "op",
    "key.converter": "org.apache.kafka.connect.json.JsonConverter",
    "key.converter.schemas.enable": "false",
    "value.converter": "org.apache.kafka.connect.json.JsonConverter",
    "value.converter.schemas.enable": "false",
}


async def postgres() -> None:
    c = await asyncpg.connect(PG)
    try:
        if not await c.fetchval("select 1 from pg_roles where rolname = 'nova_cdc'"):
            await c.execute("create role nova_cdc login replication bypassrls password 'nova_cdc'")
        tables = ", ".join(TABLES)
        await c.execute(f"grant select on {tables} to nova_cdc")
        if await c.fetchval("select 1 from pg_publication where pubname = 'nova_cdc'"):
            await c.execute(f"alter publication nova_cdc set table {tables}")
        else:
            await c.execute(f"create publication nova_cdc for table {tables}")
        # the M1 spike's slot would pin WAL forever once its connector is gone
        await c.execute(
            "select pg_drop_replication_slot(slot_name) from pg_replication_slots "
            "where slot_name = 'nova_outbox' and not active"
        )
    finally:
        await c.close()


async def connect() -> None:
    async with httpx.AsyncClient(base_url=CONNECT, timeout=30) as h:
        for _ in range(60):
            try:
                if (await h.get("/connectors")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(3)
        else:
            raise SystemExit("Kafka Connect never came up")
        await h.delete("/connectors/nova-outbox")  # the M1 spike connector, if it was registered
        r = await h.put("/connectors/nova-cdc/config", json=CONNECTOR)
        r.raise_for_status()
        print("nova-cdc connector", "created" if r.status_code == 201 else "updated", flush=True)


async def main() -> None:
    await postgres()
    await connect()


if __name__ == "__main__":
    asyncio.run(main())
