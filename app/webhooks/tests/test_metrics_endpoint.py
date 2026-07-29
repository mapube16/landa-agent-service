"""Tests for GET /metrics/daily — auth + audit/label aggregation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

TOKEN = "test-lambda-token"  # matches app/conftest.py placeholder
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class _Rows:
    def __init__(self, rows: list[tuple[str, int]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, int]]:
        return self._rows


class _Session:
    def __init__(self, rows: list[tuple[str, int]]) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any) -> _Rows:
        return _Rows(self._rows)


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    from fastapi import FastAPI

    from app.webhooks.metrics import router

    app = FastAPI()
    app.include_router(router)

    @asynccontextmanager
    async def factory() -> AsyncIterator[_Session]:
        yield _Session([("attachment_received", 3), ("escalation", 2)])

    app.state.session_factory = factory
    chatwoot = MagicMock()
    chatwoot.list_conversations = AsyncMock(
        return_value=[
            {"id": 1, "labels": ["sin-respuesta"]},
            {"id": 2, "labels": ["en-conversacion", "comprobante-recibido"]},
            {"id": 3, "labels": ["escalado-humano"]},
        ]
    )
    app.state.chatwoot = chatwoot
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_requires_bearer(client: AsyncClient) -> None:
    r = await client.get("/metrics/daily")
    assert r.status_code == 401


async def test_bad_day_is_422(client: AsyncClient) -> None:
    r = await client.get("/metrics/daily?day=not-a-date", headers=AUTH)
    assert r.status_code == 422


async def test_aggregates_audit_and_labels(client: AsyncClient) -> None:
    r = await client.get("/metrics/daily?day=2026-07-29", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["comprobantes_recibidos"] == 3  # from audit
    assert body["escalaciones"] == 2
    assert body["conversaciones_total"] == 3  # from chatwoot
    assert body["conv_sin_respuesta"] == 1
    assert body["conv_con_comprobante"] == 1
    assert body["canal"] == "whatsapp"
    assert body["fecha"] == "2026-07-29"


async def test_audit_flag_runs_quality_auditor(client: AsyncClient, monkeypatch: Any) -> None:
    """audit=true adds anomaly counts from the LLM auditor over active convs."""
    from app.features.metrics.quality import QualityRubric

    app_state = client._transport.app.state  # type: ignore[attr-defined]
    # conv 2 had client activity → gets audited; give it messages
    app_state.chatwoot.list_messages = AsyncMock(
        return_value=[{"message_type": 0, "content": "ya pagué"}]
    )

    async def _fake_audit(_msgs: Any) -> QualityRubric:
        return QualityRubric(
            intento_certificar_pago=True,
            lista_rota=False,
            loop_sin_salida=False,
            cliente_frustrado=False,
            rationale="x",
        )

    # The endpoint imports audit_conversation from this module at call time,
    # so patch it at the source.
    import app.features.metrics.quality as q_mod

    monkeypatch.setattr(q_mod, "audit_conversation", _fake_audit)

    r = await client.get("/metrics/daily?day=2026-07-29&audit=true", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert "anom_intento_certificar_pago" in body
    assert body["conversaciones_auditadas"] >= 1


async def test_chatwoot_down_still_returns_audit(client: AsyncClient) -> None:
    """A Chatwoot outage must not 500 the endpoint — audit metrics still serve."""
    client._transport.app.state.chatwoot.list_conversations.side_effect = RuntimeError("down")  # type: ignore[attr-defined]
    r = await client.get("/metrics/daily?day=2026-07-29", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["comprobantes_recibidos"] == 3
    assert r.json()["conversaciones_total"] == 0
