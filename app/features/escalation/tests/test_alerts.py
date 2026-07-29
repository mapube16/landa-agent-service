"""Tests for the escalation WhatsApp alert (features/escalation/alerts.py)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.features.escalation.alerts import ALERT_DEDUPE_SECONDS, notify_team_escalation


@pytest.fixture()
def cartera_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import settings

    monkeypatch.setattr(
        type(settings.payment),
        "cartera_phone_allowlist",
        property(lambda self: frozenset({"+573146316003"})),
    )


async def test_alert_sends_to_cartera(cartera_settings: Any) -> None:
    meta = AsyncMock()
    redis = AsyncMock()
    redis.set.return_value = True  # first alert for this client

    await notify_team_escalation(
        meta, redis, client_phone="573123528153", reason="escape_hatch", cliente_nombre="Jaime"
    )

    meta.send_text.assert_awaited_once()
    kwargs = meta.send_text.await_args.kwargs
    assert kwargs["to"] == "+573146316003"
    assert "Jaime" in kwargs["body"]
    assert "+573123528153" in kwargs["body"]
    assert "pidió hablar con una persona" in kwargs["body"]
    # Dedupe key registered with the 30-min TTL.
    dedupe_call = redis.set.await_args
    assert dedupe_call.args[0] == b"alert:escalation:+573123528153"
    assert dedupe_call.kwargs["ex"] == ALERT_DEDUPE_SECONDS


async def test_alert_deduped_within_window(cartera_settings: Any) -> None:
    meta = AsyncMock()
    redis = AsyncMock()
    redis.set.return_value = None  # SET NX says: already alerted recently

    await notify_team_escalation(meta, redis, client_phone="+573123528153")

    meta.send_text.assert_not_called()


async def test_alert_fail_open_on_errors(cartera_settings: Any) -> None:
    meta = AsyncMock()
    meta.send_text.side_effect = RuntimeError("meta down")
    redis = AsyncMock()
    redis.set.side_effect = RuntimeError("redis down")

    # Neither failure raises — escalation flow must never break on the alert.
    await notify_team_escalation(meta, redis, client_phone="+573123528153")


async def test_alert_skips_when_no_cartera(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.settings import settings

    monkeypatch.setattr(
        type(settings.payment),
        "cartera_phone_allowlist",
        property(lambda self: frozenset()),
    )
    meta = MagicMock()
    await notify_team_escalation(meta, None, client_phone="+573123528153")
    meta.send_text.assert_not_called()
