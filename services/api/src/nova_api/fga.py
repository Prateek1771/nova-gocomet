"""OpenFGA over plain HTTP (httpx is already here; no SDK). OpenFGA holds only the model: every fact a
check needs (roles, task→run→workflow→tenant, assignee, approver limits) is sent as contextual tuples
built from the tenant-scoped row (ADR-019, ADR-026). So the store keeps nothing, and the API (re)creates
it on first use.

ponytail: in-memory store, re-created after an OpenFGA restart; move to a Postgres datastore only if
tuples ever get stored.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from nova_core.settings import get_settings

MODEL = Path(__file__).resolve().parents[4] / "infra/openfga/model.json"
BATCH = 50  # OpenFGA's default max checks per batch-check


@dataclass(frozen=True)
class Tuple:
    user: str
    relation: str
    object: str
    condition: dict[str, Any] | None = None

    def key(self) -> dict[str, Any]:
        k: dict[str, Any] = {"user": self.user, "relation": self.relation, "object": self.object}
        if self.condition:
            k["condition"] = self.condition
        return k


@dataclass(frozen=True)
class Check:
    user: str
    relation: str
    object: str
    tuples: list[Tuple] = field(default_factory=list)
    context: dict[str, Any] | None = None

    def body(self) -> dict[str, Any]:
        b: dict[str, Any] = {
            "tuple_key": {"user": self.user, "relation": self.relation, "object": self.object},
            "contextual_tuples": {"tuple_keys": [t.key() for t in self.tuples]},
        }
        if self.context:
            b["context"] = self.context
        return b


class FGAError(RuntimeError):
    pass


_http: httpx.AsyncClient | None = None
_store: tuple[str, str] | None = None  # (store id, model id)


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=get_settings().openfga_url, timeout=5)
    return _http


def model_json() -> dict[str, Any]:
    path = Path(get_settings().openfga_model_path or MODEL)
    model: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return model


async def _ensure() -> tuple[str, str]:
    global _store
    if _store is None:
        c = _client()
        stores = (await c.get("/stores")).raise_for_status().json()["stores"]
        sid = next((s["id"] for s in stores if s["name"] == "nova"), None)
        if sid is None:
            sid = (await c.post("/stores", json={"name": "nova"})).raise_for_status().json()["id"]
        r = await c.post(f"/stores/{sid}/authorization-models", json=model_json())
        _store = (sid, r.raise_for_status().json()["authorization_model_id"])
    return _store


async def _post(op: str, body: dict[str, Any]) -> dict[str, Any]:
    global _store
    for attempt in (0, 1):
        try:
            sid, mid = await _ensure()
            r = await _client().post(f"/stores/{sid}/{op}", json={**body, "authorization_model_id": mid})
        except httpx.HTTPError as e:
            raise FGAError(f"openfga unreachable: {e}") from e
        if r.status_code == 404 and attempt == 0:  # OpenFGA restarted, store is gone: re-create once
            _store = None
            continue
        if r.status_code >= 400:
            raise FGAError(f"openfga {op}: HTTP {r.status_code} {r.text[:300]}")
        out: dict[str, Any] = r.json()
        return out
    raise FGAError("openfga store missing after re-create")  # pragma: no cover


async def check(c: Check) -> bool:
    return bool((await _post("check", c.body()))["allowed"])


async def batch_check(checks: list[Check]) -> list[bool]:
    out: list[bool] = []
    for i in range(0, len(checks), BATCH):
        chunk = checks[i : i + BATCH]
        body = {"checks": [{**c.body(), "correlation_id": str(n)} for n, c in enumerate(chunk)]}
        res = (await _post("batch-check", body))["result"]
        out += [bool(res[str(n)].get("allowed")) for n in range(len(chunk))]
    return out
