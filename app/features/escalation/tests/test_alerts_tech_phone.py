"""Las fallas técnicas (graph_error) van al operador, no a cartera."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.features.escalation.alerts import notify_team_escalation


@pytest.fixture()
def phones(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import settings

    monkeypatch.setattr(
        type(settings.payment),
        "cartera_phone_allowlist",
        property(lambda self: frozenset({"+573146316003"})),
    )
    monkeypatch.setattr(settings.payment, "tech_alert_phone", "+573001112233")


async def _alert(reason: str) -> AsyncMock:
    meta = AsyncMock()
    await notify_team_escalation(meta, None, client_phone="573009998877", reason=reason)
    meta.send_template.assert_awaited_once()
    return meta


async def test_graph_error_va_al_operador(phones: Any) -> None:
    meta = await _alert("graph_error")
    assert meta.send_template.await_args.args[0] == "+573001112233"
    assert "FALLA TÉCNICA" in meta.send_template.await_args.kwargs["body_params"][1]


async def test_escalacion_normal_sigue_a_cartera(phones: Any) -> None:
    meta = await _alert("escape_hatch")
    assert meta.send_template.await_args.args[0] == "+573146316003"


async def test_sin_tech_phone_cae_a_cartera(phones: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import settings

    monkeypatch.setattr(settings.payment, "tech_alert_phone", "")
    meta = await _alert("graph_error")
    assert meta.send_template.await_args.args[0] == "+573146316003"
