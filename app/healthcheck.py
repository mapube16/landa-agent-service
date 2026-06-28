"""Health check endpoint — GET /health.

Per ROADMAP F1 success criterion GOAL-1.11 + RESEARCH.md Pattern 5:

- Always returns HTTP 200 (Railway routes traffic based on 200, not body content).
- ``status`` field carries the truth: "healthy" when all probes pass,
  "degraded" when any probe fails — Railway keeps routing but humans get alerted.
- Four probes run in parallel via ``asyncio.gather`` with a 1s timeout each:
    1. Postgres — ``SELECT 1`` via the SQLAlchemy session factory in app.state
    2. Redis — ``PING`` via the redis-py client in app.state
    3. OpenRouter — ``HEAD /api/v1`` (cheap: no quota burn; proves DNS+TLS)
    4. LangSmith — env-var presence (no network call; just config validation)
- Probe result shape: ``{ok: bool, latency_ms: float}`` on success;
  ``{ok: False, error: <ExceptionTypeName>, latency_ms: float}`` on failure.
  Error messages are suppressed (only type name) to avoid leaking conn strings
  (T-01-15 threat mitigation).

NEVER change HTTP status to non-200 — Railway uses the status code to decide
whether to route traffic to this instance; a 503 on degraded would drop traffic
instead of alerting.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Request
from sqlalchemy import text

from app.config.settings import settings

router = APIRouter(tags=["meta"])
log = structlog.get_logger("healthcheck")


async def _probe(coro: Any, timeout_s: float = 1.0) -> dict[str, Any]:
    """Wrap a coroutine with a timeout and return a probe-result dict.

    Never raises — all exceptions are caught and surfaced as ``ok=False``.
    Only the exception *type name* is included (not the message) so connection
    strings never appear in the response body (T-01-15).
    """
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(coro, timeout=timeout_s)
        return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": type(exc).__name__,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


async def _check_postgres(request: Request) -> None:
    """Execute ``SELECT 1`` via the SQLAlchemy async session factory."""
    session_factory = request.app.state.session_factory
    async with session_factory() as s:
        await s.execute(text("SELECT 1"))


async def _check_redis(request: Request) -> None:
    """Send a ``PING`` via the redis-py async client."""
    await request.app.state.redis.ping()


async def _check_openrouter() -> None:
    """HEAD the OpenRouter API root — cheap reachability probe (no quota burn).

    Status 200 and 404 both prove DNS + TLS + upstream reachability.
    Only 5xx is treated as a failure.
    """
    async with httpx.AsyncClient(timeout=1.0) as client:
        r = await client.head(settings.openrouter.base_url)
        if r.status_code >= 500:
            raise RuntimeError(f"upstream {r.status_code}")


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Return infrastructure health status.

    Always HTTP 200 (Railway compat). The ``status`` field is the truth:
    ``"healthy"`` when all probes pass, ``"degraded"`` when any fails.
    """
    pg, redis_, openrouter = await asyncio.gather(
        _probe(_check_postgres(request)),
        _probe(_check_redis(request)),
        _probe(_check_openrouter()),
    )
    langsmith_env: dict[str, Any] = {
        "ok": bool(
            settings.langsmith.api_key and settings.langsmith.project and settings.langsmith.tracing
        )
    }

    components: dict[str, Any] = {
        "postgres": pg,
        "redis": redis_,
        "openrouter": openrouter,
        "langsmith_env": langsmith_env,
    }
    all_ok = all(c["ok"] for c in components.values())
    status = "healthy" if all_ok else "degraded"

    log.info("health.check", status=status, components=components)

    return {
        "status": status,
        "components": components,
        "version": settings.app.version,
        "env": settings.app.env,
    }


__all__ = ["router"]
