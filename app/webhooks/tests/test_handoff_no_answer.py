"""Tests for POST /case/handoff/no_answer (Plan 04-07, D-19..D-23).

Minimal FastAPI app with only the handoff router; ``app.state`` carries an
AsyncMock Meta client and a fake session factory (no live Postgres — repo
pattern from test_chatwoot_webhook.py).

Covers: bearer reject (missing + bad), Pydantic 422s (missing field, bad
E.164), happy path (Case insert + template send with D-20 body params and
D-21 quick-reply payloads), idempotent retransmit (no second template).

NOTE: the plan's behavior example used ``+573001`` but the mandated E.164
regex ``^\\+\\d{8,15}$`` (threat model T-04-07-04) requires 8-15 digits —
the security constraint wins, so tests use a full-length Colombian number.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest  # type: ignore[import-not-found]
from httpx import ASGITransport, AsyncClient

TOKEN = "test-lambda-token"  # noqa: S105 — matches app/conftest.py placeholder
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CASE_ID = "550e8400-e29b-41d4-a716-446655440000"
PHONE = "+573001234567"


def _body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "phone": PHONE,
        "cliente_nombre": "Juan",
        "numero_poliza": "12345",
        "case_id": CASE_ID,
    }
    base.update(overrides)
    return base


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """Single-case fake: execute() finds the previously added Case (or None)."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    async def execute(self, stmt: Any) -> _Result:
        return _Result(self.added[0] if self.added else None)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture  # type: ignore[untyped-decorator]
def meta() -> MagicMock:
    m = MagicMock()
    m.send_template = AsyncMock(return_value="wamid.tpl1")
    return m


@pytest.fixture  # type: ignore[untyped-decorator]
def session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture  # type: ignore[untyped-decorator]
async def client(meta: MagicMock, session: _FakeSession) -> AsyncIterator[AsyncClient]:
    from fastapi import FastAPI

    from app.webhooks.handoff import router

    app = FastAPI()
    app.include_router(router)
    app.state.meta = meta
    app.state.redis = MagicMock()  # bare mock: check_rate_limit's own eval() call
    # raises on it (not an AsyncMock) -> caught by _check_handoff_rate_limit's
    # fail-open except, same real-world behavior as a genuinely down Redis.

    @asynccontextmanager
    async def factory() -> AsyncIterator[_FakeSession]:
        yield session

    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_missing_bearer_returns_401(client: AsyncClient, meta: MagicMock) -> None:
    r = await client.post("/case/handoff/no_answer", json=_body())
    assert r.status_code == 401
    meta.send_template.assert_not_called()


async def test_bad_bearer_returns_401(client: AsyncClient, meta: MagicMock) -> None:
    r = await client.post(
        "/case/handoff/no_answer",
        json=_body(),
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401
    meta.send_template.assert_not_called()


async def test_validation_error_returns_422(client: AsyncClient) -> None:
    body = _body()
    del body["phone"]
    r = await client.post("/case/handoff/no_answer", json=body, headers=AUTH)
    assert r.status_code == 422


async def test_invalid_phone_format_returns_422(client: AsyncClient, meta: MagicMock) -> None:
    r = await client.post("/case/handoff/no_answer", json=_body(phone="abc"), headers=AUTH)
    assert r.status_code == 422
    meta.send_template.assert_not_called()


async def test_happy_path_creates_case_and_sends_template(
    client: AsyncClient, meta: MagicMock, session: _FakeSession
) -> None:
    r = await client.post("/case/handoff/no_answer", json=_body(), headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"case_id": CASE_ID, "sent": True}

    meta.send_template.assert_awaited_once_with(
        PHONE,
        "voice_no_answer_followup",
        "es",
        body_params=[],
        quick_reply_payloads=["si_ayudenme", "mas_tarde"],
    )
    assert session.commits == 1
    case = session.added[0]
    assert case.case_id == CASE_ID
    assert case.phone == PHONE
    assert case.cliente_nombre == "Juan"
    assert case.poliza_id == "12345"
    assert case.status == "awaiting_receipt"


async def test_idempotent_second_call_skips_send(
    client: AsyncClient, meta: MagicMock, session: _FakeSession
) -> None:
    r1 = await client.post("/case/handoff/no_answer", json=_body(), headers=AUTH)
    r2 = await client.post("/case/handoff/no_answer", json=_body(), headers=AUTH)

    assert r1.status_code == 200
    assert r1.json()["sent"] is True
    assert r2.status_code == 200
    assert r2.json()["sent"] is False
    assert meta.send_template.call_count == 1
    assert len(session.added) == 1
