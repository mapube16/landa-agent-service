"""Tests for app/main.py lifespan blocks 6-9 (Plan 03-05 Task 4).

Blocks under test:
  6 — ARQ pool creation
  7 — Chatwoot client + redis late-bind
  8 — KB audit FAIL-CLOSED gate (raises RuntimeError if risk > 50)
  9 — LangGraph qa_graph compile with checkpointer

All infrastructure (ARQ, Chatwoot, checkpointer, audit_kb) is mocked.
These tests run in CI without any live services.

Note: app.main imports app.config.checkpointer which imports psycopg at module
level. On this Windows dev machine psycopg has no libpq, so we stub the module
in sys.modules before importing app.main. Same workaround as test_webhooks_meta.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.modules stub for psycopg / checkpointer so app.main can be imported
# ---------------------------------------------------------------------------


def _stub_psycopg_modules() -> None:
    """Insert lightweight stubs for psycopg and checkpointer so the module-level
    import in app.config.checkpointer does not fail on machines without libpq."""
    for name in (
        "psycopg",
        "psycopg.rows",
        "psycopg_pool",
        "langgraph.checkpoint.postgres",
        "langgraph.checkpoint.postgres.aio",
    ):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)

    # app.config.checkpointer uses AsyncPostgresSaver from langgraph
    aio_mod = sys.modules["langgraph.checkpoint.postgres.aio"]
    if not hasattr(aio_mod, "AsyncPostgresSaver"):
        aio_mod.AsyncPostgresSaver = MagicMock()  # type: ignore[attr-defined]

    # Stub build_checkpointer_cm so it is importable even without real pg
    import types as _types

    cp_mod_name = "app.config.checkpointer"
    if cp_mod_name not in sys.modules:
        stub = _types.ModuleType(cp_mod_name)
        stub.build_checkpointer_cm = MagicMock()  # type: ignore[attr-defined]
        sys.modules[cp_mod_name] = stub


_stub_psycopg_modules()

# Now safe to import app.main (the checkpointer import resolves to our stub)

# ---------------------------------------------------------------------------
# Shared patch context for all lifespan tests
# ---------------------------------------------------------------------------

_COMMON_PATCHES = [
    "app.main.create_db_engine",
    "app.main.create_session_factory",
    "app.main.create_redis_pool",
    "app.main.get_meta_client",
    "app.main.close_redis_pool",
]


def _make_arq_pool() -> MagicMock:
    pool = MagicMock()
    pool.close = AsyncMock()
    return pool


def _make_engine() -> MagicMock:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    return engine


def _make_cp_cm(checkpointer: MagicMock | None = None) -> tuple[MagicMock, MagicMock]:
    cp = checkpointer or MagicMock()
    cp.setup = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cp)
    cm.__aexit__ = AsyncMock()
    return cm, cp


# ---------------------------------------------------------------------------
# Block 6 — ARQ pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_block6_arq_pool_created() -> None:
    """ARQ create_pool is called once; result stored on app.state.arq."""
    from app.main import lifespan

    fake_arq = _make_arq_pool()
    cm, _ = _make_cp_cm()

    with (
        patch("app.main.create_db_engine", return_value=_make_engine()),
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_pool", return_value=(MagicMock(), MagicMock())),
        patch("app.main.build_checkpointer_cm", return_value=cm),
        patch("app.main.get_meta_client"),
        patch("app.main.get_softseguros_client", return_value=MagicMock()),
        patch("app.main.arq_create_pool", AsyncMock(return_value=fake_arq)) as mock_arq,
        patch("app.main.get_chatwoot_client", return_value=MagicMock()),
        patch("app.main.audit_kb", AsyncMock(return_value=0)),
        patch("app.main.build_qa_graph", return_value=MagicMock()),
        patch("app.main.close_redis_pool", AsyncMock()),
    ):
        from fastapi import FastAPI

        test_app = FastAPI(lifespan=lifespan)
        async with lifespan(test_app):
            mock_arq.assert_called_once()
            assert test_app.state.arq is fake_arq


# ---------------------------------------------------------------------------
# Block 7 — Chatwoot late-bind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_block7_chatwoot_redis_bound() -> None:
    """Chatwoot client._redis is set to app.state.redis after creation."""
    from app.main import lifespan

    fake_arq = _make_arq_pool()
    cm, _ = _make_cp_cm()
    fake_chatwoot = MagicMock()
    fake_redis = MagicMock()

    with (
        patch("app.main.create_db_engine", return_value=_make_engine()),
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_pool", return_value=(fake_redis, MagicMock())),
        patch("app.main.build_checkpointer_cm", return_value=cm),
        patch("app.main.get_meta_client"),
        patch("app.main.get_softseguros_client", return_value=MagicMock()),
        patch("app.main.arq_create_pool", AsyncMock(return_value=fake_arq)),
        patch("app.main.get_chatwoot_client", return_value=fake_chatwoot),
        patch("app.main.audit_kb", AsyncMock(return_value=0)),
        patch("app.main.build_qa_graph", return_value=MagicMock()),
        patch("app.main.close_redis_pool", AsyncMock()),
    ):
        from fastapi import FastAPI

        test_app = FastAPI(lifespan=lifespan)
        async with lifespan(test_app):
            assert fake_chatwoot._redis is fake_redis


# ---------------------------------------------------------------------------
# Block 8 — KB audit FAIL-CLOSED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_block8_kb_audit_fail_closed_raises() -> None:
    """RuntimeError raised during startup when audit_kb returns risk > 50."""
    from app.main import lifespan

    fake_arq = _make_arq_pool()
    cm, _ = _make_cp_cm()

    with (
        patch("app.main.create_db_engine", return_value=_make_engine()),
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_pool", return_value=(MagicMock(), MagicMock())),
        patch("app.main.build_checkpointer_cm", return_value=cm),
        patch("app.main.get_meta_client"),
        patch("app.main.get_softseguros_client", return_value=MagicMock()),
        patch("app.main.arq_create_pool", AsyncMock(return_value=fake_arq)),
        patch("app.main.get_chatwoot_client", return_value=MagicMock()),
        patch("app.main.audit_kb", AsyncMock(return_value=75)),  # > 50 → FAIL-CLOSED
        patch("app.main.build_qa_graph", return_value=MagicMock()),
        patch("app.main.close_redis_pool", AsyncMock()),
    ):
        from fastapi import FastAPI

        test_app = FastAPI(lifespan=lifespan)
        with pytest.raises(RuntimeError, match="KB audit failed"):
            async with lifespan(test_app):
                pass  # must not reach here


@pytest.mark.asyncio
async def test_lifespan_block8_kb_audit_ok_does_not_raise() -> None:
    """No error raised when audit_kb returns risk <= 50."""
    from app.main import lifespan

    fake_arq = _make_arq_pool()
    cm, _ = _make_cp_cm()

    with (
        patch("app.main.create_db_engine", return_value=_make_engine()),
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_pool", return_value=(MagicMock(), MagicMock())),
        patch("app.main.build_checkpointer_cm", return_value=cm),
        patch("app.main.get_meta_client"),
        patch("app.main.get_softseguros_client", return_value=MagicMock()),
        patch("app.main.arq_create_pool", AsyncMock(return_value=fake_arq)),
        patch("app.main.get_chatwoot_client", return_value=MagicMock()),
        patch("app.main.audit_kb", AsyncMock(return_value=10)),
        patch("app.main.build_qa_graph", return_value=MagicMock()),
        patch("app.main.close_redis_pool", AsyncMock()),
    ):
        from fastapi import FastAPI

        test_app = FastAPI(lifespan=lifespan)
        async with lifespan(test_app):
            pass  # no raise expected


# ---------------------------------------------------------------------------
# Block 9 — qa_graph compile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_block9_qa_graph_compiled() -> None:
    """build_qa_graph().compile(checkpointer=...) result stored on app.state.qa_graph."""
    from app.main import lifespan

    fake_arq = _make_arq_pool()
    fake_checkpointer = MagicMock()
    cm, _ = _make_cp_cm(fake_checkpointer)
    fake_graph_builder = MagicMock()
    compiled_graph = MagicMock()
    fake_graph_builder.compile = MagicMock(return_value=compiled_graph)

    with (
        patch("app.main.create_db_engine", return_value=_make_engine()),
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_pool", return_value=(MagicMock(), MagicMock())),
        patch("app.main.build_checkpointer_cm", return_value=cm),
        patch("app.main.get_meta_client"),
        patch("app.main.get_softseguros_client", return_value=MagicMock()),
        patch("app.main.arq_create_pool", AsyncMock(return_value=fake_arq)),
        patch("app.main.get_chatwoot_client", return_value=MagicMock()),
        patch("app.main.audit_kb", AsyncMock(return_value=0)),
        patch("app.main.build_qa_graph", return_value=fake_graph_builder),
        patch("app.main.close_redis_pool", AsyncMock()),
    ):
        from fastapi import FastAPI

        test_app = FastAPI(lifespan=lifespan)
        async with lifespan(test_app):
            fake_graph_builder.compile.assert_called_once_with(checkpointer=fake_checkpointer)
            assert test_app.state.qa_graph is compiled_graph


# ---------------------------------------------------------------------------
# Shutdown — ARQ closed before checkpointer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_shutdown_arq_closed_before_checkpointer() -> None:
    """ARQ pool.close() is called before checkpointer __aexit__ on shutdown."""
    from app.main import lifespan

    call_order: list[str] = []

    fake_arq = MagicMock()

    async def arq_close() -> None:
        call_order.append("arq_close")

    fake_arq.close = arq_close

    async def cp_aexit(*args: object) -> None:
        call_order.append("cp_aexit")

    cp_mock = MagicMock()
    cp_mock.setup = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cp_mock)
    cm.__aexit__ = cp_aexit

    with (
        patch("app.main.create_db_engine", return_value=_make_engine()),
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_pool", return_value=(MagicMock(), MagicMock())),
        patch("app.main.build_checkpointer_cm", return_value=cm),
        patch("app.main.get_meta_client"),
        patch("app.main.get_softseguros_client", return_value=MagicMock()),
        patch("app.main.arq_create_pool", AsyncMock(return_value=fake_arq)),
        patch("app.main.get_chatwoot_client", return_value=MagicMock()),
        patch("app.main.audit_kb", AsyncMock(return_value=0)),
        patch("app.main.build_qa_graph", return_value=MagicMock()),
        patch("app.main.close_redis_pool", AsyncMock()),
    ):
        from fastapi import FastAPI

        test_app = FastAPI(lifespan=lifespan)
        async with lifespan(test_app):
            pass

    assert "arq_close" in call_order
    assert "cp_aexit" in call_order
    assert call_order.index("arq_close") < call_order.index("cp_aexit")
