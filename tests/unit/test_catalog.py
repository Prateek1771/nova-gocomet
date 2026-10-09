"""definitions/catalog.yaml is the contract the API validates against; it must match what actually runs."""

from pathlib import Path

from nova_agents.activities import AGENTS
from nova_api import definitions
from nova_engine.actions import ACTIONS

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_matches_the_registries() -> None:
    cat = definitions.catalog()
    assert {a["key"] for a in cat["agents"]} == set(AGENTS)
    assert {a["key"] for a in cat["actions"]} == set(ACTIONS)


def test_every_catalog_app_has_a_definition_or_is_builtin() -> None:
    for a in definitions.catalog()["apps"]:
        assert a.get("builtin") or (ROOT / "definitions/apps" / f"{a['key']}.json").is_file(), a["key"]


def test_every_shipped_workflow_only_references_catalog_entries() -> None:
    from nova_dsl import parse_workflow
    from nova_dsl.models import ActionNode, AgentNode, HumanTaskNode

    cat = {k: {e["key"] for e in v} for k, v in definitions.catalog().items()}
    for path in (ROOT / "definitions/workflows").rglob("*.yaml"):
        wf, issues = parse_workflow(path.read_text(encoding="utf-8"))
        assert wf and not issues, (path, issues)
        for n in wf.nodes:
            if isinstance(n, AgentNode):
                assert n.agent in cat["agents"], path
            if isinstance(n, ActionNode):
                assert n.action in cat["actions"], path
            if isinstance(n, HumanTaskNode):
                assert n.app in cat["apps"], path
