"""Async database layer.

Even with Robyn, the database remains the slowest component of the stack.
Optimizing HTTP while ignoring SQL is like buying racing tires for a
bicycle -- so this stays boring and conventional: SQLAlchemy + asyncpg,
with a healthy connection pool.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    future=True,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_models() -> None:
    """Create tables on startup.

    Fine for a demo/local setup; a real production service should manage
    schema changes with migrations (e.g. Alembic) instead.
    """
    from models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Cleanly close the connection pool on shutdown."""
    await engine.dispose()
