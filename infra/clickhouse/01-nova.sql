-- Nova analytics (M6, LLD §5). Idempotent: the clickhouse-init one-shot runs it on every `up` (the data
-- volume is shared with Langfuse, so the image's first-boot init scripts never run here).
-- Raw events arrive from Kafka (Kafka engine + materialised view); Postgres facts arrive via Debezium CDC.
-- Tenant isolation: row policies on every fact table for the analyst user nova_reader, keyed on the
-- per-query setting SQL_tenant_id (set by nova_core.clickhouse). dbt builds metric views in nova_metrics.

CREATE DATABASE IF NOT EXISTS nova;
CREATE DATABASE IF NOT EXISTS nova_metrics;

-- shipment.events (simulator / carrier feeds) ------------------------------------------------------
CREATE TABLE IF NOT EXISTS nova.shipment_events
(
    event_id UUID,
    tenant_id UUID,
    shipment_id UUID,
    container_no String,
    event_type LowCardinality(String),
    location LowCardinality(String),
    port_role LowCardinality(String),
    vessel Nullable(String),
    voyage Nullable(String),
    booked_vessel Nullable(String),
    event_time DateTime64(3, 'UTC'),
    eta Nullable(DateTime64(3, 'UTC')),
    ingested_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree  -- a replayed event (same event_id) collapses
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_id, shipment_id, event_time, event_id);

CREATE TABLE IF NOT EXISTS nova.shipment_events_queue (raw String)
ENGINE = Kafka
SETTINGS kafka_broker_list = 'kafka:9092', kafka_topic_list = 'shipment.events',
         kafka_group_name = 'ch-shipment-events', kafka_format = 'JSONAsString',
         kafka_num_consumers = 1, kafka_skip_broken_messages = 100;

CREATE MATERIALIZED VIEW IF NOT EXISTS nova.shipment_events_mv TO nova.shipment_events AS
SELECT
    toUUID(JSONExtractString(raw, 'event_id')) AS event_id,
    toUUID(JSONExtractString(raw, 'tenant_id')) AS tenant_id,
    toUUID(JSONExtractString(raw, 'shipment_id')) AS shipment_id,
    JSONExtractString(raw, 'container_no') AS container_no,
    JSONExtractString(raw, 'event_type') AS event_type,
    JSONExtractString(raw, 'location') AS location,
    JSONExtractString(raw, 'port_role') AS port_role,
    JSONExtract(raw, 'vessel', 'Nullable(String)') AS vessel,
    JSONExtract(raw, 'voyage', 'Nullable(String)') AS voyage,
    JSONExtract(raw, 'booked_vessel', 'Nullable(String)') AS booked_vessel,
    parseDateTime64BestEffort(JSONExtractString(raw, 'event_time'), 3, 'UTC') AS event_time,
    parseDateTime64BestEffortOrNull(JSONExtractString(raw, 'eta'), 3, 'UTC') AS eta
FROM nova.shipment_events_queue
WHERE JSONHas(raw, 'event_id') AND JSONHas(raw, 'tenant_id');

-- CDC from Postgres (Debezium, unwrapped rows) -----------------------------------------------------
CREATE TABLE IF NOT EXISTS nova.step_facts
(
    id UUID,
    tenant_id UUID,
    run_id UUID,
    node_id String,
    node_type LowCardinality(String),
    status LowCardinality(String),
    output String,
    started_at Nullable(DateTime64(3, 'UTC')),
    ended_at Nullable(DateTime64(3, 'UTC')),
    _version UInt64
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (tenant_id, run_id, node_id, id);

CREATE TABLE IF NOT EXISTS nova.step_facts_queue (raw String)
ENGINE = Kafka
SETTINGS kafka_broker_list = 'kafka:9092', kafka_topic_list = 'cdc.nova.public.run_steps',
         kafka_group_name = 'ch-step-facts', kafka_format = 'JSONAsString', kafka_skip_broken_messages = 100;

CREATE MATERIALIZED VIEW IF NOT EXISTS nova.step_facts_mv TO nova.step_facts AS
SELECT
    toUUID(JSONExtractString(raw, 'id')) AS id,
    toUUID(JSONExtractString(raw, 'tenant_id')) AS tenant_id,
    toUUID(JSONExtractString(raw, 'run_id')) AS run_id,
    JSONExtractString(raw, 'node_id') AS node_id,
    JSONExtractString(raw, 'node_type') AS node_type,
    JSONExtractString(raw, 'status') AS status,
    JSONExtractString(raw, 'output') AS output,
    parseDateTime64BestEffortOrNull(JSONExtractString(raw, 'started_at'), 3, 'UTC') AS started_at,
    parseDateTime64BestEffortOrNull(JSONExtractString(raw, 'ended_at'), 3, 'UTC') AS ended_at,
    toUnixTimestamp64Micro(now64(6)) AS _version
FROM nova.step_facts_queue
WHERE JSONHas(raw, 'id');

CREATE TABLE IF NOT EXISTS nova.run_facts
(
    id UUID,
    tenant_id UUID,
    definition_id UUID,
    status LowCardinality(String),
    cost_usd Float64,
    started_at Nullable(DateTime64(3, 'UTC')),
    ended_at Nullable(DateTime64(3, 'UTC')),
    _version UInt64
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (tenant_id, id);

CREATE TABLE IF NOT EXISTS nova.run_facts_queue (raw String)
ENGINE = Kafka
SETTINGS kafka_broker_list = 'kafka:9092', kafka_topic_list = 'cdc.nova.public.workflow_runs',
         kafka_group_name = 'ch-run-facts', kafka_format = 'JSONAsString', kafka_skip_broken_messages = 100;

CREATE MATERIALIZED VIEW IF NOT EXISTS nova.run_facts_mv TO nova.run_facts AS
SELECT
    toUUID(JSONExtractString(raw, 'id')) AS id,
    toUUID(JSONExtractString(raw, 'tenant_id')) AS tenant_id,
    toUUID(JSONExtractString(raw, 'definition_id')) AS definition_id,
    JSONExtractString(raw, 'status') AS status,
    toFloat64OrZero(JSONExtractString(raw, 'cost_usd')) AS cost_usd,  -- Debezium decimal.handling.mode=string
    parseDateTime64BestEffortOrNull(JSONExtractString(raw, 'started_at'), 3, 'UTC') AS started_at,
    parseDateTime64BestEffortOrNull(JSONExtractString(raw, 'ended_at'), 3, 'UTC') AS ended_at,
    toUnixTimestamp64Micro(now64(6)) AS _version
FROM nova.run_facts_queue
WHERE JSONHas(raw, 'id');

-- access -------------------------------------------------------------------------------------------
CREATE USER IF NOT EXISTS nova_reader IDENTIFIED WITH sha256_password BY 'nova_reader' SETTINGS readonly = 2;
GRANT SELECT ON nova.shipment_events TO nova_reader;
GRANT SELECT ON nova.step_facts TO nova_reader;
GRANT SELECT ON nova.run_facts TO nova_reader;
GRANT SELECT ON nova_metrics.* TO nova_reader;

-- Once a table has a row policy, users without one see nothing: the reader sees its tenant only, the
-- admin user (dbt, ingestion, ops) keeps a pass-through policy. An unset SQL_tenant_id is an error.
CREATE ROW POLICY IF NOT EXISTS tenant_rp ON nova.shipment_events, nova.step_facts, nova.run_facts
    USING tenant_id = toUUID(getSetting('SQL_tenant_id')) TO nova_reader;
CREATE ROW POLICY IF NOT EXISTS admin_all ON nova.shipment_events, nova.step_facts, nova.run_facts
    USING 1 TO nova;
