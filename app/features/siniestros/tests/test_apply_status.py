"""Tests de ``apply_status``: la respuesta del cliente mueve la etiqueta.

Se protege: solo hilos con marca de campana; el estado anterior se quita; un
fallo de Chatwoot o del modelo no propaga al webhook.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.features.siniestros.routing import (
    DETECCION_LABEL,
    ESCALADO_LABEL,
    SISMO_LABEL,
    apply_status,
)


def _client(labels: list[str]) -> MagicMock:
    c = MagicMock()
    c.get_or_create_conversation = AsyncMock(return_value=77)
    c.get_labels = AsyncMock(return_value=labels)
    c.replace_labels = AsyncMock()
    return c


def _patch(monkeypatch: pytest.MonkeyPatch, client: MagicMock, label: str | None) -> None:
    monkeypatch.setattr("app.integrations.chatwoot.get_chatwoot_client", lambda: client)
    monkeypatch.setattr("app.features.siniestros.routing.classify", AsyncMock(return_value=label))


def _sent(client: MagicMock) -> list[str]:
    return list(client.replace_labels.call_args.args[1])


@pytest.mark.asyncio
async def test_seguimiento_aplica_y_reemplaza(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([SISMO_LABEL, "radicada-esperando"])
    _patch(monkeypatch, c, "indemnizada")

    assert await apply_status("+573001112233", "ya me pagaron") == "indemnizada"
    labels = _sent(c)
    assert "indemnizada" in labels
    assert "radicada-esperando" not in labels
    assert SISMO_LABEL in labels


@pytest.mark.asyncio
async def test_deteccion_afectado_escala(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([DETECCION_LABEL])
    _patch(monkeypatch, c, "afectado-sin-reportar")

    await apply_status("+573001112233", "se me cayo el techo")
    assert ESCALADO_LABEL in _sent(c)


@pytest.mark.asyncio
async def test_hilo_sin_marca_no_se_toca(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(["promesa-pago"])
    clasificar = AsyncMock(return_value="indemnizada")
    monkeypatch.setattr("app.integrations.chatwoot.get_chatwoot_client", lambda: c)
    monkeypatch.setattr("app.features.siniestros.routing.classify", clasificar)

    assert await apply_status("+573001112233", "ya me pagaron") is None
    clasificar.assert_not_awaited()  # ni siquiera gasta la llamada al modelo
    c.replace_labels.assert_not_called()


@pytest.mark.asyncio
async def test_sin_clasificacion_no_toca_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([SISMO_LABEL])
    _patch(monkeypatch, c, None)

    assert await apply_status("+573001112233", "buenos dias") is None
    c.replace_labels.assert_not_called()


@pytest.mark.asyncio
async def test_chatwoot_caido_no_propaga(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([SISMO_LABEL])
    c.get_or_create_conversation = AsyncMock(side_effect=RuntimeError("caido"))
    _patch(monkeypatch, c, "indemnizada")

    assert await apply_status("+573001112233", "ya me pagaron") is None
