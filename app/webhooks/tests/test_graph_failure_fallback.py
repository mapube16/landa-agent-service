"""El fallback por excepción del grafo debe alertar al equipo, no solo al cliente.

Incidente 20-oct → 30-oct: OpenRouter devolvía 401 en cada turno; el cliente
recibía "tuvimos un inconveniente" y cartera ninguna alerta.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_fallback_envia_texto_y_alerta_al_equipo() -> None:
    from app.webhooks import meta as meta_mod

    app_state = MagicMock()
    app_state.meta.send_text = AsyncMock()
    app_state.chatwoot = MagicMock()

    with patch("app.features.escalation.alerts.notify_team_escalation", new=AsyncMock()) as notify:
        await meta_mod._graph_failure_fallback(app_state, "+573001234567", None)

    app_state.meta.send_text.assert_awaited_once()
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["reason"] == "graph_error"
    assert notify.await_args.kwargs["client_phone"] == "+573001234567"


@pytest.mark.asyncio
async def test_fallback_nunca_lanza_si_la_alerta_falla() -> None:
    from app.webhooks import meta as meta_mod

    app_state = MagicMock()
    app_state.meta.send_text = AsyncMock()

    with patch(
        "app.features.escalation.alerts.notify_team_escalation",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await meta_mod._graph_failure_fallback(app_state, "+573001234567", None)

    app_state.meta.send_text.assert_awaited_once()
