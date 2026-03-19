"""SQLAlchemy engine and session management."""

from __future__ import annotations

import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_database_url() -> str:
    """Get database URL from environment or default to SQLite."""
    url = os.environ.get("DATABASE_URL")
    if url:
        # Convert postgres:// to postgresql+asyncpg://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    # Default: SQLite in ~/.nanobot/
    db_path = Path.home() / ".nanobot" / "nonobot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


def _enable_sqlite_fk(dbapi_conn, _connection_record):
    """Enable foreign keys for SQLite connections."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def init_engine(url: str | None = None) -> AsyncEngine:
    """Initialize the database engine and create tables."""
    global _engine, _session_factory

    db_url = url or get_database_url()

    kwargs = {}
    if "sqlite" in db_url:
        kwargs["connect_args"] = {"check_same_thread": False}

    _engine = create_async_engine(db_url, echo=False, **kwargs)

    # Enable FK for SQLite
    if "sqlite" in db_url:
        event.listen(_engine.sync_engine, "connect", _enable_sqlite_fk)

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    # Create all tables
    from nanobot.db.models import Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    return _engine


async def close_engine() -> None:
    """Close the database engine."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the async session factory."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_engine() first.")
    return _session_factory


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session context manager."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
