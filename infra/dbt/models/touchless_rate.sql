-- Per tenant and day: finished runs that never needed a human task. Grain: tenant x day.
with human as (
    select distinct tenant_id, run_id
    from {{ source('nova', 'step_facts') }} final
    where node_type = 'human_task'
)
select
    r.tenant_id as tenant_id,
    toDate(r.started_at) as day,
    count() as runs,
    countIf(human.run_id = toUUID('00000000-0000-0000-0000-000000000000')) as touchless,
    round(touchless / runs, 3) as touchless_rate
from {{ source('nova', 'run_facts') }} as r final
left join human on human.tenant_id = r.tenant_id and human.run_id = r.id
where r.status in ('completed', 'rejected')
group by tenant_id, day
