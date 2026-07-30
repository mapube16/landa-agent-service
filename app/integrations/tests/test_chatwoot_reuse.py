"""Tests para el reuso de conversaciones de Chatwoot.

Bug real (30-jul): la automatización "Plantilla sin respuesta" pospone
(``snooze``) cada conversación al crearla. El buscador exigía ``status ==
"open"``, nunca encontraba el hilo existente y creaba uno NUEVO por cada
handoff — el cliente JULIAN terminó con 3 hilos (#92, #93, #94) en 5 minutos.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest  # type: ignore[import-not-found]


def _response(payload: list[dict[str, Any]]) -> httpx.Response:
    request = httpx.Request("GET", "http://test/x")
    return httpx.Response(200, json={"payload": payload}, request=request)


def _client(payload: list[dict[str, Any]]) -> Any:
    from app.integrations.chatwoot import ChatwootClient

    c = ChatwootClient.__new__(ChatwootClient)  # sin __init__: no red, no settings
    c._account_id = 1  # type: ignore[attr-defined]
    http = MagicMock()
    http.get = AsyncMock(return_value=_response(payload))
    c._http = http  # type: ignore[attr-defined]
    return c


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_reusa_conversacion_pospuesta() -> None:
    """El caso del bug: 3 hilos snoozed → reusa el más reciente, no crea otro."""
    c = _client(
        [
            {"id": 94, "status": "snoozed", "created_at": 300},
            {"id": 93, "status": "snoozed", "created_at": 200},
            {"id": 92, "status": "snoozed", "created_at": 100},
        ]
    )
    assert await c._find_reusable_conversation(74) == 94


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_prefiere_abierta_sobre_pospuesta() -> None:
    """Si hay una abierta, gana aunque sea más vieja que una pospuesta."""
    c = _client(
        [
            {"id": 94, "status": "snoozed", "created_at": 300},
            {"id": 90, "status": "open", "created_at": 100},
        ]
    )
    assert await c._find_reusable_conversation(74) == 90


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_resuelta_no_se_reusa() -> None:
    """Un caso cerrado NO se reabre: ahí sí corresponde un hilo nuevo."""
    c = _client([{"id": 80, "status": "resolved", "created_at": 100}])
    assert await c._find_reusable_conversation(74) is None


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_sin_conversaciones_devuelve_none() -> None:
    c = _client([])
    assert await c._find_reusable_conversation(74) is None
