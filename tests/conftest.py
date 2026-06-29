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
    # LANGSMITH_WORKSPACE_ID intentionally unset -- default None is valid.
    # SENTRY_DSN intentionally unset -- init_sentry() no-ops when absent.
    # Phase 2 placeholders so WhatsAppSettings + SoftSegurosSettings instancian
    # clean en tests sin levantar ValidationError. Valores ficticios; ninguno
    # se usa contra una API real -- los tests que tocan IO stubbean los clients.
    os.environ.setdefault("WA_TOKEN", "wa-test-token")
    os.environ.setdefault("WA_PHONE_ID", "1267241483129092")
    os.environ.setdefault("WA_BUSINESS_ACCOUNT_ID", "1451322196454283")
    os.environ.setdefault("WA_WEBHOOK_SECRET", "test-webhook-secret-do-not-use-in-prod")
    os.environ.setdefault("WA_VERIFY_TOKEN", "test-verify-token-do-not-use-in-prod")
    os.environ.setdefault("WA_ECHO_ALLOWLIST", "+15555550100,+15555550101")
    os.environ.setdefault("SOFTSEGUROS_BASE_URL", "https://app.softseguros.com/")
    os.environ.setdefault("SOFTSEGUROS_USERNAME", "test-user")
    os.environ.setdefault("SOFTSEGUROS_PASSWORD", "test-pass")
    # Phase 3 placeholders so ChatwootSettings instantiates clean in tests.
    # Values are dummy; tests that touch Chatwoot IO stub the client.
    os.environ.setdefault("CHATWOOT_URL", "https://chat-test.example.com")
    os.environ.setdefault("CHATWOOT_API_KEY", "cw-test-key")
    os.environ.setdefault("CHATWOOT_ACCOUNT_ID", "1")
    os.environ.setdefault("CHATWOOT_INBOX_ID", "2")


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
