"""Tests for app.integrations.meta_cloud — MetaCloudClient + get_meta_client.

Stubs ``httpx.AsyncClient.post`` per-test via ``monkeypatch`` so no live
network is required. The factory is ``@lru_cache``-backed, so the cache
is cleared in the ``stubbed_client`` fixture to avoid bleed across the
test session.

NOTE: ``app.*`` imports happen inside test bodies so the autouse session
fixture ``_test_env`` (conftest) can populate env vars before any
``Settings()`` instantiation (same pattern as ``test_llm_factory.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest

if TYPE_CHECKING:
    from app.integrations.meta_cloud import MetaCloudClient


# ---------------------------------------------------------------------------
# Sync tests — constants + factory wiring
# ---------------------------------------------------------------------------


def test_meta_api_version_is_v21() -> None:
    from app.integrations.meta_cloud import META_API_VERSION

    assert META_API_VERSION == "v21.0"


def test_meta_base_url_composition() -> None:
    from app.integrations.meta_cloud import META_BASE_URL

    assert META_BASE_URL == "https://graph.facebook.com/v21.0"


def test_get_meta_client_is_singleton() -> None:
    from app.integrations.meta_cloud import MetaCloudClient, get_meta_client

    get_meta_client.cache_clear()
    a = get_meta_client()
    b = get_meta_client()
    assert a is b
    assert isinstance(a, MetaCloudClient)


def test_get_meta_client_uses_phone_id_from_settings() -> None:
    from app.integrations.meta_cloud import get_meta_client

    get_meta_client.cache_clear()
    client = get_meta_client()
    # Placeholder from tests/conftest.py::_test_env (Plan 02-01).
    assert client._phone_id == "1267241483129092"


def test_get_meta_client_uses_token_in_auth_header() -> None:
    from app.integrations.meta_cloud import get_meta_client

    get_meta_client.cache_clear()
    client = get_meta_client()
    # Placeholder WA_TOKEN from conftest. We assert wiring of the bearer
    # token into the httpx header — not a leak (this is the only spot in
    # the test suite that touches the raw secret).
    assert client._http.headers["Authorization"] == "Bearer wa-test-token"


def test_get_meta_client_uses_meta_base_url() -> None:
    from app.integrations.meta_cloud import META_BASE_URL, get_meta_client

    get_meta_client.cache_clear()
    client = get_meta_client()
    # base_url has the trailing slash that httpx normalises into.
    assert str(client._http.base_url).rstrip("/") == META_BASE_URL


def test_hash_phone_returns_8_hex_chars() -> None:
    from app.integrations.meta_cloud import _hash_phone

    h = _hash_phone("+15555550100")
    assert len(h) == 8
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_phone_is_deterministic() -> None:
    from app.integrations.meta_cloud import _hash_phone

    assert _hash_phone("+15555550100") == _hash_phone("+15555550100")
    assert _hash_phone("+15555550100") != _hash_phone("+15555550101")


# ---------------------------------------------------------------------------
# Async tests — send_text + send_media_ack
# ---------------------------------------------------------------------------


@pytest.fixture
def stubbed_client(monkeypatch: pytest.MonkeyPatch) -> tuple[MetaCloudClient, AsyncMock]:
    """Return ``(client, mock_post)`` with ``client._http.post`` patched."""
    from app.integrations.meta_cloud import get_meta_client

    get_meta_client.cache_clear()
    client = get_meta_client()
    mock_post = AsyncMock()
    mock_post.return_value = httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "16505551234", "wa_id": "16505551234"}],
            "messages": [{"id": "wamid.XYZ"}],
        },
        request=httpx.Request("POST", "https://graph.facebook.com/v21.0/x/messages"),
    )
    monkeypatch.setattr(client._http, "post", mock_post)
    return client, mock_post


async def test_send_text_posts_to_messages_endpoint(
    stubbed_client: tuple[MetaCloudClient, AsyncMock],
) -> None:
    client, mock_post = stubbed_client
    await client.send_text(to="16505551234", body="hola")
    assert mock_post.await_count == 1
    args, kwargs = mock_post.call_args
    # URL is the first positional arg.
    assert args[0] == f"/{client._phone_id}/messages"
    # JSON body matches the Meta-required shape (RESEARCH "Code Examples").
    assert kwargs["json"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "16505551234",
        "type": "text",
        "text": {"body": "hola"},
    }


async def test_send_text_returns_wamid_from_response(
    stubbed_client: tuple[MetaCloudClient, AsyncMock],
) -> None:
    client, _ = stubbed_client
    wamid = await client.send_text(to="16505551234", body="hola")
    assert wamid == "wamid.XYZ"


async def test_send_text_raises_on_4xx(
    stubbed_client: tuple[MetaCloudClient, AsyncMock],
) -> None:
    client, mock_post = stubbed_client
    mock_post.return_value = httpx.Response(
        400,
        json={"error": {"message": "bad", "code": 100}},
        request=httpx.Request("POST", "https://graph.facebook.com/v21.0/x/messages"),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.send_text(to="16505551234", body="hola")


async def test_send_media_ack_delegates_to_send_text_with_media_echo(
    stubbed_client: tuple[MetaCloudClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = stubbed_client
    # Replace send_text to capture what send_media_ack feeds it (proves the
    # delegation path; the HTTP-level test above already covers send_text).
    captured: dict[str, str] = {}

    async def fake_send_text(to: str, body: str) -> str:
        captured["to"] = to
        captured["body"] = body
        return "wamid.MEDIA"

    monkeypatch.setattr(client, "send_text", fake_send_text)
    wamid = await client.send_media_ack(to="16505551234", media_type="image")
    assert wamid == "wamid.MEDIA"
    assert captured == {"to": "16505551234", "body": "echo: [image] received"}
