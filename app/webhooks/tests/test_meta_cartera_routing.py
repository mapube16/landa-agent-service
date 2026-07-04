"""Tests for cartera-allowlist routing branch in app/webhooks/meta.py (Plan 04-05).

Covers:
  - Cartera number routes to handle_cartera_message; never touches _handle_text_message
  - Unknown number silently dropped — no outbound calls, HTTP 200
  - Client number (echo allowlist) routes to Q&A handler (_handle_text_message)
  - Valid button tap from cartera reaches handle_cartera_message

HMAC signing is done inline (_sign helper) so tests don't need a live secret store.
``CARTERA_PHONE_ALLOWLIST`` is set via os.environ before the test so the module-level
``_get_cartera_allowlist()`` picks it up.

The minimal FastAPI app mounts only the meta router and pre-seeds ``app.state``
with AsyncMock stand-ins — no live infrastructure.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ──────────────────────────────────────────────────────────────────────────────
# Constants that match app/conftest.py placeholders
# ──────────────────────────────────────────────────────────────────────────────

WEBHOOK_SECRET = "test-webhook-secret-do-not-use-in-prod"
CARTERA_PHONE = "+573001234567"
CLIENT_PHONE = "+15555550100"  # in WA_ECHO_ALLOWLIST in conftest.py
UNKNOWN_PHONE = "+599000000001"
CASE_ID = "550e8400-e29b-41d4-a716-446655440000"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _meta_payload(from_phone: str, msg_type: str = "text", text: str = "Hola") -> bytes:
    """Build a minimal Meta Cloud API inbound webhook payload."""
    msg: dict[str, Any] = {
        "from": from_phone,
        "id": f"wamid.test-{from_phone}-001",
        "timestamp": "1700000000",
        "type": msg_type,
    }
    if msg_type == "text":
        msg["text"] = {"body": text}
    elif msg_type == "interactive":
        msg["interactive"] = {
            "type": "button_reply",
            "button_reply": {"id": f"aprobar|{CASE_ID}", "title": "Aprobar"},
        }
    payload: dict[str, Any] = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "ENTRY_001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15551234567", "phone_number_id": "111"},
                            "messages": [msg],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    return json.dumps(payload).encode("utf-8")


def _signed_post(body: bytes) -> dict[str, str]:
    return {"X-Hub-Signature-256": _sign(body)}


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_redis() -> MagicMock:
    """Redis stub — always returns b'1' (first-see, no dedups)."""
    r = MagicMock()
    r.set = AsyncMock(return_value=b"1")
    return r


@pytest.fixture()
def mock_meta_client() -> MagicMock:
    m = MagicMock()
    m.send_text = AsyncMock(return_value="wamid.out1")
    m.send_buttons = AsyncMock(return_value="wamid.btn1")
    return m


@pytest.fixture()
async def client_with_cartera(
    mock_redis: MagicMock,
    mock_meta_client: MagicMock,
) -> AsyncIterator[tuple[AsyncClient, MagicMock, MagicMock]]:
    """FastAPI test client with CARTERA_PHONE_ALLOWLIST set to CARTERA_PHONE."""
    from fastapi import FastAPI

    from app.webhooks.meta import router

    app = FastAPI()
    app.include_router(router)
    app.state.meta = mock_meta_client
    app.state.redis = mock_redis
    # qa_graph is an AsyncMock — cartera handler will call its ainvoke
    qa_graph = AsyncMock()
    app.state.qa_graph = qa_graph

    with patch.dict(os.environ, {"CARTERA_PHONE_ALLOWLIST": CARTERA_PHONE}, clear=False):
        # Patch _get_cartera_allowlist to return the allowlist directly
        # (avoids lru_cache staleness between test runs)
        with patch(
            "app.webhooks.meta._get_cartera_allowlist",
            return_value=frozenset({CARTERA_PHONE}),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                yield ac, qa_graph, mock_meta_client


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cartera_number_routes_to_handler(
    client_with_cartera: tuple[AsyncClient, MagicMock, MagicMock],
) -> None:
    """Cartera phone must route to handle_cartera_message, not _handle_text_message."""
    ac, qa_graph, meta_client = client_with_cartera
    body = _meta_payload(CARTERA_PHONE)

    with patch("app.webhooks.meta.handle_cartera_message", new_callable=AsyncMock) as mock_handler:
        with patch("app.webhooks.meta._handle_text_message", new_callable=AsyncMock) as mock_qa:
            r = await ac.post("/webhooks/meta", content=body, headers=_signed_post(body))

    assert r.status_code == 200
    mock_handler.assert_called_once()
    mock_qa.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_number_silently_dropped(
    client_with_cartera: tuple[AsyncClient, MagicMock, MagicMock],
) -> None:
    """Unknown number (not in cartera allowlist, not in client allowlist) must be silently dropped."""
    ac, qa_graph, meta_client = client_with_cartera
    body = _meta_payload(UNKNOWN_PHONE)

    with patch("app.webhooks.meta.handle_cartera_message", new_callable=AsyncMock) as mock_cartera:
        with patch("app.webhooks.meta._handle_text_message", new_callable=AsyncMock) as mock_qa:
            r = await ac.post("/webhooks/meta", content=body, headers=_signed_post(body))

    assert r.status_code == 200
    mock_cartera.assert_not_called()
    mock_qa.assert_not_called()
    # No outbound send calls
    meta_client.send_text.assert_not_called()
    meta_client.send_buttons.assert_not_called()


@pytest.mark.asyncio
async def test_client_number_routes_to_qa(
    client_with_cartera: tuple[AsyncClient, MagicMock, MagicMock],
) -> None:
    """Client number (in echo_allowlist) must NOT be intercepted by cartera branch."""
    ac, qa_graph, meta_client = client_with_cartera
    body = _meta_payload(CLIENT_PHONE)

    with patch("app.webhooks.meta.handle_cartera_message", new_callable=AsyncMock) as mock_cartera:
        with patch("app.webhooks.meta._handle_text_message", new_callable=AsyncMock) as mock_qa:
            r = await ac.post("/webhooks/meta", content=body, headers=_signed_post(body))

    assert r.status_code == 200
    mock_cartera.assert_not_called()
    mock_qa.assert_called_once()


@pytest.mark.asyncio
async def test_cartera_button_tap_routes_to_handler(
    client_with_cartera: tuple[AsyncClient, MagicMock, MagicMock],
) -> None:
    """Button tap from cartera phone routes to handle_cartera_message (not QA)."""
    ac, qa_graph, meta_client = client_with_cartera
    body = _meta_payload(CARTERA_PHONE, msg_type="interactive")

    with patch("app.webhooks.meta.handle_cartera_message", new_callable=AsyncMock) as mock_handler:
        with patch("app.webhooks.meta._handle_text_message", new_callable=AsyncMock) as mock_qa:
            r = await ac.post("/webhooks/meta", content=body, headers=_signed_post(body))

    assert r.status_code == 200
    mock_handler.assert_called_once()
    mock_qa.assert_not_called()
