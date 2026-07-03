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
    return meta, chatwoot, redis


@pytest.fixture  # type: ignore[untyped-decorator]
async def client(mocks: tuple[MagicMock, MagicMock, MagicMock]) -> AsyncIterator[AsyncClient]:
    from fastapi import FastAPI

    from app.webhooks.chatwoot import router  # type: ignore[import-not-found]

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


async def test_dedups_duplicate_id(
    client: AsyncClient, mocks: tuple[MagicMock, MagicMock, MagicMock]
) -> None:
    """Second delivery of the same message id within 24h is dropped (D-17)."""
    meta, _, redis = mocks
    redis.set.side_effect = [True, None]  # first-see, then duplicate
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
