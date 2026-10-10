"""LandingAI ADE (DPT-2 Parse + Extract) as a LiteLLM custom provider (ADR-034), so scans go through the
gateway like every model call: tenant virtual keys and budgets, Langfuse, response cache. Services name
the alias (`nova-extract-scan`), never LandingAI (rule 6).

Request: a system message with the extraction JSON schema, a user message with the PDF as a
`data:application/pdf;base64,` file part. Response content: JSON
{markdown, chunks, grounding, extraction, versions, credits}.
Usage: completion_tokens = credits x 1000; the config prices output tokens at $0.01 per 1000, so the
gateway books $0.01 per ADE credit against the tenant's budget."""

import base64
import json
from typing import Any

import httpx
import litellm
from litellm import CustomLLM

PDF_PREFIX = "data:application/pdf;base64,"


def _inputs(messages: list[dict[str, Any]]) -> tuple[dict[str, Any], bytes]:
    schema: dict[str, Any] | None = None
    pdf: bytes | None = None
    for m in messages:
        content = m.get("content")
        if m.get("role") == "system" and isinstance(content, str):
            schema = json.loads(content)
        elif isinstance(content, list):
            for part in content:
                url = (part.get("file") or {}).get("file_data") or ""
                if url.startswith(PDF_PREFIX):
                    pdf = base64.b64decode(url[len(PDF_PREFIX) :])
    if schema is None or pdf is None:
        raise ValueError("nova-extract-scan needs a system JSON schema and a PDF file part")
    return schema, pdf


async def _post(
    h: httpx.AsyncClient, what: str, files: dict[str, Any], data: dict[str, str]
) -> dict[str, Any]:
    try:
        r = await h.post(f"/v1/ade/{what}", files=files, data=data)
    except httpx.ConnectError:  # never reached LandingAI (e.g. a Docker DNS blip): safe to retry, not billed
        r = await h.post(f"/v1/ade/{what}", files=files, data=data)
    if r.status_code >= 400:  # 206 (partial extraction) passes; the caller validates against the schema
        raise RuntimeError(f"ADE {what}: HTTP {r.status_code} {r.text[:300]}")
    return dict(r.json())


class ADE(CustomLLM):
    async def acompletion(  # type: ignore[override]  # LiteLLM's full hook signature
        self,
        model: str,
        messages: list[dict[str, Any]],
        api_base: str,
        custom_prompt_dict: dict[str, Any],
        model_response: Any,
        print_verbose: Any,
        encoding: Any,
        api_key: str,
        logging_obj: Any,
        optional_params: dict[str, Any],
        acompletion: Any = None,
        litellm_params: Any = None,
        logger_fn: Any = None,
        headers: Any = None,
        timeout: Any = None,  # noqa: ASYNC109 (part of the LiteLLM hook signature)
        client: Any = None,
    ) -> litellm.ModelResponse:
        schema, pdf = _inputs(messages)
        auth = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(base_url=api_base, headers=auth, timeout=180) as h:
            parsed = await _post(
                h, "parse", {"document": ("document.pdf", pdf, "application/pdf")}, {"model": model}
            )
            md = parsed.get("markdown") or ""
            extracted = await _post(
                h,
                "extract",
                {"markdown": ("document.md", md.encode(), "text/markdown")},
                {"schema": json.dumps(schema)},
            )
        pm, em = parsed.get("metadata") or {}, extracted.get("metadata") or {}
        credits = float(pm.get("credit_usage") or 0) + float(em.get("credit_usage") or 0)
        content = {
            "markdown": md,
            "chunks": parsed.get("chunks") or [],
            "grounding": parsed.get("grounding") or {},
            "extraction": extracted.get("extraction") or {},
            "versions": [pm.get("version"), em.get("version")],
            "credits": credits,
        }
        tokens = round(credits * 1000)
        return litellm.ModelResponse(
            model=f"{pm.get('version')}+{em.get('version')}",
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(content)},
                }
            ],
            usage={"prompt_tokens": 0, "completion_tokens": tokens, "total_tokens": tokens},
        )


ade = ADE()
