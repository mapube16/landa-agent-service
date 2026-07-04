"""Phase 05-06: Rate limit enforcement in _dispatch_message + outbound audit capture.

Task 1: Rate limit wired between cartera branch and client-allowlist in _dispatch_message.
Task 2: outbound_sent / outbound_blocked audit events emitted from _send_outbound.

Dispatch order after 05-06 (D-15 extension):
    HMAC -> parse -> dedup -> cartera-allowlist -> rate_limit
        -> client-allowlist -> firewall -> graph

Cartera and duplicate messages are structurally exempt: the rate limiter is placed
AFTER the cartera branch so cartera taps can never be throttled (threat T-05-06-02).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Constants + helpers
# ---------------------------------------------------------------------------

WEBHOOK_SECRET = "test-webhook-secret-do-not-use-in-prod"
CLIENT_PHONE = "15555550100"  # matches WA_ECHO_ALLOWLIST in conftest
CLIENT_PHONE_E164 = "+15555550100"
CARTERA_PHONE = "15559990000"  # will be injected into cartera allowlist
CARTERA_PHONE_E164 = "+15559990000"


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _text_payload(
    message_id: str = "wamid.f5test",
    from_: str = CLIENT_PHONE,
    text: str = "hola",
) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "x",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {},
                                "messages": [
                                    {
                                        "from": from_,
                                        "id": message_id,
                                        "timestamp": "1",
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
    ).encode("utf-8")


def _cartera_text_payload(
    message_id: str = "wamid.cartera1",
    from_: str = CARTERA_PHONE,
    text: str = "revision",
) -> bytes:
    return _text_payload(message_id=message_id, from_=from_, text=text)


# ---------------------------------------------------------------------------
# Shared fixture: app_state with meta + redis + no cartera allowlist
# ---------------------------------------------------------------------------


@pytest.fixture()
def stub_f5(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Inject mock meta + redis + arq onto app.state for F5 rate-limit tests."""
    from app.main import app as fastapi_app

    meta_mock = MagicMock()
    meta_mock.send_text = AsyncMock(return_value="wamid.out")

    redis_mock = MagicMock()
    redis_mock.set = AsyncMock(return_value=True)  # first-see

    monkeypatch.setattr(fastapi_app.state, "meta", meta_mock, raising=False)
    monkeypatch.setattr(fastapi_app.state, "redis", redis_mock, raising=False)

    return meta_mock, redis_mock


