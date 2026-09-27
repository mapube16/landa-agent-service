"""Tests para ``set_stage_label``: las etapas de un siniestro son excluyentes.

Un caso está en perito O en ofrecimiento, nunca en ambas. ``add_labels`` solo
suma, así que una conversación que avanza acumularía las 6 etapas y la vista
filtrada de DPG mostraría el mismo caso en todas las columnas.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest  # type: ignore[import-not-found]

PREFIX = "siniestro-"


def _client(current: list[str]) -> tuple[Any, MagicMock]:
    from app.integrations.chatwoot import ChatwootClient

    c = ChatwootClient.__new__(ChatwootClient)  # sin __init__: no red, no settings
    c._account_id = 1  # type: ignore[attr-defined]
    request = httpx.Request("GET", "http://test/x")
    http = MagicMock()
    http.get = AsyncMock(
        return_value=httpx.Response(200, json={"payload": current}, request=request)
    )
    http.post = AsyncMock(return_value=httpx.Response(200, json={}, request=request))
    c._http = http  # type: ignore[attr-defined]
    return c, http


def _sent(http: MagicMock) -> list[str]:
    return list(http.post.call_args.kwargs["json"]["labels"])


@pytest.mark.asyncio
async def test_reemplaza_la_etapa_anterior() -> None:
    c, http = _client(["siniestro-sismo", "siniestro-aviso"])
    await c.set_stage_label(7, "siniestro-perito", PREFIX)
    labels = _sent(http)
    assert "siniestro-aviso" not in labels  # la etapa vieja se fue
    assert "siniestro-perito" in labels


@pytest.mark.asyncio
async def test_conserva_etiquetas_de_otro_prefijo() -> None:
    c, http = _client(["escalado-humano", "promesa-pago", "siniestro-aviso"])
    await c.set_stage_label(7, "siniestro-reclamacion", PREFIX)
    labels = _sent(http)
    assert "escalado-humano" in labels
    assert "promesa-pago" in labels
    assert labels.count("siniestro-reclamacion") == 1


@pytest.mark.asyncio
async def test_reaplicar_la_misma_etapa_no_duplica() -> None:
    c, http = _client(["siniestro-perito"])
    await c.set_stage_label(7, "siniestro-perito", PREFIX)
    assert _sent(http) == ["siniestro-perito"]


@pytest.mark.asyncio
async def test_sin_etiquetas_previas() -> None:
    c, http = _client([])
    await c.set_stage_label(7, "siniestro-aviso", PREFIX)
    assert _sent(http) == ["siniestro-aviso"]


@pytest.mark.asyncio
async def test_si_falla_el_get_igual_aplica_la_etapa() -> None:
    """Fail-open: perder las etiquetas viejas es mejor que no marcar la etapa."""
    c, http = _client([])
    http.get = AsyncMock(side_effect=httpx.ConnectError("caido"))
    await c.set_stage_label(7, "siniestro-pago", PREFIX)
    assert _sent(http) == ["siniestro-pago"]
