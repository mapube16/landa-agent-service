"""Tests del barrido de etapas (features/siniestros/sync.py).

Lo critico: en modo silencioso (``apply=False``, el default) NO se escribe una
sola etiqueta en Chatwoot. Ese modo existe para contrastar la lectura del
modelo contra la realidad antes de dejarlo mover datos con los que DPG decide.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.features.siniestros.stages import StageRead
from app.features.siniestros.sync import SISMO_LABEL, sync_stages


def _conv(cid: int, labels: list[str]) -> dict[str, Any]:
    return {"id": cid, "labels": labels}


def _client(convs: list[dict[str, Any]]) -> MagicMock:
    c = MagicMock()
    c.list_conversations_by_label = AsyncMock(return_value=convs)
    c.list_messages = AsyncMock(return_value=[{"content": "ya vino el perito", "message_type": 0}])
    c.set_stage_label = AsyncMock()
    return c


def _patch(monkeypatch: pytest.MonkeyPatch, client: MagicMock, leida: StageRead) -> None:
    monkeypatch.setattr("app.features.siniestros.sync.get_chatwoot_client", lambda: client)
    monkeypatch.setattr("app.features.siniestros.sync.read_stage", AsyncMock(return_value=leida))


@pytest.mark.asyncio
async def test_modo_silencioso_no_escribe(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([_conv(1, [SISMO_LABEL, "siniestro-aviso"])])
    _patch(monkeypatch, c, StageRead(etapa="perito", confianza="alta"))

    rep = await sync_stages()  # default apply=False

    c.set_stage_label.assert_not_called()
    assert rep.avanzadas == 1  # lo reporta, pero no lo aplica
    assert rep.movimientos[0]["de"] == "aviso"
    assert rep.movimientos[0]["a"] == "perito"


@pytest.mark.asyncio
async def test_con_apply_escribe_la_etapa(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([_conv(1, [SISMO_LABEL, "siniestro-aviso"])])
    _patch(monkeypatch, c, StageRead(etapa="perito", confianza="alta"))

    await sync_stages(apply=True)

    c.set_stage_label.assert_awaited_once_with(1, "siniestro-perito", "siniestro-")


@pytest.mark.asyncio
async def test_solo_toca_las_del_sismo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una conversacion de cartera no entra al barrido aunque haya avance."""
    c = _client([_conv(1, ["promesa-pago"]), _conv(2, [SISMO_LABEL])])
    _patch(monkeypatch, c, StageRead(etapa="aviso", confianza="alta"))

    rep = await sync_stages(apply=True)

    assert rep.revisadas == 1
    c.set_stage_label.assert_awaited_once_with(2, "siniestro-aviso", "siniestro-")


@pytest.mark.asyncio
async def test_retroceso_no_se_aplica(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([_conv(1, [SISMO_LABEL, "siniestro-ofrecimiento"])])
    _patch(monkeypatch, c, StageRead(etapa="aviso", confianza="alta"))

    rep = await sync_stages(apply=True)

    c.set_stage_label.assert_not_called()
    assert rep.sin_cambio == 1


@pytest.mark.asyncio
async def test_una_conversacion_rota_no_aborta_el_barrido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _client([_conv(1, [SISMO_LABEL]), _conv(2, [SISMO_LABEL])])
    c.list_messages = AsyncMock(
        side_effect=[RuntimeError("timeout"), [{"content": "x", "message_type": 0}]]
    )
    _patch(monkeypatch, c, StageRead(etapa="aviso", confianza="alta"))

    rep = await sync_stages(apply=True)

    assert rep.errores == 1
    assert rep.avanzadas == 1  # la segunda si se proceso


@pytest.mark.asyncio
async def test_chatwoot_caido_devuelve_reporte_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([])
    c.list_conversations_by_label = AsyncMock(side_effect=RuntimeError("caido"))
    _patch(monkeypatch, c, StageRead())

    rep = await sync_stages(apply=True)

    assert rep.revisadas == 0
    c.set_stage_label.assert_not_called()


@pytest.mark.asyncio
async def test_sin_mensajes_del_cliente_no_llama_al_modelo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hilos donde solo hablo el bot: no hay relato, no se gasta una llamada."""
    c = _client([_conv(1, [SISMO_LABEL])])
    c.list_messages = AsyncMock(return_value=[{"content": "plantilla", "message_type": 1}])
    leer = AsyncMock(return_value=StageRead(etapa="pago", confianza="alta"))
    monkeypatch.setattr("app.features.siniestros.sync.get_chatwoot_client", lambda: c)
    monkeypatch.setattr("app.features.siniestros.sync.read_stage", leer)

    rep = await sync_stages(apply=True)

    leer.assert_not_awaited()
    c.set_stage_label.assert_not_called()
    assert rep.sin_cambio == 1


@pytest.mark.asyncio
async def test_cuenta_bloqueos_para_el_informe(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([_conv(1, [SISMO_LABEL]), _conv(2, [SISMO_LABEL])])
    _patch(monkeypatch, c, StageRead(etapa="perito", bloqueo="aseguradora", confianza="alta"))

    rep = await sync_stages()

    assert rep.bloqueos == {"aseguradora": 2}
