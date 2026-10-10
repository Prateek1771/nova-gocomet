-- Per tenant and day: LLM spend per finished run (gateway cost booked on the run). Grain: tenant x day.
select
    tenant_id,
    toDate(started_at) as day,
    count() as runs,
    round(sum(cost_usd), 6) as spend_usd,
    round(sum(cost_usd) / count(), 6) as cost_per_run
from {{ source('nova', 'run_facts') }} final
where status in ('completed', 'rejected', 'waiting_human')
group by tenant_id, day
