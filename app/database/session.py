from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

EXPECTED_SCHEMA_VERSION = "0012_multicurrency_finance"


def normalize_async_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if (parsed.hostname or "").endswith(".supabase.com") and not ({"ssl", "sslmode"} & query.keys()):
        url += ("&" if parsed.query else "?") + "ssl=require"
    return url


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        normalize_async_url(settings.database_url.get_secret_value()),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"timeout": 2},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        version = await connection.scalar(
            text("SELECT version_num FROM tutor_bot.alembic_version ORDER BY version_num DESC LIMIT 1")
        )
        if not version:
            raise RuntimeError("Database schema is missing. Run: alembic upgrade head")
        if version != EXPECTED_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema is at {version}, expected {EXPECTED_SCHEMA_VERSION}. Run: alembic upgrade head"
            )
