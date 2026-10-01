"""Tests for POST /case/handoff (Contrato A, Fase 6).

Mirrors test_handoff_no_answer.py's fixtures/style — minimal FastAPI app with
only the handoff router, fake session (no live Postgres).
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
        "case_id": CASE_ID,
        "debtor_id": "dpg-deudor-123",
        "poliza_number": "POL-000123",
        "phone": PHONE,
        "call_id": "twilio-CAxxxx",
        "user_id": "agente-voz-7",
        "initial_context": "Cliente dice que ya pago.",
        "message": "Hola, vi que hablaste con nuestro asistente de voz.",
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
    m.send_text = AsyncMock(return_value="wamid.txt1")
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
    seen: set[bytes] = set()

    async def _set_nx(key: bytes, *_: Any, **__: Any) -> bool | None:
        if key in seen:
            return None
        seen.add(key)
        return True

    app.state.redis.set = _set_nx

    @asynccontextmanager
    async def factory() -> AsyncIterator[_FakeSession]:
        yield session

    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_missing_bearer_returns_401(client: AsyncClient, meta: MagicMock) -> None:
    r = await client.post("/case/handoff", json=_body())
    assert r.status_code == 401
    meta.send_text.assert_not_called()


async def test_validation_error_returns_422(client: AsyncClient) -> None:
    body = _body()
    del body["debtor_id"]
    r = await client.post("/case/handoff", json=body, headers=AUTH)
    assert r.status_code == 422


async def test_invalid_phone_format_returns_422(client: AsyncClient, meta: MagicMock) -> None:
    r = await client.post("/case/handoff", json=_body(phone="abc"), headers=AUTH)
    assert r.status_code == 422
    meta.send_text.assert_not_called()


async def test_rate_limited_returns_429(
    client: AsyncClient, meta: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.webhooks import handoff

    async def _blocked(*args: Any, **kwargs: Any) -> Any:
        return type("_RL", (), {"allowed": False, "scope": "phone"})()

    monkeypatch.setattr(handoff, "check_rate_limit", _blocked)
    r = await client.post("/case/handoff", json=_body(), headers=AUTH)
    assert r.status_code == 429
    meta.send_text.assert_not_called()


async def test_happy_path_creates_case_and_sends_message(
    client: AsyncClient, meta: MagicMock, session: _FakeSession
) -> None:
    r = await client.post("/case/handoff", json=_body(), headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"case_id": CASE_ID, "sent": True}

    meta.send_text.assert_awaited_once_with(
        PHONE, "Hola, vi que hablaste con nuestro asistente de voz."
    )
    assert session.commits == 1
    case = session.added[0]
    assert case.case_id == CASE_ID
    assert case.phone == PHONE
    assert case.poliza_id == "POL-000123"
    assert case.debtor_id == "dpg-deudor-123"
    assert case.call_ids == ["twilio-CAxxxx"]
    assert case.status == "awaiting_receipt"


async def test_no_message_creates_case_without_sending(
    client: AsyncClient, meta: MagicMock, session: _FakeSession
) -> None:
    r = await client.post("/case/handoff", json=_body(message=None), headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"case_id": CASE_ID, "sent": False}
    meta.send_text.assert_not_called()
    assert session.commits == 1


async def test_no_call_id_leaves_call_ids_empty(client: AsyncClient, session: _FakeSession) -> None:
    await client.post("/case/handoff", json=_body(call_id=None), headers=AUTH)
    assert session.added[0].call_ids == []


async def test_idempotent_second_call_skips_send(
    client: AsyncClient, meta: MagicMock, session: _FakeSession
) -> None:
    r1 = await client.post("/case/handoff", json=_body(), headers=AUTH)
    r2 = await client.post("/case/handoff", json=_body(), headers=AUTH)

    assert r1.json()["sent"] is True
    assert r2.status_code == 200
    assert r2.json()["sent"] is False
    assert meta.send_text.call_count == 1
    assert len(session.added) == 1


async def test_same_case_new_message_is_sent(
    client: AsyncClient, meta: MagicMock, session: _FakeSession
) -> None:
    """VOICE reusa el case_id del deudor en cada llamada: un mensaje nuevo del
    mismo caso debe enviarse (antes se descartaba en silencio)."""
    await client.post("/case/handoff", json=_body(), headers=AUTH)
    r2 = await client.post(
        "/case/handoff", json=_body(call_id="CA-2", message="Otro mensaje"), headers=AUTH
    )

    assert r2.json()["sent"] is True
    assert meta.send_text.call_count == 2
    assert len(session.added) == 1
    assert session.added[0].call_ids == ["twilio-CAxxxx", "CA-2"]


async def test_thread_kept_for_same_poliza_reset_for_other(
    client: AsyncClient, session: _FakeSession
) -> None:
    """Dos handoffs de la misma póliza no borran el hilo; otra póliza sí."""
    state: dict[str, Any] = {"channel_values": {}}
    deletes: list[str] = []

    async def _aget(cfg: Any) -> Any:
        return state if state["channel_values"] else None

    async def _adelete(tid: str) -> None:
        deletes.append(tid)
        state["channel_values"] = {}

    async def _aupdate(cfg: Any, values: Any, as_node: Any = None) -> None:
        state["channel_values"].update(values)

    app = client._transport.app  # type: ignore[attr-defined]
    app.state.checkpointer = MagicMock(aget=_aget, adelete_thread=_adelete)
    app.state.qa_graph = MagicMock(aupdate_state=_aupdate)

    await client.post("/case/handoff", json=_body(), headers=AUTH)
    await client.post("/case/handoff", json=_body(call_id="CA-2", message="m2"), headers=AUTH)
    assert deletes == []
    assert state["channel_values"]["handoff_numero_poliza"] == "POL-000123"

    await client.post(
        "/case/handoff", json=_body(call_id="CA-3", poliza_number="POL-999"), headers=AUTH
    )
    assert deletes == [PHONE]
    assert state["channel_values"]["handoff_numero_poliza"] == "POL-999"


async def test_poliza_na_no_se_siembra(client: AsyncClient) -> None:
    """VOICE sin número de póliza manda "N/A": no debe llegar al saludo."""
    seeded: dict[str, Any] = {}

    async def _aupdate(cfg: Any, values: Any, as_node: Any = None) -> None:
        seeded.update(values)

    app = client._transport.app  # type: ignore[attr-defined]
    app.state.qa_graph = MagicMock(aupdate_state=_aupdate)

    await client.post(
        "/case/handoff", json=_body(poliza_number="N/A", documento="1022446283"), headers=AUTH
    )
    assert "handoff_numero_poliza" not in seeded
    assert "handoff_poliza_hint" not in seeded
    assert seeded["cliente_doc"] == "1022446283"
