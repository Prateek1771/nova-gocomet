import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from nova_core.settings import get_settings


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    # migrations run as nova_owner (table owner); the app runs as nova_app so RLS always applies
    url = context.config.get_main_option("sqlalchemy.url") or get_settings().migrations_database_url
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


asyncio.run(main())
