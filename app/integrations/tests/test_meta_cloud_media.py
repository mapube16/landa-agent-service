"""Tests for MetaCloudClient media + template methods (Plan 04-02).

Stubs ``client._http`` verbs per-test via ``monkeypatch`` + ``AsyncMock``
(same pattern as ``tests/test_integrations_meta_cloud.py``) — no live
network. The client is constructed directly (not via the lru_cache
factory) so tests stay independent of singleton state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from pathlib import Path

    from app.integrations.meta_cloud import MetaCloudClient


def _make_client() -> MetaCloudClient:
    from app.integrations.meta_cloud import META_BASE_URL, MetaCloudClient

    http = httpx.AsyncClient(
        base_url=META_BASE_URL,
        headers={"Authorization": "Bearer test-token"},
    )
    return MetaCloudClient(http=http, phone_id="PHONE1", token="test-token")  # noqa: S106


def _json_response(status: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("POST", "https://graph.facebook.com/v21.0/x"),
    )


# ---------------------------------------------------------------------------
# upload_media
# ---------------------------------------------------------------------------


async def test_upload_media_returns_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    f = tmp_path / "comprobante.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    mock_post = AsyncMock(return_value=_json_response(200, {"id": "MID-1"}))
    monkeypatch.setattr(client._http, "post", mock_post)

    media_id = await client.upload_media(f, "image/jpeg")

    assert media_id == "MID-1"
    args, kwargs = mock_post.call_args
    assert args[0] == "/PHONE1/media"
    assert kwargs["data"]["messaging_product"] == "whatsapp"
    assert kwargs["data"]["type"] == "image/jpeg"
    assert "file" in kwargs["files"]


async def test_upload_media_raises_on_4xx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    f = tmp_path / "comprobante.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    mock_post = AsyncMock(
        return_value=_json_response(400, {"error": {"message": "bad", "code": 100}})
    )
    monkeypatch.setattr(client._http, "post", mock_post)

    with pytest.raises(httpx.HTTPStatusError):
        await client.upload_media(f, "image/jpeg")


# ---------------------------------------------------------------------------
# download_media (two-step: metadata GET -> CDN GET)
# ---------------------------------------------------------------------------


async def test_download_media_two_step(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    mock_get = AsyncMock(
        return_value=_json_response(
            200,
            {
                "url": "https://lookaside.fbsbx.com/x",
                "mime_type": "image/jpeg",
                "file_size": 1024,
            },
        )
    )
    monkeypatch.setattr(client._http, "get", mock_get)

    fetched: dict[str, str] = {}

    async def fake_fetch_cdn(url: str) -> httpx.Response:
        fetched["url"] = url
        return httpx.Response(
            200,
            content=b"FAKEBYTES",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(client, "_fetch_cdn", fake_fetch_cdn)

    result = await client.download_media("MID1")

    assert result == (b"FAKEBYTES", "image/jpeg")
    assert fetched["url"] == "https://lookaside.fbsbx.com/x"
    args, _ = mock_get.call_args
    assert args[0] == "/MID1"


async def test_download_media_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    mock_get = AsyncMock(
        return_value=_json_response(
            200,
            {
                "url": "https://lookaside.fbsbx.com/x",
                "mime_type": "image/jpeg",
                "file_size": 6_000_000,
            },
        )
    )
    monkeypatch.setattr(client._http, "get", mock_get)
    mock_fetch = AsyncMock()
    monkeypatch.setattr(client, "_fetch_cdn", mock_fetch)

    with pytest.raises(ValueError, match="attachment_too_large"):
        await client.download_media("MID1")

    # Size gate fires BEFORE the binary GET (D-25 — saves bandwidth).
    assert mock_fetch.await_count == 0


async def test_download_media_raises_on_cdn_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    mock_get = AsyncMock(
        return_value=_json_response(
            200,
            {
                "url": "https://lookaside.fbsbx.com/x",
                "mime_type": "image/jpeg",
                "file_size": 1024,
            },
        )
    )
    monkeypatch.setattr(client._http, "get", mock_get)

    async def fake_fetch_cdn(url: str) -> httpx.Response:
        return httpx.Response(403, request=httpx.Request("GET", url))

    monkeypatch.setattr(client, "_fetch_cdn", fake_fetch_cdn)

    with pytest.raises(httpx.HTTPStatusError):
        await client.download_media("MID1")
