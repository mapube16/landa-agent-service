"""Unit tests for app.integrations.chatwoot.

Stubbed httpx + redis; never hits the network. Covers:

- post_message incoming + outgoing paths + correct URL
- get_or_create_conversation cache hit / cache miss two-step create
- mark_resolved POSTs toggle_status
- factory header uses api_access_token (NOT Authorization Bearer)
- factory lru_cache singleton identity
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_http() -> MagicMock:
    """Stubbed httpx.AsyncClient with AsyncMock for .get and .post."""
    http = MagicMock(spec=httpx.AsyncClient)
    http.get = AsyncMock()
    http.post = AsyncMock()
    return http


@pytest.fixture
def stub_redis() -> MagicMock:
    """Stubbed Redis client; default get -> None (cache miss)."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


@pytest.fixture
def chatwoot_client(stub_http: MagicMock, stub_redis: MagicMock) -> Any:
    """ChatwootClient with stubbed httpx + redis, account_id=1."""
    from app.integrations.chatwoot import ChatwootClient

    return ChatwootClient(http=stub_http, account_id=1, redis=stub_redis)


def _make_response(status: int, json_body: dict[str, Any] | None = None) -> httpx.Response:
    """Build a real httpx.Response (raise_for_status needs a request bound)."""
    request = httpx.Request("POST", "http://test/x")
    if json_body is not None:
        return httpx.Response(status_code=status, json=json_body, request=request)
    return httpx.Response(status_code=status, request=request)


# ---------------------------------------------------------------------------
# Test 1: post_message incoming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_incoming_calls_correct_path(
    chatwoot_client: Any, stub_http: MagicMock
) -> None:
    stub_http.post.return_value = _make_response(200, {})

    await chatwoot_client.post_message(42, "hola", "incoming")

    stub_http.post.assert_awaited_once()
    call_args = stub_http.post.call_args
    # First positional arg is the path
    assert "/conversations/42/messages" in call_args[0][0]
    assert call_args[1]["json"] == {"content": "hola", "message_type": "incoming"}


# ---------------------------------------------------------------------------
# Test 2: post_message outgoing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_outgoing_works(chatwoot_client: Any, stub_http: MagicMock) -> None:
    stub_http.post.return_value = _make_response(200, {})

    await chatwoot_client.post_message(99, "respuesta", "outgoing")

    stub_http.post.assert_awaited_once()
    call_args = stub_http.post.call_args
    assert "/conversations/99/messages" in call_args[0][0]
    assert call_args[1]["json"]["message_type"] == "outgoing"


# ---------------------------------------------------------------------------
# Test 3: get_or_create_conversation cache hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_conversation_cache_hit(
    chatwoot_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    # Pre-populate cache with conv_id 123
    stub_redis.get.return_value = b"123"

    result = await chatwoot_client.get_or_create_conversation("+15555550100")

    assert result == 123
    # No HTTP calls should be made on cache hit
    stub_http.post.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 4: get_or_create_conversation cache miss -- two-step create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_conversation_cache_miss_creates_contact_and_conversation(
    chatwoot_client: Any, stub_http: MagicMock, stub_redis: MagicMock
) -> None:
    stub_redis.get.return_value = None

    # Step 1: contacts POST returns contact_id
    contact_response = _make_response(
        200,
        {"payload": {"contact": {"id": 77}, "meta": {}}},
    )
    # Step 2: conversations POST returns conv_id
    conv_response = _make_response(200, {"id": 42, "status": "open"})

    stub_http.post.side_effect = [contact_response, conv_response]
    # GET /contacts/{id}/conversations -> empty so we proceed to create
    stub_http.get.return_value = _make_response(200, {"payload": []})

    result = await chatwoot_client.get_or_create_conversation("+15555550100")

    assert result == 42
    # Two POST calls: contacts + conversations
    assert stub_http.post.await_count == 2
    # Cache write happened with 7-day TTL (lock acquire also calls SET with NX/EX=15;
    # find the actual conversation-cache write among the calls).
    cache_writes = [
        call for call in stub_redis.set.call_args_list if call.kwargs.get("ex") == 604800
    ]
    assert len(cache_writes) == 1


# ---------------------------------------------------------------------------
# Test 5: mark_resolved POSTs toggle_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_resolved_posts_toggle_status(
    chatwoot_client: Any, stub_http: MagicMock
) -> None:
    stub_http.post.return_value = _make_response(200, {})

    await chatwoot_client.mark_resolved(42)

    stub_http.post.assert_awaited_once()
    call_args = stub_http.post.call_args
    assert "/conversations/42/toggle_status" in call_args[0][0]
    assert call_args[1]["json"] == {"status": "resolved"}


# ---------------------------------------------------------------------------
# Test 6: factory uses api_access_token header, NOT Bearer
# ---------------------------------------------------------------------------


def test_factory_uses_api_access_token_header_not_bearer() -> None:
    from app.integrations.chatwoot import get_chatwoot_client

    # Clear lru_cache so we get a fresh instance from settings
    get_chatwoot_client.cache_clear()
    try:
        client = get_chatwoot_client()
        headers = client._http.headers
        assert "api_access_token" in headers, "api_access_token header must be set"
        no_bearer = not any("bearer" in v.lower() for v in headers.values())
        assert no_bearer, "Authorization: Bearer must NOT be used for Chatwoot"
    finally:
        get_chatwoot_client.cache_clear()


# ---------------------------------------------------------------------------
# Test 7: factory caches singleton (lru_cache identity)
# ---------------------------------------------------------------------------


def test_factory_caches_singleton() -> None:
    from app.integrations.chatwoot import get_chatwoot_client

    get_chatwoot_client.cache_clear()
    try:
        c1 = get_chatwoot_client()
        c2 = get_chatwoot_client()
        assert c1 is c2, "get_chatwoot_client must return the same instance (lru_cache)"
    finally:
        get_chatwoot_client.cache_clear()
