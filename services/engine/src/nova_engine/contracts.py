"""Payloads crossing the workflow ↔ activity boundary (Temporal's JSON converter handles dataclasses)."""

from dataclasses import dataclass, field
from typing import Any

from nova_core.temporal import AgentRequest

__all__ = ["AgentRequest"]  # re-exported: the contract moved to nova_core (shared with agents)


@dataclass
class RunRequest:
    run_id: str
    tenant_id: str
    definition_id: str
    config_version: int
    input: dict[str, Any] = field(default_factory=dict)
    # carried over by continue_as_new
    nodes: dict[str, Any] = field(default_factory=dict)
    resume_at: str | None = None
    visits: dict[str, int] = field(default_factory=dict)
    max_events: int = 1000


@dataclass
class RunResult:
    status: str
    nodes: dict[str, Any]


@dataclass
class LoadRequest:
    tenant_id: str
    definition_id: str
    config_version: int


@dataclass
class Loaded:
    definition: dict[str, Any]  # Workflow.model_dump(mode="json", by_alias=True)
    config: dict[str, Any]


@dataclass
class StepEvent:
    tenant_id: str
    run_id: str
    step_id: str | None  # None: run-level transition only
    node_id: str
    node_type: str
    status: str  # running | completed | failed | skipped | waiting
    output: Any = None
    run_status: str | None = None  # also move workflow_runs.status (09 §7)


@dataclass
class TaskWrite:
    """Insert (when `title` is set) or transition a human task."""

    tenant_id: str
    task_id: str
    status: str
    run_id: str = ""
    node_id: str = ""
    title: str | None = None
    app_key: str = ""
    assignee_role: str | None = None
    assignee_user: str | None = None
    due_at: str | None = None
    decision: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = "system"


@dataclass
class ActionRequest:
    tenant_id: str
    run_id: str
    node_id: str
    idempotency_key: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubrunRequest:
    tenant_id: str
    key: str
    version: int | None
    config_version: int  # a child runs on its parent's pinned config
    input: dict[str, Any]
    parent_run_id: str


@dataclass
class Subrun:
    run_id: str
    definition_id: str
    input: dict[str, Any]
