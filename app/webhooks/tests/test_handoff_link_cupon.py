"""Tests for POST /case/link_cupon — confirmación escrita de link/cupón (§7).

Antes de este endpoint, ARIA prometía el cupón por voz y el cliente no recibía
nada por WhatsApp hasta que un humano se acordara. Lo que importa aquí: que
salga la plantilla correcta con el tipo bien redactado (cupón/link).
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


@pytest.fixture  # type: ignore[untyped-decorator]
def meta() -> MagicMock:
    m = MagicMock()
    m.send_template = AsyncMock(return_value="wamid.tpl1")
    return m


@pytest.fixture  # type: ignore[untyped-decorator]
async def client(meta: MagicMock) -> AsyncIterator[AsyncClient]:
    from fastapi import FastAPI

    from app.webhooks.handoff import router

    app = FastAPI()
    app.include_router(router)
    app.state.meta = meta
    app.state.redis = MagicMock()  # fail-open del rate limiter, igual que no_answer

    @asynccontextmanager
    async def factory() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_envia_plantilla_con_nombre_y_tipo(client: AsyncClient, meta: MagicMock) -> None:
    r = await client.post("/case/link_cupon", json=_body(tipo="cupon"), headers=AUTH)
    assert r.status_code == 200
    assert r.json()["sent"] is True

    meta.send_template.assert_awaited_once()
    args, kwargs = meta.send_template.await_args
    assert args[0] == PHONE
    assert args[1] == "solicitud_link_cupon"
    # El cliente lee "cupón" (con tilde), no el enum interno "cupon".
    assert kwargs["body_params"] == ["Juan", "cupón"]


async def test_tipo_link_es_el_default(client: AsyncClient, meta: MagicMock) -> None:
    r = await client.post("/case/link_cupon", json=_body(), headers=AUTH)
    assert r.status_code == 200
    assert meta.send_template.await_args.kwargs["body_params"] == ["Juan", "link"]


async def test_sin_bearer_rechaza(client: AsyncClient, meta: MagicMock) -> None:
    r = await client.post("/case/link_cupon", json=_body())
    assert r.status_code == 401
    meta.send_template.assert_not_awaited()


async def test_tipo_invalido_rechaza(client: AsyncClient, meta: MagicMock) -> None:
    """Un tipo fuera del enum no puede llegar a la plantilla."""
    r = await client.post("/case/link_cupon", json=_body(tipo="transferencia"), headers=AUTH)
    assert r.status_code == 422
    meta.send_template.assert_not_awaited()
