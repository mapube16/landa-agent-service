"""Tests for the Chatwoot inverse index (Plan 04-03, D-16).

Covers:

- ``get_or_create_conversation`` populates ``chatwoot:phone_by_conv:{conv_id}``
- ``get_phone_by_conv`` Redis hit (no API call)
- ``get_phone_by_conv`` API fallback parsing ``meta.sender.phone_number``
- ``get_phone_by_conv`` returns None on 404 (no raise)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest  # type: ignore[import-not-found]


class FakeRedis:
    """Dict-backed async Redis stub (get/set/delete only)."""

    def __init__(self) -> None:
        self.store: dict[bytes, bytes] = {}

    async def get(self, key: bytes) -> bytes | None:
        return self.store.get(key)

    async def set(
        self, key: bytes, value: bytes, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: bytes) -> int:
        return int(self.store.pop(key, None) is not None)


def _make_response(
    status: int, json_body: dict[str, Any] | list[Any] | None = None, method: str = "POST"
) -> httpx.Response:
    """Build a real httpx.Response (raise_for_status needs a request bound)."""
    request = httpx.Request(method, "http://test/x")
    if json_body is not None:
        return httpx.Response(status_code=status, json=json_body, request=request)
    return httpx.Response(status_code=status, request=request)


@pytest.fixture  # type: ignore[untyped-decorator]
def stub_http() -> MagicMock:
    http = MagicMock(spec=httpx.AsyncClient)
    http.get = AsyncMock()
    http.post = AsyncMock()
    return http


@pytest.fixture  # type: ignore[untyped-decorator]
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture  # type: ignore[untyped-decorator]
def chatwoot_client(stub_http: MagicMock, fake_redis: FakeRedis) -> Any:
    from app.integrations.chatwoot import ChatwootClient

    return ChatwootClient(http=stub_http, account_id=1, redis=fake_redis)


async def test_inverse_index_populated_on_create(
    chatwoot_client: Any, stub_http: MagicMock, fake_redis: FakeRedis
) -> None:
    """Creating a conversation writes phone_by_conv; lookup is a Redis hit."""
    stub_http.post.side_effect = [
        _make_response(200, {"payload": {"contact": {"id": 7}}}),  # POST /contacts
        _make_response(200, {"id": 42}),  # POST /conversations
    ]
    # GET /contacts/7/conversations — no open conversation to reuse
    stub_http.get.return_value = _make_response(200, {"payload": []}, method="GET")

    conv_id = await chatwoot_client.get_or_create_conversation("+573001")
    assert conv_id == 42
    assert fake_redis.store.get(b"chatwoot:phone_by_conv:42") == b"+573001"

    gets_before = stub_http.get.await_count
    phone = await chatwoot_client.get_phone_by_conv(42)

    assert phone == "+573001"
    assert stub_http.get.await_count == gets_before  # Redis hit — no API fallback


async def test_get_phone_by_conv_fallback_to_api(
    chatwoot_client: Any, stub_http: MagicMock, fake_redis: FakeRedis
) -> None:
    """Cache miss falls back to GET /conversations/{id} -> meta.sender.phone_number."""
    stub_http.get.return_value = _make_response(
        200, {"meta": {"sender": {"phone_number": "+573009"}}}, method="GET"
    )

    phone = await chatwoot_client.get_phone_by_conv(99)

    assert phone == "+573009"
    call_path = stub_http.get.call_args[0][0]
    assert "/conversations/99" in call_path
    # Fallback repopulates the cache before returning
    assert fake_redis.store.get(b"chatwoot:phone_by_conv:99") == b"+573009"


async def test_get_phone_by_conv_returns_none(chatwoot_client: Any, stub_http: MagicMock) -> None:
    """Cache miss + API 404 -> None, no raise."""
    stub_http.get.return_value = _make_response(404, {"error": "not found"}, method="GET")

    phone = await chatwoot_client.get_phone_by_conv(999)

    assert phone is None
