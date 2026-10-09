"""Object storage (MinIO in dev). Every key starts with tenant/{tenant_id}/ so isolation is visible in
the key itself; callers never build keys by hand."""

import asyncio
import io
import uuid
from functools import lru_cache

from minio import Minio

from nova_core.settings import get_settings


@lru_cache
def _client() -> Minio:
    s = get_settings()
    c = Minio(s.s3_endpoint, s.s3_access_key, s.s3_secret_key, secure=s.s3_secure)
    if not c.bucket_exists(s.s3_bucket):  # ponytail: lazy bucket create instead of an init container
        c.make_bucket(s.s3_bucket)
    return c


def key(tenant_id: uuid.UUID | str, name: str) -> str:
    return f"tenant/{tenant_id}/{name}"


def _put(k: str, data: bytes, content_type: str) -> None:
    _client().put_object(get_settings().s3_bucket, k, io.BytesIO(data), len(data), content_type=content_type)


def _get(k: str) -> bytes:
    r = _client().get_object(get_settings().s3_bucket, k)
    try:
        return r.read()
    finally:
        r.close()
        r.release_conn()


async def put(k: str, data: bytes, content_type: str) -> None:
    await asyncio.to_thread(_put, k, data, content_type)


async def get(k: str) -> bytes:
    return await asyncio.to_thread(_get, k)
