"""YAML text → models. Takes strings, never paths: reading files is the caller's I/O."""

import re
from typing import Any

import yaml
from pydantic import ValidationError

from nova_dsl.graph import Issue, validate
from nova_dsl.models import DocType, Workflow


class _Yaml12(yaml.SafeLoader):
    """YAML 1.2 booleans: only true/false. YAML 1.1 turns the edge key `on:` (and yes/no/off) into bools."""


_Yaml12.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def _load(text: str) -> Any:
    return yaml.load(text, Loader=_Yaml12)  # noqa: S506 (SafeLoader subclass)


def parse_workflow(text: str) -> tuple[Workflow | None, list[Issue]]:
    """Parse + validate. Returns (workflow, []) when publishable, else (maybe-workflow, issues)."""
    try:
        data = _load(text)
    except yaml.YAMLError as e:
        return None, [Issue("yaml", str(e))]
    if not isinstance(data, dict):
        return None, [Issue("yaml", "a workflow is a YAML mapping")]
    try:
        wf = Workflow.model_validate(data)
    except ValidationError as e:
        return None, [
            Issue("schema", f"{'.'.join(map(str, err['loc']))}: {err['msg']}") for err in e.errors()
        ]
    return wf, validate(wf)


def parse_doc_type(text: str) -> DocType:
    return DocType.model_validate(_load(text))
