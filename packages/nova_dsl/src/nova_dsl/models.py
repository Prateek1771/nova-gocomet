"""Workflow DSL (LLD §2): one Pydantic model per node type. Pure: no I/O."""

import re
from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema

_DUR = re.compile(r"^(\d+)(s|m|h|d)$")
_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def _duration(v: Any) -> Any:
    if isinstance(v, str) and (m := _DUR.match(v)):
        return timedelta(**{_UNIT[m[2]]: int(m[1])})
    return v  # timedelta, seconds, or ISO 8601 (round-trip of model_dump) → pydantic parses


Duration = Annotated[
    timedelta,
    BeforeValidator(_duration),
    WithJsonSchema({"type": "string", "pattern": r"^\d+(s|m|h|d)$", "examples": ["120s", "4h"]}),
]
NodeId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _Node(_Base):
    id: NodeId
    title: str | None = None
    timeout: Duration | None = None


class Retry(_Base):
    max_attempts: int = Field(3, ge=1, le=10)


class AgentNode(_Node):
    type: Literal["agent"]
    agent: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    retry: Retry | None = None


class Criteria(_Base):
    """What a yes and a no mean, in the domain's words (Jev `noul` criteria, LLD §3.4)."""

    true: str
    false: str


class Question(_Base):
    id: NodeId
    ask: str
    context: Any = None
    criteria: Criteria | None = None
    # P(yes) at or above this answers yes. Set it from the cost of each kind of mistake: a low
    # threshold sends more to humans, a high one lets more through.
    threshold: float = Field(0.5, ge=0, le=1)


class DecideNode(_Node):
    type: Literal["decide"]
    questions: list[Question] = Field(min_length=1)


class Case(_Base):
    when: str
    goto: NodeId


class RuleNode(_Node):
    type: Literal["rule"]
    cases: list[Case] = Field(min_length=1)
    default: NodeId


class Assignee(_Base):
    role: str | None = None
    user: str | None = None


class Escalate(_Base):
    after: Duration
    to: Assignee


class HumanTaskNode(_Node):
    type: Literal["human_task"]
    title: str
    assignee: Assignee
    app: str
    sla: Duration | None = None
    escalate: Escalate | None = None
    outputs: list[str] = Field(default_factory=lambda: ["approved", "rejected"], min_length=1)
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")


class ActionNode(_Node):
    type: Literal["action"]
    action: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    retry: Retry | None = None


class ParallelNode(_Node):
    type: Literal["parallel"]
    branches: list[list[NodeId]] = Field(min_length=2)
    join: Literal["all", "any"] = "all"


class WaitNode(_Node):
    type: Literal["wait"]
    duration: Duration | None = None
    event: str | None = None  # signal name; with duration it is a timeout


class SubflowNode(_Node):
    type: Literal["subflow"]
    workflow: str = Field(pattern=r"^[a-z][a-z0-9_]*(@\d+)?$")  # key or key@version
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")


class EndNode(_Node):
    type: Literal["end"]
    status: Literal["completed", "rejected", "cancelled"] = "completed"


Node = Annotated[
    AgentNode
    | DecideNode
    | RuleNode
    | HumanTaskNode
    | ActionNode
    | ParallelNode
    | WaitNode
    | SubflowNode
    | EndNode,
    Field(discriminator="type"),
]


class Edge(_Base):
    from_: NodeId = Field(alias="from")
    to: NodeId
    on: str | None = None  # human_task output


class Metadata(_Base):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    tenant: str | None = None
    title: str | None = None
    version: int | None = None  # set by publish


class Trigger(_Base):
    type: Literal["manual", "document_upload", "event", "schedule"] = "manual"
    doc_type: str | None = None
    event: str | None = None
    cron: str | None = None


class Workflow(_Base):
    apiVersion: Literal["nova/v1"] = "nova/v1"  # noqa: N815 (DSL field name)
    kind: Literal["Workflow"] = "Workflow"
    metadata: Metadata
    trigger: Trigger = Field(default_factory=Trigger)
    inputs: dict[str, Any] = Field(default_factory=dict)  # JSON Schema properties of the run input
    nodes: list[Node] = Field(min_length=1)
    edges: list[Edge] = Field(default_factory=list)
    layout: dict[str, Any] | None = None  # studio only; the engine ignores it

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)


class DocType(_Base):
    """definitions/doc_types/*.yaml (09 §3)."""

    key: str
    schema_: str = Field(alias="schema")
    checks: list[str] = Field(default_factory=list)
    default_workflow: str | None = None
    classifier_hints: list[str] = Field(default_factory=list)
    retention: str | None = None


class TenantConfig(BaseModel):
    """Versioned per-tenant knobs read by CEL as `tenant.*`. Unknown keys are allowed on purpose."""

    model_config = ConfigDict(extra="allow")
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    approval_limits: dict[str, float | None] = Field(default_factory=dict)  # None = unlimited
