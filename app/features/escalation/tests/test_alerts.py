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


async def test_alert_sends_template_to_cartera(cartera_settings: Any) -> None:
    """Template-first: UTILITY template delivers regardless of the 24h window."""
    meta = AsyncMock()
    redis = AsyncMock()
    redis.set.return_value = True  # first alert for this client

    await notify_team_escalation(
        meta, redis, client_phone="573123528153", reason="escape_hatch", cliente_nombre="Jaime"
    )

    meta.send_template.assert_awaited_once()
    args = meta.send_template.await_args.args
    assert args[0] == "+573146316003"
    assert args[1] == "alerta_atencion_humana"
    assert "Jaime (+573123528153)" in meta.send_template.await_args.kwargs["body_params"][0]
    meta.send_text.assert_not_called()
    # Dedupe key registered with the 30-min TTL.
    dedupe_call = redis.set.await_args
    assert dedupe_call.args[0] == b"alert:escalation:+573123528153"
    assert dedupe_call.kwargs["ex"] == ALERT_DEDUPE_SECONDS


async def test_alert_falls_back_to_text_when_template_unavailable(
    cartera_settings: Any,
) -> None:
    """Template pending approval / rejected → free text still goes out."""
    meta = AsyncMock()
    meta.send_template.side_effect = RuntimeError("template not found")
    redis = AsyncMock()
    redis.set.return_value = True

    await notify_team_escalation(
        meta, redis, client_phone="573123528153", reason="escape_hatch", cliente_nombre="Jaime"
    )

    meta.send_text.assert_awaited_once()
    kwargs = meta.send_text.await_args.kwargs
    assert kwargs["to"] == "+573146316003"
    assert "Jaime" in kwargs["body"]
    assert "pidió hablar con una persona" in kwargs["body"]


async def test_alert_posts_chatwoot_private_note(cartera_settings: Any) -> None:
    """Escalation also drops a private note inside the Chatwoot conversation."""
    meta = AsyncMock()
    redis = AsyncMock()
    redis.set.return_value = True
    chatwoot = AsyncMock()
    chatwoot.get_or_create_conversation.return_value = 55

    await notify_team_escalation(
        meta,
        redis,
        client_phone="573123528153",
        reason="escape_hatch",
        cliente_nombre="Jaime",
        chatwoot=chatwoot,
    )

    chatwoot.post_private_note.assert_awaited_once()
    conv_id, note = chatwoot.post_private_note.await_args.args
    assert conv_id == 55
    assert "Jaime" in note and "atención humana" in note


async def test_alert_note_posts_even_when_deduped(cartera_settings: Any) -> None:
    """The dedupe gate throttles the WhatsApp blast, not the Chatwoot note."""
    meta = AsyncMock()
    redis = AsyncMock()
    redis.set.return_value = None  # already alerted recently → WhatsApp skipped
    chatwoot = AsyncMock()
    chatwoot.get_or_create_conversation.return_value = 55

    await notify_team_escalation(meta, redis, client_phone="+573123528153", chatwoot=chatwoot)

    chatwoot.post_private_note.assert_awaited_once()
    meta.send_template.assert_not_called()
    meta.send_text.assert_not_called()


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