# ---------------------------------------------------------------------------
# Task 1 — Rate limit enforcement tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limited_sends_t_rate_limited_and_returns(
    client: AsyncClient,
    stub_f5: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flooded client number -> T_RATE_LIMITED sent once; dispatch stops.

    check_rate_limit returns (False, "phone") -> meta.send_text called with T_RATE_LIMITED,
    and _handle_text_message is never called (no further processing).
    """
    import app.webhooks.meta as meta_module
    from app.security.rate_limiter import T_RATE_LIMITED

    meta_mock, redis_mock = stub_f5

    rl_blocked = MagicMock(allowed=False, scope="phone")

    with patch.object(meta_module, "check_rate_limit", new=AsyncMock(return_value=rl_blocked)):
        with patch.object(meta_module, "_handle_text_message", new=AsyncMock()) as mock_handle:
            body = _text_payload(message_id="wamid.rl_blocked")
            sig = _sign(body)
            r = await client.post(
                "/webhooks/meta",
                content=body,
                headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
            )
            assert r.status_code == 200

            # T_RATE_LIMITED sent exactly once
            meta_mock.send_text.assert_awaited_once()
            call_kwargs = meta_mock.send_text.call_args.kwargs
            assert call_kwargs.get("body") == T_RATE_LIMITED

            # _handle_text_message must NOT be reached
            mock_handle.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_allowed_continues_dispatch(
    client: AsyncClient,
    stub_f5: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """check_rate_limit returning (True, None) -> dispatch proceeds to client allowlist."""
    import app.webhooks.meta as meta_module

    meta_mock, redis_mock = stub_f5

    rl_allowed = MagicMock(allowed=True, scope=None)

    with patch.object(meta_module, "check_rate_limit", new=AsyncMock(return_value=rl_allowed)):
        with patch.object(meta_module, "_handle_text_message", new=AsyncMock()) as mock_handle:
            body = _text_payload(message_id="wamid.rl_allowed")
            sig = _sign(body)
            r = await client.post(
                "/webhooks/meta",
                content=body,
                headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
            )
            assert r.status_code == 200

            # Dispatch must continue — _handle_text_message is called
            await asyncio.sleep(0.05)
            mock_handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_cartera_number_never_calls_check_rate_limit(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cartera-allowlisted sender -> check_rate_limit NEVER called.

    Cartera branch runs BEFORE rate_limit (structural exemption per D-15 order).
    The cartera branch returns early so rate_limit is never reached.
    """
    import app.webhooks.meta as meta_module
    from app.main import app as fastapi_app

    meta_mock = MagicMock()
    meta_mock.send_text = AsyncMock(return_value="wamid.out")
    redis_mock = MagicMock()
    redis_mock.set = AsyncMock(return_value=True)

    monkeypatch.setattr(fastapi_app.state, "meta", meta_mock, raising=False)
    monkeypatch.setattr(fastapi_app.state, "redis", redis_mock, raising=False)

    # Inject cartera allowlist with our cartera phone
    monkeypatch.setattr(
        meta_module,
        "_get_cartera_allowlist",
        lambda: frozenset([CARTERA_PHONE_E164]),
    )

    # Stub qa_graph so _dispatch_message -> cartera branch doesn't crash
    qa_graph_mock = MagicMock()
    qa_graph_mock.ainvoke = AsyncMock(return_value={"messages": [], "node": "answering_qa"})
    monkeypatch.setattr(fastapi_app.state, "qa_graph", qa_graph_mock, raising=False)

    # Patch handle_cartera_message so we don't need real DB/graph
    with patch("app.webhooks.meta.handle_cartera_message", new=AsyncMock()) as mock_cartera:
        with patch.object(meta_module, "check_rate_limit", new=AsyncMock()) as mock_rl:
            body = _cartera_text_payload(message_id="wamid.cartera_exempt")
            sig = _sign(body)
            r = await client.post(
                "/webhooks/meta",
                content=body,
                headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
            )
            assert r.status_code == 200

            # Cartera handler was called
            mock_cartera.assert_awaited_once()

            # Rate limiter must NOT have been called (cartera exempt by ordering)
            mock_rl.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_message_never_calls_check_rate_limit(
    client: AsyncClient,
    stub_f5: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate message (dedup hit) -> check_rate_limit NEVER called.

    Dedup happens before the rate limiter so replayed messages don't
    consume rate-limit budget (prevents replay-based budget drain).
    """
    import app.webhooks.meta as meta_module

    meta_mock, redis_mock = stub_f5

    # Simulate a duplicate: redis.set returns None (key already exists)
    redis_mock.set = AsyncMock(return_value=None)

    with patch.object(meta_module, "check_rate_limit", new=AsyncMock()) as mock_rl:
        body = _text_payload(message_id="wamid.dup_rl")
        sig = _sign(body)
        r = await client.post(
            "/webhooks/meta",
            content=body,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        # Rate limiter must NOT have been called on a duplicate
        mock_rl.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_exception_fails_open(
    client: AsyncClient,
    stub_f5: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """check_rate_limit raising -> message still dispatched (fail-open)."""
    import app.webhooks.meta as meta_module

    meta_mock, redis_mock = stub_f5

    with patch.object(
        meta_module,
        "check_rate_limit",
        new=AsyncMock(side_effect=RuntimeError("redis boom")),
    ):
        with patch.object(meta_module, "_handle_text_message", new=AsyncMock()) as mock_handle:
            body = _text_payload(message_id="wamid.rl_exc")
            sig = _sign(body)
            r = await client.post(
                "/webhooks/meta",
                content=body,
                headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
            )
            assert r.status_code == 200
            # Despite exception, dispatch continues
            await asyncio.sleep(0.05)
            mock_handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_peek_poliza_id_returns_poliza_from_checkpoint() -> None:
    """_peek_poliza_id returns poliza_id from checkpointer channel_values."""
    import app.webhooks.meta as meta_module

    checkpointer_mock = MagicMock()
    checkpointer_mock.aget = AsyncMock(return_value={"channel_values": {"poliza_id": "POL-999"}})

    app_state = MagicMock()
    app_state.checkpointer = checkpointer_mock

    result = await meta_module._peek_poliza_id(app_state, "+15555550100")
    assert result == "POL-999"


@pytest.mark.asyncio
async def test_peek_poliza_id_returns_none_when_no_checkpointer() -> None:
    """_peek_poliza_id returns None when checkpointer is absent."""
    import app.webhooks.meta as meta_module

    app_state = MagicMock(spec=[])  # no checkpointer attr

    result = await meta_module._peek_poliza_id(app_state, "+15555550100")
    assert result is None


@pytest.mark.asyncio
async def test_peek_poliza_id_returns_none_when_checkpointer_errors() -> None:
    """_peek_poliza_id returns None when checkpointer.aget raises."""
    import app.webhooks.meta as meta_module

    checkpointer_mock = MagicMock()
    checkpointer_mock.aget = AsyncMock(side_effect=Exception("DB gone"))

    app_state = MagicMock()
    app_state.checkpointer = checkpointer_mock

    result = await meta_module._peek_poliza_id(app_state, "+15555550100")
    assert result is None


# ---------------------------------------------------------------------------
# Task 2 — outbound audit capture tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_sent_audit_emitted_on_successful_send() -> None:
    """Successful text send -> audit_log.emit_task called with action='outbound_sent'."""
    from langchain_core.messages import AIMessage

    import app.webhooks.meta as meta_module

    meta_client = MagicMock()
    meta_client.send_text = AsyncMock(return_value="wamid.sent")

    app_state = MagicMock()
    app_state.meta = meta_client
    # No arq to skip the mirror enqueue
    del app_state.arq

    msg = AIMessage(content="Hola, como puedo ayudarte?")

    with patch.object(meta_module, "audit_log") as mock_audit:
        mock_audit.emit_task = MagicMock()
        await meta_module._send_outbound(app_state, "+15555550100", msg, "wamid.in_001")

    mock_audit.emit_task.assert_called_once()
    call_kwargs = mock_audit.emit_task.call_args.kwargs
    assert call_kwargs["action"] == "outbound_sent"
    assert call_kwargs["actor"] == "bot"
    # conversation_id must be a hash via _hash_phone (no raw phone stored — PII rule).
    # _hash_phone returns 8-char truncated sha256 for log-safe correlation.
    assert call_kwargs["conversation_id"] != "+15555550100"
    assert call_kwargs["conversation_id"] is not None
    payload = call_kwargs["payload"]
    assert payload["wamid_in"] == "wamid.in_001"
    assert payload["kind"] in {"text", "buttons", "list"}
    assert len(payload["text_sha256"]) == 64  # sha256 hex (full digest for audit binding)


@pytest.mark.asyncio
async def test_outbound_blocked_audit_emitted_on_firewall_block() -> None:
    """Firewall-blocked send -> emit_task called with action='outbound_blocked'."""
    from langchain_core.messages import AIMessage

    import app.webhooks.meta as meta_module

    meta_client = MagicMock()
    meta_client.send_text = AsyncMock(return_value="wamid.esc")

    app_state = MagicMock()
    app_state.meta = meta_client
    # No chatwoot / arq to keep test simple
    del app_state.chatwoot
    del app_state.arq

    # A message that triggers the output firewall (payment confirmation)
    msg = AIMessage(content="Tu pago fue confirmado exitosamente.")
    # payment_approved NOT set -> firewall blocks

    with patch.object(meta_module, "audit_log") as mock_audit:
        mock_audit.emit_task = MagicMock()
        await meta_module._send_outbound(app_state, "+15555550100", msg, "wamid.in_fw")

    mock_audit.emit_task.assert_called_once()
    call_kwargs = mock_audit.emit_task.call_args.kwargs
    assert call_kwargs["action"] == "outbound_blocked"
    assert call_kwargs["actor"] == "bot"
    payload = call_kwargs["payload"]
    assert payload["wamid_in"] == "wamid.in_fw"
    assert "reason" in payload


@pytest.mark.asyncio
async def test_outbound_sent_not_emitted_when_send_raises() -> None:
    """meta.send_text raising -> NO outbound_sent event (only successful sends audited)."""
    from langchain_core.messages import AIMessage

    import app.webhooks.meta as meta_module

    meta_client = MagicMock()
    meta_client.send_text = AsyncMock(side_effect=RuntimeError("network error"))

    app_state = MagicMock()
    app_state.meta = meta_client
    del app_state.arq

    msg = AIMessage(content="Hola!")

    with patch.object(meta_module, "audit_log") as mock_audit:
        mock_audit.emit_task = MagicMock()
        await meta_module._send_outbound(app_state, "+15555550100", msg, "wamid.in_err")

    # Audit must NOT be called — only successful sends are audited
    for call in mock_audit.emit_task.call_args_list:
        assert (
            call.kwargs.get("action") != "outbound_sent"
        ), "outbound_sent must not be emitted when send raises"


@pytest.mark.asyncio
async def test_outbound_sent_payload_contains_correct_text_sha256() -> None:
    """text_sha256 in audit payload is sha256 of the actual text sent."""
    from langchain_core.messages import AIMessage

    import app.webhooks.meta as meta_module

    text_body = "Bienvenido a DPG Seguros."
    expected_hash = hashlib.sha256(text_body.encode()).hexdigest()

    meta_client = MagicMock()
    meta_client.send_text = AsyncMock(return_value="wamid.ok")

    app_state = MagicMock()
    app_state.meta = meta_client
    del app_state.arq

    msg = AIMessage(content=text_body)

    with patch.object(meta_module, "audit_log") as mock_audit:
        mock_audit.emit_task = MagicMock()
        await meta_module._send_outbound(app_state, "+15555550100", msg, "wamid.hash_check")

    call_kwargs = mock_audit.emit_task.call_args.kwargs
    assert call_kwargs["action"] == "outbound_sent"
    assert call_kwargs["payload"]["text_sha256"] == expected_hash
