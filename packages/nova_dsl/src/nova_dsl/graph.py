"""Graph validation (FR-1.6) and routing. Shared by the API (validate/publish) and the engine (walk)."""

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from nova_dsl import cel
from nova_dsl.models import (
    DecideNode,
    EndNode,
    HumanTaskNode,
    Node,
    ParallelNode,
    RuleNode,
    SubflowNode,
    WaitNode,
    Workflow,
)

_NODE_REF = re.compile(r"\bnodes\.([a-z][a-z0-9_]*)")
_NOT_IN_BRANCH = (RuleNode, EndNode, ParallelNode)


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    node_id: str | None = None

    def dict(self) -> dict[str, Any]:
        return asdict(self)


def branch_members(wf: Workflow) -> set[str]:
    return {i for n in wf.nodes if isinstance(n, ParallelNode) for b in n.branches for i in b}


def successors(wf: Workflow, node: Node) -> list[str]:
    if isinstance(node, RuleNode):
        return [c.goto for c in node.cases] + [node.default]
    return [e.to for e in wf.edges if e.from_ == node.id]


def entry(wf: Workflow) -> Node:
    """The one top-level node nothing points at."""
    targets = {t for n in wf.nodes for t in successors(wf, n)} | branch_members(wf)
    return next(n for n in wf.nodes if n.id not in targets)


def next_node(wf: Workflow, node: Node, out: Any) -> Node:
    """Rule → its chosen goto; human_task → the edge whose `on` matches the decision; else the edge."""
    if isinstance(node, RuleNode):
        return wf.node(out["goto"])
    decision = out.get("decision") if isinstance(out, dict) else None
    edges = [e for e in wf.edges if e.from_ == node.id]
    edge = next((e for e in edges if e.on is not None and e.on == decision), None) or next(
        e for e in edges if e.on is None
    )
    return wf.node(edge.to)


def expressions(node: Node) -> list[str]:
    """All CEL in a node: rule conditions plus templates anywhere in its config."""
    raw = node.model_dump(by_alias=True, exclude={"id", "type"})
    found = cel.templates(raw)
    if isinstance(node, RuleNode):
        found += [c.when for c in node.cases]
    return found


def validate(wf: Workflow) -> list[Issue]:
    issues: list[Issue] = []
    ids = [n.id for n in wf.nodes]
    for dup, count in Counter(ids).items():
        if count > 1:
            issues.append(Issue("duplicate_id", f"node id {dup!r} is used {count} times", dup))
    if issues:
        return issues  # everything below indexes by id
    known = set(ids)
    members = branch_members(wf)

    for e in wf.edges:
        for end in (e.from_, e.to):
            if end not in known:
                issues.append(Issue("dangling_edge", f"edge {e.from_}→{e.to} references unknown {end!r}"))
    for n in wf.nodes:
        out_edges = [e for e in wf.edges if e.from_ == n.id]
        if isinstance(n, RuleNode):
            for t in successors(wf, n):
                if t not in known:
                    issues.append(Issue("unknown_goto", f"goto {t!r} is not a node", n.id))
            if out_edges:
                issues.append(Issue("rule_edges", "rule nodes route via cases/default, not edges", n.id))
        if isinstance(n, EndNode) and out_edges:
            issues.append(Issue("edge_from_end", "end nodes have no outgoing edges", n.id))
        if n.id in members and out_edges:
            issues.append(Issue("branch_edges", "parallel branch nodes are sequenced by the branch", n.id))
        for e in out_edges:
            if e.on is not None and not (isinstance(n, HumanTaskNode) and e.on in n.outputs):
                issues.append(Issue("unknown_output", f"edge on {e.on!r} is not an output of {n.id}", n.id))
        if isinstance(n, HumanTaskNode) and not any(e.on is None for e in out_edges):
            for o in n.outputs:
                if not any(e.on == o for e in out_edges):
                    issues.append(Issue("uncovered_output", f"output {o!r} has no edge", n.id))
        if (
            not isinstance(n, RuleNode | EndNode | HumanTaskNode)
            and n.id not in members
            and sum(e.on is None for e in out_edges) != 1
        ):
            issues.append(Issue("next_edge", "needs exactly one outgoing edge", n.id))
        if isinstance(n, ParallelNode):
            for b in n.branches:
                for i in b:
                    if i not in known:
                        issues.append(Issue("bad_branch", f"branch node {i!r} is not a node", n.id))
                    elif isinstance(wf.node(i), _NOT_IN_BRANCH):
                        issues.append(Issue("bad_branch", f"{i!r} can't run inside a branch", n.id))
        if isinstance(n, SubflowNode) and n.workflow.split("@")[0] == wf.metadata.key:
            issues.append(Issue("recursive_subflow", "a workflow can't call itself", n.id))
        if isinstance(n, DecideNode) and len({q.id for q in n.questions}) != len(n.questions):
            issues.append(Issue("duplicate_question", "question ids must be unique", n.id))
        if isinstance(n, WaitNode) and n.duration is None and n.event is None:
            issues.append(Issue("wait_needs", "wait needs a duration or an event", n.id))
        for expr in expressions(n):
            try:
                cel.check(expr)
            except cel.CelError as e:
                issues.append(Issue("cel_error", str(e), n.id))
            for ref in _NODE_REF.findall(expr):
                if ref not in known:
                    issues.append(Issue("unknown_ref", f"{expr!r} references unknown node {ref!r}", n.id))
    if issues:
        return issues

    top = [n for n in wf.nodes if n.id not in members]
    targets = {t for n in wf.nodes for t in successors(wf, n)}
    roots = [n.id for n in top if n.id not in targets]
    if len(roots) != 1:
        issues.append(Issue("entry", f"need exactly one entry node, found {roots or 'none (cycle)'}"))
        return issues

    reach = _closure(roots, lambda i: successors(wf, wf.node(i)))
    for n in top:
        if n.id not in reach:
            issues.append(Issue("unreachable", "not reachable from the entry", n.id))
    preds: dict[str, list[str]] = {i: [] for i in known}
    for n in top:
        for t in successors(wf, n):
            preds[t].append(n.id)
    ends = [n.id for n in top if isinstance(n, EndNode)]
    finishes = _closure(ends, lambda i: preds[i])
    for n in top:
        if n.id in reach and n.id not in finishes:
            issues.append(Issue("no_end", "no path from here reaches an end node", n.id))
    return issues


def _closure(start: list[str], step: Any) -> set[str]:
    seen, todo = set(start), list(start)
    while todo:
        for nxt in step(todo.pop()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen
