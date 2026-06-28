"""Tests for GET /health (plan 01-04, GOAL-1.11).

Infrastructure probes are stubbed so tests run without live Postgres/Redis.
The ``_stub_probes`` fixture replaces the three probe coroutine functions in
``app.healthcheck`` with no-op async functions before each test, then restores
the originals via monkeypatch teardown.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _stub_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the three probe coroutines with no-op stubs.

    This lets /health run without live Postgres or Redis (lifespan is not
    started in unit tests — app.state attributes are absent). The LangSmith
    env probe is a pure env-var check so it never needs stubbing.
    """
    from app import healthcheck

    async def _ok_request(req: object) -> None:  # noqa: ARG001
        return None

    async def _ok() -> None:
        return None

    monkeypatch.setattr(healthcheck, "_check_postgres", _ok_request)
    monkeypatch.setattr(healthcheck, "_check_redis", _ok_request)
    monkeypatch.setattr(healthcheck, "_check_openrouter", _ok)


async def test_health_returns_200_and_healthy(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert set(body["components"]) >= {"postgres", "redis", "openrouter", "langsmith_env"}
    assert body["components"]["postgres"]["ok"] is True
    assert body["components"]["redis"]["ok"] is True
    assert body["components"]["openrouter"]["ok"] is True


async def test_health_degraded_when_redis_fails(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import healthcheck

    async def _boom(req: object) -> None:  # noqa: ARG001
        raise RuntimeError("redis down")

    monkeypatch.setattr(healthcheck, "_check_redis", _boom)
    r = await client.get("/health")
    # Must still return HTTP 200 (Railway compat — see module docstring)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["components"]["redis"]["ok"] is False
    assert body["components"]["redis"]["error"] == "RuntimeError"


async def test_health_response_includes_version_and_env(client: AsyncClient) -> None:
    r = await client.get("/health")
    body = r.json()
    assert "version" in body
    assert "env" in body
    assert body["env"] in ("dev", "staging", "prod")
