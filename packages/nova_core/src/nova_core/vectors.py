"""Retrieval (ADR-030): Weaviate with native multi-tenancy (one Weaviate tenant per Nova tenant) and our own
vectors from the `nova-embed` alias (nomic-embed-text via LiteLLM; ADR-015 prefixes). Plain httpx against
Weaviate's REST + GraphQL, so no SDK. Used by the API (indexing) and agents (search).

Two collections: contract clauses (W2) and SOP chunks (W3, ADR-036). Indexing is idempotent (ids derive
from tenant + the item's id), so re-running is a reindex.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from nova_core.llm_keys import tenant_key
from nova_core.settings import get_settings


@dataclass(frozen=True)
class Collection:
    name: str
    props: tuple[str, ...]
    keys: tuple[str, ...]  # exact-match filters (field tokenization)
    id_prop: str
    text: tuple[str, ...]  # what gets embedded, joined with ". "
    numbers: tuple[str, ...] = ()


CLAUSES = Collection(
    "ContractClause",
    ("clause_id", "contract_no", "carrier_scac", "charge_code", "title", "text", "rate", "unit"),
    ("clause_id", "carrier_scac", "charge_code"),
    "clause_id",
    ("title", "text"),
    ("rate",),
)
SOPS = Collection(
    "Sop",
    ("chunk_id", "sop_id", "exception_type", "carrier", "title", "section", "text"),
    ("chunk_id", "sop_id", "exception_type", "carrier"),
    "chunk_id",
    ("title", "section", "text"),
)
COLLECTION, PROPS = CLAUSES.name, CLAUSES.props  # W2 names, kept for callers
EMBED_ALIAS = "nova-embed"  # rule 6: an alias, never a provider model
NS = uuid.UUID("6f1c2a52-6b9e-4f43-9a0e-2a6f8f0c4d11")

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


async def ensure_collection(tenant_id: str, col: Collection = CLAUSES) -> None:
    base = get_settings().weaviate_url
    c = _client()
    if (await c.get(f"{base}/v1/schema/{col.name}")).status_code == 404:
        body = {
            "class": col.name,
            "vectorizer": "none",  # we bring vectors (ADR-015)
            "multiTenancyConfig": {"enabled": True},
            "properties": [
                {"name": p, "dataType": ["number" if p in col.numbers else "text"]}
                | ({"tokenization": "field"} if p in col.keys else {})
                for p in col.props
            ],
        }
        r = await c.post(f"{base}/v1/schema", json=body)
        if r.status_code not in (200, 422):  # 422: created concurrently
            r.raise_for_status()
    r = await c.post(f"{base}/v1/schema/{col.name}/tenants", json=[{"name": tenant_id}])
    if r.status_code not in (200, 422):
        r.raise_for_status()


def object_id(tenant_id: str, item_id: str, col: Collection = CLAUSES) -> str:
    # clause ids predate the SOP collection; keeping their seed keeps existing objects in place
    return str(
        uuid.uuid5(NS, f"{tenant_id}:{item_id}" if col is CLAUSES else f"{tenant_id}:{col.name}:{item_id}")
    )


async def index(tenant_id: str, items: list[dict[str, Any]], col: Collection) -> int:
    """Upsert `items` (dicts with col.props) into the tenant's shard. Returns how many were written."""
    if not items:
        return 0
    await ensure_collection(tenant_id, col)
    vectors = await embed(
        [". ".join(str(i.get(k) or "") for k in col.text) for i in items], "document", tenant_id
    )
    objects = [
        {
            "class": col.name,
            "id": object_id(tenant_id, str(i[col.id_prop]), col),
            "tenant": tenant_id,
            "properties": {
                p: (float(i[p]) if p in col.numbers else str(i.get(p) if i.get(p) is not None else ""))
                for p in col.props
            },
            "vector": v,
        }
        for i, v in zip(items, vectors, strict=True)
    ]
    r = await _client().post(f"{get_settings().weaviate_url}/v1/batch/objects", json={"objects": objects})
    r.raise_for_status()
    errors = [o["result"]["errors"] for o in r.json() if (o.get("result") or {}).get("errors")]
    if errors:
        raise RuntimeError(f"weaviate batch errors: {errors[:2]}")
    return len(objects)


async def search(
    tenant_id: str, query: str, col: Collection, where: dict[str, str] | None = None, limit: int = 4
) -> list[dict[str, Any]]:
    """Hybrid (BM25 + vector) search in the tenant's shard only; another tenant's items can't match.
    `where` is exact match on col.keys (ANDed)."""
    (vector,) = await embed([query], "query", tenant_id)
    ops = [
        f"{{path: [{json.dumps(k)}], operator: Equal, valueText: {json.dumps(v)}}}"
        for k, v in (where or {}).items()
        if k in col.keys and v
    ]
    flt = (
        ""
        if not ops
        else f", where: {ops[0]}"
        if len(ops) == 1
        else f", where: {{operator: And, operands: [{', '.join(ops)}]}}"
    )
    gql = (
        f"{{ Get {{ {col.name}(tenant: {json.dumps(tenant_id)}, limit: {int(limit)}{flt}, "
        f"hybrid: {{query: {json.dumps(query)}, vector: {json.dumps(vector)}, alpha: 0.5}}) "
        f"{{ {' '.join(col.props)} _additional {{ score }} }} }} }}"
    )
    r = await _client().post(f"{get_settings().weaviate_url}/v1/graphql", json={"query": gql})
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"weaviate query failed: {body['errors'][0].get('message')}")
    hits: list[dict[str, Any]] = body["data"]["Get"][col.name] or []
    return [
        {**{p: h.get(p) for p in col.props}, "score": float((h.get("_additional") or {}).get("score") or 0)}
        for h in hits
    ]


async def index_clauses(tenant_id: str, clauses: list[dict[str, Any]]) -> int:
    return await index(tenant_id, clauses, CLAUSES)


async def search_clauses(
    tenant_id: str, query: str, carrier_scac: str | None = None, limit: int = 4
) -> list[dict[str, Any]]:
    return await search(tenant_id, query, CLAUSES, {"carrier_scac": carrier_scac or ""}, limit)
