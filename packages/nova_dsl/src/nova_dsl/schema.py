"""DSL JSON Schema: drives TS types (apps/web) and the studio's YAML autocomplete.

Run: uv run python -m nova_dsl.schema  (CI fails if the committed file drifts)
"""

import json
from pathlib import Path
from typing import Any

from nova_dsl.models import Workflow

OUT = Path(__file__).resolve().parents[2] / "schema" / "workflow.schema.json"


def json_schema() -> dict[str, Any]:
    return Workflow.model_json_schema(by_alias=True, mode="validation")


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
