"""FastAPI application entry point.

Boot order (critical — per RESEARCH.md Pattern 4 + 01-02-SUMMARY.md notes):
  1. ``configure_logging()`` — must run before any structlog.get_logger() call
  2. ``init_sentry()`` — must run before router imports so Sentry auto-detection
     can inspect sys.modules for Starlette/FastAPI (already imported by this point)
  3. Router / integration imports — after logging + Sentry are wired

Lifespan owns all stateful resources (RESEARCH.md Pattern 1, Pitfall 1):
  - Postgres async engine + session factory (asyncpg / SQLAlchemy 2.0)
  - Redis async client + pool (redis-py)
  - LangGraph AsyncPostgresSaver checkpointer (psycopg 3)

The checkpointer uses explicit ``__aenter__`` / ``__aexit__`` (NOT ``async with``)
because the textbook pattern traps long-running ASGI servers — the scope around
``async with`` never exits until the lifespan generator resumes after ``yield``,
which is correct, but the explicit form makes the lifetime relationship obvious
to readers and avoids accidents when refactoring.

Resources are released in reverse acquisition order in the finally block.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from arq.connections import RedisSettings as ArqRedisSettings
from arq.connections import create_pool as arq_create_pool
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse

from app.config.checkpointer import build_checkpointer_cm
from app.config.db import create_db_engine, create_session_factory
from app.config.logging import configure_logging
from app.config.observability import init_sentry
from app.config.redis import close_redis_pool, create_redis_pool
from app.config.settings import settings

# ---------------------------------------------------------------------------
# Step 1 + 2: configure logging and Sentry BEFORE any router or feature import.
# ---------------------------------------------------------------------------
configure_logging(log_level=settings.app.log_level, env=settings.app.env)
init_sentry()

log = structlog.get_logger("main")

# noqa: E402 — intentional: these imports must come AFTER logging/Sentry init.
from app.features.qa.graph import build_qa_graph  # noqa: E402
from app.healthcheck import router as health_router  # noqa: E402
from app.integrations.chatwoot import get_chatwoot_client  # noqa: E402
from app.integrations.meta_cloud import get_meta_client  # noqa: E402
from app.integrations.openrouter import get_llm  # noqa: E402
from app.integrations.softseguros import get_softseguros_client  # noqa: E402
from app.security.kb_auditor import audit_kb  # noqa: E402
from app.webhooks.meta import router as meta_router  # noqa: E402

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Acquire all stateful resources at startup; release them at shutdown."""
    log.info("lifespan.startup", env=settings.app.env, version=settings.app.version)

    # 1. Postgres engine + session factory (asyncpg)
    engine = create_db_engine()
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)

    # 2. Redis client + pool
    redis_client, redis_pool = create_redis_pool()
    app.state.redis = redis_client
    app.state.redis_pool = redis_pool

    # 3. LangGraph checkpointer (psycopg 3) — explicit __aenter__/__aexit__
    #    pattern (CONTEXT.md D-06, RESEARCH.md Pattern 1 / Pitfall 1).
    cp_cm = build_checkpointer_cm()
    app.state._cp_cm = cp_cm
    app.state.checkpointer = await cp_cm.__aenter__()
    # setup() creates LangGraph checkpoint tables if not already present.
    # Idempotent — safe to call after alembic migration has already run.
    await app.state.checkpointer.setup()

    # 4. Meta Cloud API client (httpx singleton — NOT async-resource-heavy,
    #    no __aenter__/__aexit__ needed per PATTERNS.md Pitfall 1).
    app.state.meta = get_meta_client()

    # 5. SoftSeguros client (httpx singleton; factory leaves redis=None,
    #    we late-bind it from app.state.redis here so the cache layer is
    #    wired now that step 2 has Redis up).
    #    late-binding allowed: lifespan is the single owner of singletons.
    app.state.softseguros = get_softseguros_client()
    app.state.softseguros._redis = app.state.redis

    # 6. ARQ pool for background job enqueueing (mirror_inbound/mirror_outbound).
    app.state.arq = await arq_create_pool(
        ArqRedisSettings.from_dsn(settings.redis.url.get_secret_value())
    )

    # 7. Chatwoot client + late-bind redis (for conversation cache lookup).
    app.state.chatwoot = get_chatwoot_client()
    app.state.chatwoot._redis = app.state.redis

    # 8. KB audit FAIL-CLOSED (D-11 startup gate).
    #    Service refuses to start if KB content has high prompt-injection risk.
    kb_risk = await audit_kb("knowledge/dpg_cartera.md", redis=app.state.redis)
    if kb_risk > 50:
        raise RuntimeError(f"KB audit failed: risk={kb_risk}. Service not started.")
    if kb_risk > 20:
        log.warning("lifespan.kb_audit.warn", risk=kb_risk)
    else:
        log.info("lifespan.kb_audit.ok", risk=kb_risk)

    # 9. Compile LangGraph qa_graph with Postgres checkpointer.
    app.state.qa_graph = build_qa_graph().compile(checkpointer=app.state.checkpointer)

    log.info("lifespan.startup.complete")
    try:
        yield
    finally:
        # Release in reverse acquisition order.
        log.info("lifespan.shutdown")
        await app.state.arq.close()
        await app.state._cp_cm.__aexit__(None, None, None)
        await close_redis_pool(app.state.redis, app.state.redis_pool)
        await app.state.db_engine.dispose()
        log.info("lifespan.shutdown.complete")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="landa-agent-service",
    version=settings.app.version,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

