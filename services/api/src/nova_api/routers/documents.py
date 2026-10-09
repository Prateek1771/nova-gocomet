"""Documents: validated upload → object storage (tenant-keyed) → auto-start the doc type's workflow."""

import hashlib
import uuid
from typing import Annotated, Any

import pypdfium2 as pdfium
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import text

from nova_api import definitions
from nova_api.authz import TenantDep, authorize
from nova_api.routers.runs import launch
from nova_core import db, storage
from nova_core.settings import get_settings

router = APIRouter(prefix="/documents", tags=["documents"])
PDF_MAGIC = b"%PDF-"


class DocumentOut(BaseModel):
    id: str
    doc_type: str
    filename: str | None
    pages: int | None
    sha256: str
    uploaded_at: str
    run_id: str | None = None
    run_status: str | None = None


class UploadOut(DocumentOut):
    duplicate: bool


class DocumentDetail(DocumentOut):
    extraction: dict[str, Any] | None


_DOC = """select d.id, d.doc_type, d.filename, d.pages, d.sha256, d.uploaded_at, r.id as run_id,
                 r.status as run_status
          from documents d
          left join lateral (select id, status from workflow_runs
                             where subject_type = 'document' and subject_id = d.id::text
                             order by started_at desc limit 1) r on true"""


def _doc(r: Any) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "doc_type": r.doc_type,
        "filename": r.filename,
        "pages": r.pages,
        "sha256": r.sha256,
        "uploaded_at": r.uploaded_at.isoformat(),
        "run_id": str(r.run_id) if r.run_id else None,
        "run_status": r.run_status,
    }


def _page_count(data: bytes) -> int:
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        raise HTTPException(422, "file is not a readable PDF") from e
    try:
        return len(pdf)
    finally:
        pdf.close()


@router.post("", status_code=201)
async def upload(
    c: TenantDep,
    file: Annotated[UploadFile, File()],
    doc_type: Annotated[str, Form()] = "bill_of_lading",
) -> UploadOut:
    dt = definitions.doc_type(doc_type)
    if dt.default_workflow:
        await authorize(c, "can_start", f"workflow:{c.tenant_id}/{dt.default_workflow}")
    s = get_settings()
    limit = s.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"file larger than {s.max_upload_mb} MB")
    if not data.startswith(PDF_MAGIC):  # trust the bytes, not the client's content-type
        raise HTTPException(415, "only PDF documents are accepted")
    pages = _page_count(data)
    if pages < 1 or pages > s.max_pages:
        raise HTTPException(422, f"PDF must have 1-{s.max_pages} pages, got {pages}")
    sha = hashlib.sha256(data).hexdigest()

    async with db.tenant_session(c.tenant_id) as sess:
        existing = (await sess.execute(text(f"{_DOC} where d.sha256 = :h"), {"h": sha})).one_or_none()
    if existing:
        return UploadOut(**_doc(existing), duplicate=True)

    key = storage.key(c.tenant_id, f"documents/{sha}.pdf")
    await storage.put(key, data, "application/pdf")
    doc_id = uuid.uuid4()
    async with db.tenant_session(c.tenant_id) as sess:
        await sess.execute(
            text("""insert into documents (id, tenant_id, doc_type, storage_key, sha256, pages, uploaded_by,
                      filename, mime)
                    values (:id, :t, :dt, :k, :h, :p, cast(:u as uuid), :f, 'application/pdf')"""),
            {
                "id": doc_id,
                "t": c.tenant_id,
                "dt": doc_type,
                "k": key,
                "h": sha,
                "p": pages,
                "u": c.sub,
                "f": (file.filename or "")[:255] or None,
            },
        )
    if dt.default_workflow:
        await launch(
            c.tenant_id, dt.default_workflow, {"document_id": str(doc_id)}, subject=("document", str(doc_id))
        )
    return UploadOut(**(await _get(c, doc_id)), duplicate=False)


async def _get(c: TenantDep, doc_id: uuid.UUID) -> dict[str, Any]:
    async with db.tenant_session(c.tenant_id) as s:
        r = (await s.execute(text(f"{_DOC} where d.id = :id"), {"id": doc_id})).one_or_none()
    if r is None:
        raise HTTPException(404, "document not found")
    return _doc(r)


@router.get("")
async def list_documents(c: TenantDep, limit: int = 100) -> list[DocumentOut]:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        rows = await s.execute(
            text(f"{_DOC} order by d.uploaded_at desc limit :l"),  # noqa: S608 (constant query)
            {"l": min(limit, 500)},
        )
        return [DocumentOut(**_doc(r)) for r in rows]


@router.get("/{doc_id}")
async def get_document(c: TenantDep, doc_id: uuid.UUID) -> DocumentDetail:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    doc = await _get(c, doc_id)
    async with db.tenant_session(c.tenant_id) as s:
        x = (
            await s.execute(
                text("""select schema_key, fields, confidence, evidence, model from extractions
                        where document_id = :d order by created_at desc limit 1"""),
                {"d": doc_id},
            )
        ).one_or_none()
    extraction = dict(x._mapping) if x else None
    return DocumentDetail(**doc, extraction=extraction)


@router.get("/{doc_id}/file")
async def get_file(c: TenantDep, doc_id: uuid.UUID) -> Response:
    await authorize(c, "can_view", f"tenant:{c.tenant_id}")
    async with db.tenant_session(c.tenant_id) as s:
        r = (
            await s.execute(
                text("select storage_key, filename from documents where id = :id"), {"id": doc_id}
            )
        ).one_or_none()
    if r is None:
        raise HTTPException(404, "document not found")
    data = await storage.get(r.storage_key)
    name = (r.filename or f"{doc_id}.pdf").replace('"', "")
    return Response(
        data,
        media_type="application/pdf",
        headers={
            "content-disposition": f'inline; filename="{name}"',
            "cache-control": "private, max-age=3600",
        },
    )
