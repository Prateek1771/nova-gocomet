-- Every milestone of a shipment in sim-time order (the ExceptionPanel timeline). Grain: event.
select
    tenant_id,
    shipment_id,
    event_id,
    container_no,
    event_type,
    location,
    port_role,
    vessel,
    booked_vessel,
    event_time,
    eta
from {{ source('nova', 'shipment_events') }} final
