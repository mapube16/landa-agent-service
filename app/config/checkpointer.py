"""LangGraph ``AsyncPostgresSaver`` async-context-manager factory for the lifespan.

Per CONTEXT.md D-06 (Two-Pool model) + RESEARCH.md Pattern 1 + Pitfall 1:

- The checkpointer connects to the SAME Postgres database as the SQLAlchemy
  app engine, but via the **psycopg 3** driver (the only driver LangGraph
  ships against for ``AsyncPostgresSaver``). The two pools are independent
  by design (D-06) — pool exhaustion in one doesn't poison the other.

- ``AsyncPostgresSaver.from_conn_string`` returns an async context manager.
  The lifespan must use it with **explicit** ``__aenter__`` / ``__aexit__``
  (NOT the textbook ``async with`` block). The textbook pattern traps
  long-running servers because the surrounding scope never exits — the
  Medium write-up "I Built a LangGraph + FastAPI Agent... and Spent Days
  Fighting Postgres" documents the exact failure mode.

  The lifespan idiom in ``main.py`` (plan 01-04) is therefore::

      cm = build_checkpointer_cm()
      app.state.checkpointer = await cm.__aenter__()
      app.state._checkpointer_cm = cm  # retained for shutdown
      yield
      await app.state._checkpointer_cm.__aexit__(None, None, None)

- ``setup()`` (creation of ``checkpoints``, ``checkpoint_blobs``,
  ``checkpoint_writes``, ``checkpoint_migrations`` tables) is handled by the
  alembic migration ``0001_initial_schema`` so bootstrap order is deterministic.
  ``setup()`` is idempotent — re-running it after alembic has already created
  the tables is safe (LangGraph maintains its own ``checkpoint_migrations``
  table to skip already-applied DDL).

- The conn string passed here is the RAW Postgres URL (``postgresql://…``),
  NOT the ``postgresql+asyncpg://`` SQLAlchemy variant. psycopg 3 understands
  the raw scheme directly; ``+asyncpg`` would break the parser.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config.settings import settings


def build_checkpointer_cm() -> AbstractAsyncContextManager[AsyncPostgresSaver]:
    """Return the (not-yet-entered) async context manager for ``AsyncPostgresSaver``.

    The lifespan owns entering/exiting this context manager exactly once —
    see module docstring for the canonical idiom.

    Connection-count budget (paired with ``app.config.db.create_db_engine``,
    per CONTEXT.md D-06): psycopg's connection management here is internal to
    LangGraph's saver — under v1 traffic it holds at most a handful of conns,
    leaving the asyncpg pool (15 conns) the dominant contributor to the
    ~30-conn budget vs Postgres ``max_connections=100``.
    """
    return AsyncPostgresSaver.from_conn_string(settings.postgres.url.get_secret_value())


__all__ = [
    "build_checkpointer_cm",
]
