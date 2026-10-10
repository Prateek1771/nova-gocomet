-- Per invoice run: variance vs contract from the W2 matcher step (node `match`). Grain: run.
select
    tenant_id,
    run_id,
    JSONExtractFloat(output, 'variance_pct') as variance_pct,
    JSONExtractFloat(output, 'total_usd') as total_usd,
    ended_at
from {{ source('nova', 'step_facts') }} final
where node_id = 'match' and status = 'completed'
