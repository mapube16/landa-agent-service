"""initial schema: langgraph checkpoint tables via AsyncPostgresSaver.setup()

Revision ID: 0001
Revises:
Create Date: 2026-06-27 00:00:00.000000

Rationale (see 01-03-SUMMARY.md for the full decision record):

  This migration delegates DDL to ``AsyncPostgresSaver.setup()`` rather than
  embedding hardcoded ``CREATE TABLE`` strings. Two reasons:

  1. ``langgraph-checkpoint-postgres`` 3.x ships 10 migrations including
     ``CREATE INDEX CONCURRENTLY`` statements (entries 6/7/8 in MIGRATIONS).
     ``CREATE INDEX CONCURRENTLY`` MUST run outside a transaction. Alembic's
     default ``with context.begin_transaction()`` would force the indices into
     a transaction and Postgres would reject them. ``AsyncPostgresSaver`` opens
     its own psycopg connection with ``autocommit=True``, sidestepping the
     transaction issue.

  2. Hardcoding the schema in this file would drift the moment LangGraph
     ships migration #11. Delegating to ``setup()`` means whichever version
     of the library is installed at deploy time owns the truth.

The bridge: alembic's ``upgrade()`` is sync, but ``AsyncPostgresSaver.setup()``
is async and lives behind ``AsyncPostgresSaver.from_conn_string`` (an async
context manager). We open a fresh event loop just for the migration via
``asyncio.run`` on a fresh psycopg connection — this is intentional and does
NOT race with the alembic-async env.py because env.py has already returned to
sync land by the time ``upgrade()`` runs.

Downgrade drops the four tables. Idempotent: re-running ``upgrade`` after a
partial failure is safe because LangGraph's ``setup()`` checks the
``checkpoint_migrations`` version row before applying each entry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from alembic import op
from app.config.settings import settings

# Alembic identifiers ----------------------------------------------------------
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


async def _apply_checkpointer_setup() -> None:
    """Open a fresh psycopg connection and run LangGraph's idempotent setup().

    Uses the RAW Postgres URL (``postgresql://...``) — NOT the
    ``postgresql+asyncpg://`` variant — because psycopg 3 parses the raw
    scheme directly. This connection is independent of the alembic-async
    engine; it only lives for the duration of setup().
    """
    raw_url = settings.postgres.url.get_secret_value()
    async with AsyncPostgresSaver.from_conn_string(raw_url) as saver:
        await saver.setup()


def upgrade() -> None:
    """Create LangGraph checkpoint tables (delegated to AsyncPostgresSaver)."""
    asyncio.run(_apply_checkpointer_setup())


def downgrade() -> None:
    """Drop LangGraph checkpoint tables.

    Order matters: ``checkpoint_writes`` and ``checkpoint_blobs`` reference
    rows that originate in ``checkpoints`` semantically (no FK in v1 but the
    drop order still expresses the dependency).
    """
    op.execute("DROP TABLE IF EXISTS checkpoint_writes")
    op.execute("DROP TABLE IF EXISTS checkpoint_blobs")
    op.execute("DROP TABLE IF EXISTS checkpoints")
    op.execute("DROP TABLE IF EXISTS checkpoint_migrations")
