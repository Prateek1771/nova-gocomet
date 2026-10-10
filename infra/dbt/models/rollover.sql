-- Per shipment: how many times the booking moved to another vessel, and when it first did. Grain: shipment.
with first_booking as (
    select tenant_id, shipment_id, argMin(booked_vessel, event_time) as booked_first
    from {{ source('nova', 'shipment_events') }} final
    where booked_vessel is not null
    group by tenant_id, shipment_id
)
select
    e.tenant_id as tenant_id,
    e.shipment_id as shipment_id,
    any(e.container_no) as container_no,
    any(f.booked_first) as booked_first,
    argMax(e.booked_vessel, e.event_time) as booked_now,
    toUInt32(uniqExact(e.booked_vessel) - 1) as rollovers,
    minIf(e.event_time, e.booked_vessel != f.booked_first) as rolled_at
from {{ source('nova', 'shipment_events') }} as e final
inner join first_booking as f on f.tenant_id = e.tenant_id and f.shipment_id = e.shipment_id
where e.booked_vessel is not null
group by e.tenant_id, e.shipment_id