# CorrelationIdMiddleware MUST be added before any middleware that wants the ID.
# It reads X-Request-ID from inbound requests (or generates one) and stores it
# in a contextvar that structlog's merge_contextvars processor picks up.
app.add_middleware(CorrelationIdMiddleware, header_name="X-Request-ID")


@app.middleware("http")
async def bind_correlation_to_structlog(request: Request, call_next: Any) -> Any:
    """Bind correlation_id + request metadata to the structlog context.

    asgi-correlation-id already placed the ID in a contextvar; we pull it into
    structlog so every log line within this request carries it automatically.
    The context is cleared after the response so it doesn't bleed into the next
    request handled by the same worker.
    """
    from asgi_correlation_id.context import correlation_id

    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id.get() or "-",
        path=request.url.path,
        method=request.method,
    )
    t0 = time.perf_counter()
    response: Any = None
    try:
        response = await call_next(request)
    finally:
        log.info(
            "request.complete",
            status=response.status_code if response is not None else 500,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        structlog.contextvars.clear_contextvars()
    return response


# ---------------------------------------------------------------------------
# Routers + dummy endpoints
# ---------------------------------------------------------------------------

app.include_router(health_router)
app.include_router(meta_router)


@app.post("/test/llm")
async def test_llm(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify the OpenRouter + LangSmith pipeline end-to-end.

    ROADMAP F1 deliverable GOAL-1.12. NOT for production — gate or remove in F5.
    """
    text_input = (payload or {}).get("text", "ping")
    t0 = time.perf_counter()
    llm = get_llm("conversation")
    result = await llm.ainvoke(text_input)
    return {
        "reply": getattr(result, "content", str(result)),
        "model": llm.model_name,
        "role": "conversation",
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


@app.post("/test/sentry")
async def test_sentry() -> dict[str, Any]:
    """Raise a synthetic exception so Sentry captures it.

    Verifies ROADMAP F1 success criterion "Error sintético llega a Sentry".
    NOT for production — gate or remove in F5.
    """
    raise RuntimeError("synthetic test error from /test/sentry")


@app.get("/test/poliza/{poliza_id}")
async def test_poliza(poliza_id: str, request: Request) -> dict[str, Any]:
    """Verify SoftSeguros client end-to-end (D-10).

    Returns raw upstream JSON without LLM transformation. NOT for
    production — gate or remove in F5.
    """
    t0 = time.perf_counter()
    client = request.app.state.softseguros
    poliza = await client.get_poliza(poliza_id)
    return {
        "poliza": poliza,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


__all__ = ["app", "lifespan"]
