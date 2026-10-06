"""Write the OpenAPI spec the web client is generated from: uv run python -m nova_api.openapi"""

import json
from pathlib import Path

from nova_api.main import app

OUT = Path(__file__).resolve().parents[2] / "openapi.json"

if __name__ == "__main__":
    OUT.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
