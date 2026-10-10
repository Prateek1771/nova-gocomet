-- Per shipment and transhipment port: discharged -> loaded, or -> the tenant's latest event time (sim "now")
-- while the container still sits there. Grain: shipment x location.
with clock as (
    select tenant_id, max(event_time) as sim_now
    from {{ source('nova', 'shipment_events') }} final
    group by tenant_id
),
ts as (
    select
        tenant_id,
        shipment_id,
        location,
        any(container_no) as container_no,
        minIf(event_time, event_type = 'discharged') as discharged_at,
        maxIf(event_time, event_type = 'loaded') as loaded_at,
        countIf(event_type = 'discharged') as n_discharged,
        countIf(event_type = 'loaded') as n_loaded
    from {{ source('nova', 'shipment_events') }} final
    where port_role = 'ts'
    group by tenant_id, shipment_id, location
)
select
    ts.tenant_id as tenant_id,
    ts.shipment_id as shipment_id,
    ts.container_no as container_no,
    ts.location as location,
    ts.discharged_at as discharged_at,
    if(ts.n_loaded > 0, ts.loaded_at, null) as loaded_at,
    round(dateDiff('minute', ts.discharged_at, if(ts.n_loaded > 0, ts.loaded_at, clock.sim_now)) / 60.0, 1)
        as dwell_hours
from ts
inner join clock on clock.tenant_id = ts.tenant_id
where ts.n_discharged > 0
