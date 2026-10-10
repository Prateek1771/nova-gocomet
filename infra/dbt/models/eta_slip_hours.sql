-- Per shipment: the latest carrier ETA minus the first one seen (the plan). Grain: shipment.
select
    tenant_id,
    shipment_id,
    any(container_no) as container_no,
    argMin(eta, event_time) as eta_planned,
    argMax(eta, event_time) as eta_current,
    round(dateDiff('minute', argMin(eta, event_time), argMax(eta, event_time)) / 60.0, 1) as slip_hours,
    max(event_time) as as_of
from {{ source('nova', 'shipment_events') }} final
where eta is not null
group by tenant_id, shipment_id
