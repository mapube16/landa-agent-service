"""Unit tests for app.worker.

Covers:
- WorkerSettings.functions lists mirror_inbound + mirror_outbound (no _noop)
- mirror_inbound calls get_or_create_conversation + post_message with incoming type
- mirror_outbound calls get_or_create_conversation + post_message with outgoing type
- ARQ Pitfall 6: all non-ctx kwargs are JSON primitives (str/int/bool/float/bytes/None)
"""

from __future__ import annotations

import inspect
import typing
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker import WorkerSettings, mirror_inbound, mirror_outbound

# ---------------------------------------------------------------------------
# Test 1: WorkerSettings.functions contains both mirror functions, not _noop
# ---------------------------------------------------------------------------


def test_worker_settings_lists_mirror_functions() -> None:
    names = [f.__name__ for f in WorkerSettings.functions]
    assert "mirror_inbound" in names, "mirror_inbound must be registered in WorkerSettings"
    assert "mirror_outbound" in names, "mirror_outbound must be registered in WorkerSettings"
    assert "_noop" not in names, "_noop must be removed in Phase 3"


# ---------------------------------------------------------------------------
# Test 2: mirror_inbound posts incoming message via Chatwoot client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_inbound_posts_incoming(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = MagicMock()
    mock_client.get_or_create_conversation = AsyncMock(return_value=42)
    mock_client.post_message = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "app.integrations.chatwoot.get_chatwoot_client",
        lambda: mock_client,
    )

    await mirror_inbound({}, phone="+15555550100", text="hola", wamid="wamid.123")

    mock_client.get_or_create_conversation.assert_awaited_once_with("+15555550100")
    mock_client.post_message.assert_awaited_once_with(42, "hola", message_type="incoming")


# ---------------------------------------------------------------------------
# Test 3: mirror_outbound posts outgoing message via Chatwoot client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_outbound_posts_outgoing(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = MagicMock()
    mock_client.get_or_create_conversation = AsyncMock(return_value=99)
    mock_client.post_message = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "app.integrations.chatwoot.get_chatwoot_client",
        lambda: mock_client,
    )

    await mirror_outbound({}, phone="+15555550100", text="respuesta del bot", wamid="wamid.456")

    mock_client.get_or_create_conversation.assert_awaited_once_with("+15555550100")
    mock_client.post_message.assert_awaited_once_with(
        99, "respuesta del bot", message_type="outgoing"
    )


# ---------------------------------------------------------------------------
# Test 4: ARQ Pitfall 6 -- all non-ctx kwargs must be JSON primitives
# ---------------------------------------------------------------------------


_PRIMITIVE_TYPES = (str, int, bool, float, bytes, type(None))


def test_mirror_functions_signature_uses_primitive_kwargs_only() -> None:
    """Guard: ARQ serializes job kwargs as JSON -- only primitives allowed.

    Pydantic models passed as kwargs would be silently dropped or corrupted.
    This test fails the build if a non-primitive annotation appears in any
    mirror function signature (ARQ Pitfall 6, RESEARCH note).
    """
    for fn in (mirror_inbound, mirror_outbound):
        hints = typing.get_type_hints(fn)
        sig = inspect.signature(fn)
        for name, param in sig.parameters.items():
            if name == "ctx":
                continue  # ARQ context dict, exempt
            annotation = hints.get(name, param.annotation)
            assert annotation in _PRIMITIVE_TYPES, (
                f"{fn.__name__}: parameter '{name}' has annotation "
                f"'{param.annotation}' which is not a JSON-primitive type. "
                "ARQ Pitfall 6: only str/int/bool/float/bytes/None allowed as "
                "ARQ job kwargs to guarantee JSON serializability."
            )
