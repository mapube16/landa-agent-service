"""Async alembic environment for landa-agent-service.

Per RESEARCH.md "Alembic async env.py" code example + 01-03-PLAN.md task 3:

- Reads the DB URL from ``app.config.settings`` (no credentials in alembic.ini).
- Imports ``app.config.db.metadata`` for autogenerate.
- Future phases register additional models by importing them HERE so their
  tables attach to ``Base.metadata`` before ``target_metadata`` is captured.
- Offline mode is unused in this project and intentionally fails loud.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config.db import metadata as target_metadata
from app.config.settings import settings

# NOTE for future phases: import application models here so their tables
# register on ``app.config.db.Base.metadata`` before alembic captures it.
# Example (uncomment when models exist):
#     from app.memory import case_store  # noqa: F401
#     from app.security import audit_log  # noqa: F401
# Phase 1 ships zero app models — the schema is empty on the SQLAlchemy
# side; LangGraph checkpoint tables are managed under the psycopg connection
# inside the migration body, not via ``Base``.


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the DB URL from settings at runtime so alembic.ini stays creds-free.
# We use the asyncpg-flavoured URL because env.py runs under SQLAlchemy 2.0
# async engine; the per-migration psycopg connections (for LangGraph DDL) are
# opened separately inside the migration body.
config.set_main_option("sqlalchemy.url", settings.postgres.async_url)


def do_run_migrations(connection: Connection) -> None:
    """Sync-side migration hook invoked by ``connection.run_sync``."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Build an async engine and dispatch the sync migration runner via ``run_sync``."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Online migration entrypoint."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    # Offline mode is unused in this project. Fail loud rather than silently
    # producing a SQL script that nobody is going to apply.
    raise RuntimeError(
        "Offline alembic mode is not configured for landa-agent-service. "
        "Run `alembic upgrade head` against a live database."
    )

run_migrations_online()
