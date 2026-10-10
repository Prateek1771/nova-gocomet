"""Clause retrieval (ADR-030): Weaviate with native multi-tenancy (one Weaviate tenant per Nova tenant) and
our own vectors from the `nova-embed` alias (nomic-embed-text via LiteLLM; ADR-015 prefixes). Plain
httpx against Weaviate's REST + GraphQL, so no SDK. Used by the API (indexing) and agents (search).

ponytail: one collection (contract clauses). SOPs and playbooks get their own collection when W3 needs
them; `index_clauses` is idempotent (ids derive from tenant + clause id), so re-running is a reindex.
"""

import json
import uuid
from typing import Any

import httpx

from nova_core.llm_keys import tenant_key
from nova_core.settings import get_settings

COLLECTION = "ContractClause"
EMBED_ALIAS = "nova-embed"  # rule 6: an alias, never a provider model
NS = uuid.UUID("6f1c2a52-6b9e-4f43-9a0e-2a6f8f0c4d11")
PROPS = ("clause_id", "contract_no", "carrier_scac", "charge_code", "title", "text", "rate", "unit")

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=30)
    return _http


def use(client: httpx.AsyncClient | None) -> None:
    """Tests swap in a client with a MockTransport (or reset to the default)."""
    global _http
    _http = client


async def embed(texts: list[str], kind: str, tenant_id: str | None = None) -> list[list[float]]:
    """kind: "document" when indexing, "query" when searching (nomic-embed-text task prefixes)."""
    s = get_settings()
    key = (tenant_key(tenant_id) if tenant_id else None) or s.llm_api_key
    r = await _client().post(
        f"{s.llm_base_url}/embeddings",
        json={"model": EMBED_ALIAS, "input": [f"search_{kind}: {t}" for t in texts]},
        headers={"Authorization": f"Bearer {key}"},
    )
    r.raise_for_status()
    return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]


async def ensure_collection(tenant_id: str) -> None:
    base = get_settings().weaviate_url
    c = _client()
    if (await c.get(f"{base}/v1/schema/{COLLECTION}")).status_code == 404:
        body = {
            "class": COLLECTION,
            "vectorizer": "none",  # we bring vectors (ADR-015)
            "multiTenancyConfig": {"enabled": True},
            "properties": [
                {"name": p, "dataType": ["number" if p == "rate" else "text"]}
                | ({"tokenization": "field"} if p in ("clause_id", "carrier_scac", "charge_code") else {})
                for p in PROPS
            ],
        }
        r = await c.post(f"{base}/v1/schema", json=body)
        if r.status_code not in (200, 422):  # 422: created concurrently
            r.raise_for_status()
    r = await c.post(f"{base}/v1/schema/{COLLECTION}/tenants", json=[{"name": tenant_id}])
    if r.status_code not in (200, 422):
        r.raise_for_status()


def object_id(tenant_id: str, clause_id: str) -> str:
    return str(uuid.uuid5(NS, f"{tenant_id}:{clause_id}"))


async def index_clauses(tenant_id: str, clauses: list[dict[str, Any]]) -> int:
    """Upsert `clauses` (dicts with PROPS) into the tenant's shard. Returns how many were written."""
    if not clauses:
        return 0
    await ensure_collection(tenant_id)
    vectors = await embed([f"{c['title']}. {c['text']}" for c in clauses], "document", tenant_id)
    objects = [
        {
            "class": COLLECTION,
            "id": object_id(tenant_id, c["clause_id"]),
            "tenant": tenant_id,
            "properties": {p: (float(c[p]) if p == "rate" else str(c[p])) for p in PROPS},
            "vector": v,
        }
        for c, v in zip(clauses, vectors, strict=True)
    ]
    r = await _client().post(f"{get_settings().weaviate_url}/v1/batch/objects", json={"objects": objects})
    r.raise_for_status()
    errors = [o["result"]["errors"] for o in r.json() if (o.get("result") or {}).get("errors")]
    if errors:
        raise RuntimeError(f"weaviate batch errors: {errors[:2]}")
    return len(objects)


async def search_clauses(
    tenant_id: str, query: str, carrier_scac: str | None = None, limit: int = 4
) -> list[dict[str, Any]]:
    """Hybrid (BM25 + vector) search in the tenant's shard only; another tenant's clauses can't match."""
    (vector,) = await embed([query], "query", tenant_id)
    where = (
        f', where: {{path: ["carrier_scac"], operator: Equal, valueText: {json.dumps(carrier_scac)}}}'
        if carrier_scac
        else ""
    )
    gql = (
        f"{{ Get {{ {COLLECTION}(tenant: {json.dumps(tenant_id)}, limit: {int(limit)}{where}, "
        f"hybrid: {{query: {json.dumps(query)}, vector: {json.dumps(vector)}, alpha: 0.5}}) "
        f"{{ {' '.join(PROPS)} _additional {{ score }} }} }} }}"
    )
    r = await _client().post(f"{get_settings().weaviate_url}/v1/graphql", json={"query": gql})
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"weaviate query failed: {body['errors'][0].get('message')}")
    hits: list[dict[str, Any]] = body["data"]["Get"][COLLECTION] or []
    return [
        {**{p: h.get(p) for p in PROPS}, "score": float((h.get("_additional") or {}).get("score") or 0)}
        for h in hits
    ]
