"""Tests del barrido ``sin-respuesta`` (worker.mark_unanswered_conversations).

Regla de negocio (petición DPG 30-jul): una conversación NO es "sin respuesta"
por existir — solo si pasaron ~5h y el cliente nunca escribió. Antes lo hacía
una automatización de Chatwoot al crearla, y eso escondía los hilos del equipo
e inflaba la métrica conv_sin_respuesta.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest  # type: ignore[import-not-found]

from app.worker import UNANSWERED_AFTER_HOURS, mark_unanswered_conversations

VIEJA = time.time() - (UNANSWERED_AFTER_HOURS + 1) * 3600
RECIENTE = time.time() - 600  # 10 minutos


def _cw(convs: list[dict[str, Any]], msgs: list[dict[str, Any]] | None = None) -> MagicMock:
    cw = MagicMock()
    cw.list_conversations = AsyncMock(return_value=convs)
    cw.list_messages = AsyncMock(return_value=msgs or [])
    cw.add_labels = AsyncMock()
    cw.snooze = AsyncMock()
    return cw


async def _run(monkeypatch: Any, cw: MagicMock) -> None:
    monkeypatch.setattr("app.integrations.chatwoot.get_chatwoot_client", lambda: cw)
    await mark_unanswered_conversations({})


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_marca_la_que_lleva_horas_sin_respuesta(monkeypatch: Any) -> None:
    cw = _cw(
        [{"id": 94, "status": "snoozed", "created_at": VIEJA, "labels": []}],
        msgs=[{"message_type": 1}],  # solo saliente: el cliente nunca escribió
    )
    await _run(monkeypatch, cw)
    cw.add_labels.assert_awaited_once_with(94, ["sin-respuesta"])
    cw.snooze.assert_awaited_once_with(94)


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_no_marca_la_recien_creada(monkeypatch: Any) -> None:
    """El bug original: marcaba y escondía el hilo al instante de crearlo."""
    cw = _cw([{"id": 95, "status": "open", "created_at": RECIENTE, "labels": []}])
    await _run(monkeypatch, cw)
    cw.add_labels.assert_not_awaited()
    cw.snooze.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_no_marca_si_el_cliente_respondio(monkeypatch: Any) -> None:
    cw = _cw(
        [{"id": 96, "status": "open", "created_at": VIEJA, "labels": []}],
        msgs=[{"message_type": 1}, {"message_type": 0}],  # 0 = entrante
    )
    await _run(monkeypatch, cw)
    cw.add_labels.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_idempotente_y_salta_resueltas(monkeypatch: Any) -> None:
    cw = _cw(
        [
            {"id": 97, "status": "open", "created_at": VIEJA, "labels": ["sin-respuesta"]},
            {"id": 98, "status": "resolved", "created_at": VIEJA, "labels": []},
        ]
    )
    await _run(monkeypatch, cw)
    cw.add_labels.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_un_fallo_no_aborta_el_barrido(monkeypatch: Any) -> None:
    """Si una conversación falla, las demás se siguen procesando."""
    cw = _cw(
        [
            {"id": 99, "status": "open", "created_at": VIEJA, "labels": []},
            {"id": 100, "status": "open", "created_at": VIEJA, "labels": []},
        ]
    )
    cw.list_messages = AsyncMock(side_effect=[RuntimeError("boom"), []])
    await _run(monkeypatch, cw)
    cw.add_labels.assert_awaited_once_with(100, ["sin-respuesta"])
