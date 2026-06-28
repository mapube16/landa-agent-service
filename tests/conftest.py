"""Pytest configuration for landa-agent-service."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True, scope="session")
def _test_env() -> None:
    """Inject minimum env vars before any Settings() instantiation."""
    os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/landa_test")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
    os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-test-key")
    os.environ.setdefault("APP_ENV", "dev")
    # LangSmith env-vars are placeholders: tests never invoke an LLM, so
    # langchain auto-tracing has nothing to send. They exist only to satisfy
    # the /health env-presence probe.
    os.environ.setdefault("LANGSMITH_API_KEY", "ls-test-key")
    os.environ.setdefault("LANGSMITH_PROJECT", "landa-agent-test")
    # SENTRY_DSN intentionally unset -- init_sentry() no-ops when absent.


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Async httpx client wired to the FastAPI app (no live infrastructure).

    Lifespan is not started -- probe functions in test_health.py are stubbed.
    """
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app),
        base_url="http://test",
    ) as ac:
        yield ac


pytest_plugins: list[str] = []
