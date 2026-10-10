-- Per shipment and transhipment port: the connecting vessel departed but the container was never loaded on
-- it (missed = 1). Grain: shipment x location.
select
    tenant_id,
    shipment_id,
    location,
    any(container_no) as container_no,
    anyIf(vessel, event_type = 'departed') as departed_vessel,
    maxIf(event_time, event_type = 'departed') as departed_at,
    toUInt8(countIf(event_type = 'departed') > 0 and countIf(event_type = 'loaded') = 0) as missed
from {{ source('nova', 'shipment_events') }} final
where port_role = 'ts'
group by tenant_id, shipment_id, location
