"""SQLAlchemy 2.0 async engine factory + Base/metadata for app data.

Per CONTEXT.md D-06 (Two-Pool model) and RESEARCH.md Pattern 1 + State of the Art:

- The app uses **asyncpg via SQLAlchemy 2.0** for app data (cases, debtor flags,
  audit log — all future-phase tables). A separate ``psycopg 3`` pool lives in
  ``app.config.checkpointer`` for LangGraph state.
- Engine config: ``pool_size=10 + max_overflow=5`` → 15 conns max from this engine.
  Sum with checkpointer (psycopg pool) stays well under Postgres
  ``max_connections=100`` default.
- ``pool_pre_ping=True`` prevents "broken pipe" on idle Railway connections.
- ``Base`` is declared so alembic autogenerate (future phases) can detect tables
  registered on it. Phase 1 has zero app tables — the schema is empty on the
  SQLAlchemy side; LangGraph checkpoint tables live entirely under psycopg
  (migrated by alembic but unmanaged by SQLAlchemy ORM).
- ``naming_convention`` is configured up-front so future migrations have stable,
  predictable index/constraint names without retroactive renames.

NEVER import this module from request handlers — the engine is created once in
``app.main:lifespan`` (plan 01-04) and exposed via ``request.app.state``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config.settings import settings

# Alembic-compatible naming convention. Locking this in F1 means future
# autogenerate runs produce stable identifier names without case-by-case
# renames. Pattern matches Alembic's own documented best practice.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for all app-owned SQLAlchemy models.

    Future phases add models under ``app.memory`` / ``app.security`` (audit_log,
    cases, debtor_flags) by subclassing this Base. Phase 1 ships zero tables
    here — LangGraph checkpoint tables are managed under psycopg, not SQLAlchemy.
    """

    metadata = metadata


def create_db_engine() -> AsyncEngine:
    """Build the application's SQLAlchemy 2.0 async engine (asyncpg driver).

    Returns a fresh engine each call; the lifespan owns the single instance.
    Pool sizing budget (per CONTEXT.md D-06 documentation requirement):
      - pool_size=10 base connections held open
      - max_overflow=5 burst connections
      - hard ceiling: 15 conns from this engine
      - paired with psycopg checkpointer pool: ~30 conns total to Postgres
    """
    return create_async_engine(
        settings.postgres.async_url,
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,  # recycle conns every 30 min to dodge Railway idle kills
        echo=False,  # T-01-12 mitigation: never log connection strings or SQL
        future=True,
        # command_timeout: asyncpg has no default here — an unbounded query on an
        # already-open connection can pin a pool slot forever under a DB stall.
        connect_args={"command_timeout": 20},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the ``async_sessionmaker`` bound to the given engine.

    ``expire_on_commit=False`` is the FastAPI-recommended setting for async
    sessions — ORM objects stay usable after ``await session.commit()`` without
    triggering implicit reload queries.
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Async context manager: open session, commit on success, rollback on error.

    Usage::

        async with session_scope(request.app.state.session_factory) as session:
            await session.execute(...)
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "create_db_engine",
    "create_session_factory",
    "metadata",
    "session_scope",
]
