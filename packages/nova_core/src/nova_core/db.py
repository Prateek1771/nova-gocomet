import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from nova_core.settings import get_settings


@lru_cache
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _sessions() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine(), expire_on_commit=False)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """Session with no tenant set: RLS hides every tenant row. Only for tenant-less tables."""
    async with _sessions()() as s, s.begin():
        yield s


@asynccontextmanager
async def tenant_session(tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Transaction scoped to one tenant; RLS policies read app.tenant_id."""
    async with _sessions()() as s, s.begin():
        await s.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
        yield s
