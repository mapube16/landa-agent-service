"""Tests for POST /webhooks/chatwoot (Plan 04-03, D-15/D-16/D-17/D-18).

Minimal FastAPI app with only the chatwoot router; ``app.state`` carries
AsyncMock stand-ins for meta / chatwoot / redis. No live infrastructure.

Covers: HMAC reject (missing + bad), event/message_type/sender filters,
dedup by message id, text relay via inverse index, image attachment
re-upload + send_media.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest  # type: ignore[import-not-found]
from httpx import ASGITransport, AsyncClient

WEBHOOK_SECRET = "test-cw-webhook-secret"  # noqa: S105 — matches app/conftest.py placeholder


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _payload(**overrides: Any) -> bytes:
    base: dict[str, Any] = {
        "event": "message_created",
        "id": 12345,
        "content": "Hola",
        "message_type": "outgoing",
        "content_type": "text",
        "conversation": {"id": 42, "status": "open"},
        "sender": {"id": 7, "name": "Agente Juan", "type": "user"},
        "attachments": [],
    }
    base.update(overrides)
    return json.dumps(base).encode("utf-8")


@pytest.fixture  # type: ignore[untyped-decorator]
def mocks() -> tuple[MagicMock, MagicMock, MagicMock]:
    meta = MagicMock()
    meta.send_text = AsyncMock(return_value="wamid.out1")
    meta.upload_media = AsyncMock(return_value="MEDIA_ID_1")
    meta.send_media = AsyncMock(return_value="wamid.out2")

    chatwoot = MagicMock()
    chatwoot.get_phone_by_conv = AsyncMock(return_value="+573001")
    chatwoot.download_attachment = AsyncMock(return_value=b"\xff\xd8\xff\xe0fakejpeg")

    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)  # default: first-see
    redis.delete = AsyncMock(return_value=1)
    return meta, chatwoot, redis


@pytest.fixture  # type: ignore[untyped-decorator]
async def client(mocks: tuple[MagicMock, MagicMock, MagicMock]) -> AsyncIterator[AsyncClient]:
    from fastapi import FastAPI

    from app.webhooks.chatwoot import router

    app = FastAPI()
    app.include_router(router)
    meta, chatwoot, redis = mocks
    app.state.meta = meta
    app.state.chatwoot = chatwoot
    app.state.redis = redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_rejects_missing_signature(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    r = await client.post("/webhooks/chatwoot", content=_payload())
    assert r.status_code == 401


async def test_rejects_bad_signature(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    r = await client.post(
        "/webhooks/chatwoot",
        content=_payload(),
        headers={"X-Chatwoot-Signature": "sha256=" + "0" * 64},
    )
    assert r.status_code == 401


async def test_ignores_non_message_event(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    meta, _, _ = mocks
    body = _payload(event="conversation_created")
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    meta.send_text.assert_not_called()


async def test_ignores_incoming(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    meta, _, _ = mocks
    body = _payload(message_type="incoming")
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    meta.send_text.assert_not_called()


async def test_ignores_agent_bot(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """Loop prevention (D-15): bot mirror messages never relay back to the client."""
    meta, _, _ = mocks
    body = _payload(sender={"id": 1, "name": "landa-bot", "type": "agent_bot"})
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    meta.send_text.assert_not_called()


async def test_private_note_never_relays(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """Private notes (outgoing + private=true) must NEVER reach the client."""
    meta, _, _ = mocks
    body = _payload(private=True)
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    assert r.json() == {"ignored": "private_note"}
    meta.send_text.assert_not_called()


async def test_relay_mutes_bot(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """A human agent reply mutes the bot for that phone (human takeover)."""
    _, chatwoot, redis = mocks
    chatwoot.get_phone_by_conv.return_value = "+573001112233"
    body = _payload()
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    mute_sets = [
        c for c in redis.set.await_args_list if c.args and c.args[0].startswith(b"bot:muted:")
    ]
    assert len(mute_sets) == 1
    assert mute_sets[0].args[0] == b"bot:muted:+573001112233"
    # Human reply is a hard mute (no grace auto-recovery).
    assert mute_sets[0].args[1].startswith(b"human:")


async def test_conversation_resolved_unmutes_bot(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """Agent hitting 'Resolver' clears the mute so the bot re-activates."""
    meta, chatwoot, redis = mocks
    chatwoot.get_phone_by_conv.return_value = "+573001112233"
    body = _payload(event="conversation_resolved", id=42)
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": "unmuted"}
    redis.delete.assert_awaited_once_with(b"bot:muted:+573001112233")
    meta.send_text.assert_not_called()


async def test_conversation_status_changed_resolved_unmutes_bot(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """El evento REAL de Chatwoot al resolver es conversation_status_changed
    con status=resolved (NO 'conversation_resolved'). Buscar solo el nombre
    viejo dejaba el bot muteado para siempre tras cada handoff (bug live
    2026-07-29)."""
    meta, chatwoot, redis = mocks
    chatwoot.get_phone_by_conv.return_value = "+573001112233"
    body = _payload(event="conversation_status_changed", status="resolved", id=42)
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": "unmuted"}
    redis.delete.assert_awaited_once_with(b"bot:muted:+573001112233")


async def test_conversation_status_changed_open_does_not_unmute(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """Un cambio de status a 'open' (reabrir) NO debe des-mutear."""
    _, _, redis = mocks
    body = _payload(event="conversation_status_changed", status="open", id=42)
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    redis.delete.assert_not_awaited()


async def test_ignores_bot_mirror_attribute(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """Loop prevention: mirrors posted via ChatwootClient carry
    content_attributes.bot_mirror and sender.type == "user" (user-level API
    token) — without this filter every bot reply echoed back as a duplicate."""
    meta, _, _ = mocks
    body = _payload(content_attributes={"bot_mirror": True})
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    assert r.json() == {"ignored": "bot_mirror"}
    meta.send_text.assert_not_called()


async def test_dedups_duplicate_id(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """Second delivery of the same message id within 24h is dropped (D-17)."""
    meta, _, redis = mocks
    # Key-aware side_effect: the dedup key is first-seen once then duplicate;
    # unrelated writes (e.g. the bot:muted takeover flag) always succeed.
    seen_dedup = False

    async def _set(key: bytes, *args: object, **kwargs: object) -> bool | None:
        nonlocal seen_dedup
        if key.startswith(b"chatwoot:msg:"):
            if seen_dedup:
                return None
            seen_dedup = True
            return True
        return True

    redis.set.side_effect = _set
    body = _payload()
    headers = {"X-Chatwoot-Signature": _sign(body)}

    r1 = await client.post("/webhooks/chatwoot", content=body, headers=headers)
    r2 = await client.post("/webhooks/chatwoot", content=body, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    meta.send_text.assert_awaited_once()


async def test_relays_text(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    meta, chatwoot, _ = mocks
    body = _payload()
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    chatwoot.get_phone_by_conv.assert_awaited_once_with(42)
    meta.send_text.assert_called_once_with("+573001", "Hola")


async def test_relays_image_attachment(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """Agent image is downloaded from Chatwoot, re-uploaded to Meta, sent (D-18)."""
    meta, chatwoot, _ = mocks
    body = _payload(
        content="",
        attachments=[
            {
                "file_type": "image",
                "data_url": "https://chat-test.example.com/rails/active_storage/x.jpg",
                "file_name": "comprobante.jpg",
            }
        ],
    )
    r = await client.post(
        "/webhooks/chatwoot", content=body, headers={"X-Chatwoot-Signature": _sign(body)}
    )
    assert r.status_code == 200
    chatwoot.download_attachment.assert_awaited_once_with(
        "https://chat-test.example.com/rails/active_storage/x.jpg"
    )
    meta.upload_media.assert_awaited_once()
    assert meta.upload_media.call_args[0][1] == "image/jpeg"
    meta.send_media.assert_awaited_once()
    args, kwargs = meta.send_media.call_args
    assert args[0] == "+573001"
    assert args[1] == "MEDIA_ID_1"
    assert args[2] == "image"
    assert kwargs.get("caption") is None
    meta.send_text.assert_not_called()
