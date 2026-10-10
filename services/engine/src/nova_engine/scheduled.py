"""`ScheduledRun`: what a Temporal Schedule fires for a `trigger: schedule` workflow (nova_core.schedules,
ADR-036). It creates an ordinary run (latest published version + latest TenantConfig, subject = the
schedule) and starts it as an abandoned child NovaWorkflow, so a scheduled run looks like any other in the
run list, the projection and the audit log. It returns once the child has started: a run that waits (a human
task, needs_attention) must not hold the schedule, whose overlap policy skips while a firing is open.
Generic: tenant and key come from the schedule."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nova_core.schedules import SCHEDULED_RUN, schedule_id
    from nova_core.temporal import WORKFLOW
    from nova_engine import activities as acts
    from nova_engine.contracts import RunRequest, RunResult, ScheduledFire, SubrunRequest
    from nova_engine.handlers import FAST


@workflow.defn(name=SCHEDULED_RUN)
class ScheduledRun:
    @workflow.run
    async def run(self, fire: ScheduledFire) -> str:
        child = await workflow.execute_activity(
            acts.start_subrun,
            SubrunRequest(
                fire.tenant_id,
                fire.key,
                None,
                None,
                {},
                parent_run_id=schedule_id(fire.tenant_id, fire.key),
                subject_type="schedule",
            ),
            start_to_close_timeout=FAST,
        )
        await workflow.start_child_workflow(
            WORKFLOW,
            RunRequest(child.run_id, fire.tenant_id, child.definition_id, child.config_version, child.input),
            id=f"run-{child.run_id}",
            result_type=RunResult,
            parent_close_policy=workflow.ParentClosePolicy.ABANDON,
        )
        return child.run_id
