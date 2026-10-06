import json

from nova_api.openapi import OUT, app


def test_committed_openapi_is_current() -> None:
    assert json.loads(OUT.read_text(encoding="utf-8")) == app.openapi(), "run: make gen"
