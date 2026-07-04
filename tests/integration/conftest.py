"""Integration test fixtures for Phase 4 end-to-end tests (Plan 04-08).

Provides a minimal FastAPI app with:
- Meta, Chatwoot, Redis, ARQ mocks attached to app.state
- HMAC signing helpers for Meta + Chatwoot webhook payloads
- Inbound payload builder helpers
- Session + DB mock for handoff/cartera paths

The app does NOT start the full lifespan (no real Postgres/Redis). All I/O is
stubbed via AsyncMock so the tests remain hermetic and fast.

Mark the integration test module with @pytest.mark.integration so it can be
filtered via -m integration.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

META_SECRET = "test-webhook-secret-do-not-use-in-prod"  # noqa: S105
CHATWOOT_SECRET = "test-cw-webhook-secret"  # noqa: S105
LAMBDA_TOKEN = "test-lambda-token"  # noqa: S105

CLIENT_PHONE = "+15555550100"  # in WA_ECHO_ALLOWLIST in root conftest.py
CARTERA_PHONE = "+573999000001"
UNKNOWN_PHONE = "+599000000099"


# ──────────────────────────────────────────────────────────────────────────────
# HMAC signing helpers
# ──────────────────────────────────────────────────────────────────────────────


def meta_sign(body: bytes, secret: str = META_SECRET) -> str:
    """Return Meta X-Hub-Signature-256 header value for ``body``."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def chatwoot_sign(body: bytes, secret: str = CHATWOOT_SECRET) -> str:
    """Return Chatwoot X-Chatwoot-Signature header value for ``body``."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# Inbound payload builders
# ──────────────────────────────────────────────────────────────────────────────


def build_inbound_text(from_: str, text: str, msg_id: str) -> bytes:
    """Build a Meta inbound text webhook envelope."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "ENTRY_001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "16415416615",
                                "phone_number_id": "1267241483129092",
                            },
                            "messages": [
                                {
                                    "from": from_,
                                    "id": msg_id,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    return json.dumps(payload).encode()


def build_inbound_interactive(from_: str, button_id: str, msg_id: str) -> bytes:
    """Build a Meta inbound button-reply webhook envelope."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "ENTRY_001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "16415416615",
                                "phone_number_id": "1267241483129092",
                            },
                            "messages": [
                                {
                                    "from": from_,
                                    "id": msg_id,
                                    "timestamp": "1700000000",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": button_id,
                                            "title": button_id.split("|")[0].title(),
                                        },
                                    },
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    return json.dumps(payload).encode()


def build_inbound_image(from_: str, media_id: str, mime: str, msg_id: str) -> bytes:
    """Build a Meta inbound image webhook envelope."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "ENTRY_001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "16415416615",
                                "phone_number_id": "1267241483129092",
                            },
                            "messages": [
                                {
                                    "from": from_,
                                    "id": msg_id,
                                    "timestamp": "1700000000",
                                    "type": "image",
                                    "image": {
                                        "id": media_id,
                                        "mime_type": mime,
                                        "sha256": "fakehash",
                                    },
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    return json.dumps(payload).encode()


# ──────────────────────────────────────────────────────────────────────────────
# DB session mock
# ──────────────────────────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Minimal async session stub for integration tests."""

    def __init__(self, cases: list[Any] | None = None) -> None:
        self._cases: list[Any] = cases or []
        self.added: list[Any] = []
        self.commits = 0
        self.flushes = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self._cases)

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self._cases.append(obj)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1


# ──────────────────────────────────────────────────────────────────────────────
# App factory fixture (shared across integration tests)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def meta_mock() -> MagicMock:
    """AsyncMock MetaCloudClient."""
    m = MagicMock()
    m.send_text = AsyncMock(return_value="wamid.out1")
    m.send_buttons = AsyncMock(return_value="wamid.btn1")
    m.send_template = AsyncMock(return_value="wamid.tpl1")
    m.send_media = AsyncMock(return_value="wamid.media1")
    m.send_media_ack = AsyncMock(return_value="wamid.ack1")
    m.upload_media = AsyncMock(return_value="MEDIA_ID_UPLOAD")
    # download_media returns valid JPEG bytes (magic header matches JPEG)
    sample_path = Path(__file__).parent.parent / "fixtures" / "sample.jpg"
    jpeg_bytes = sample_path.read_bytes() if sample_path.exists() else b"\xff\xd8\xff\xe0\x00"
    m.download_media = AsyncMock(return_value=(jpeg_bytes, "image/jpeg"))
    return m


@pytest.fixture()
def chatwoot_mock() -> MagicMock:
    """AsyncMock ChatwootClient."""
    m = MagicMock()
    m.get_or_create_conversation = AsyncMock(return_value=42)
    m.post_message = AsyncMock()
    m.get_phone_by_conv = AsyncMock(return_value=CLIENT_PHONE)
    m.mark_resolved = AsyncMock()
    return m


@pytest.fixture()
def redis_mock() -> MagicMock:
    """AsyncMock Redis — always first-see (no dedups by default)."""
    r = MagicMock()
    r.set = AsyncMock(return_value=b"1")
    r.get = AsyncMock(return_value=None)
    return r


@pytest.fixture()
def arq_mock() -> MagicMock:
    """AsyncMock ARQ pool."""
    a = MagicMock()
    a.enqueue_job = AsyncMock()
    return a


@pytest.fixture()
def fake_session() -> _FakeSession:
    return _FakeSession()
