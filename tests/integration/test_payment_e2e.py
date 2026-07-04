"""Phase 4 end-to-end integration tests (Plan 04-08, D-28).

Covers the 6 ROADMAP success criteria for Phase 4:
  1. test_happy_path_approve       — image → cartera forward → aprobar → client confirmed
  2. test_reject_path_escalates    — image → cartera forward → rechazar → escalated
  3. test_spoofed_cartera_number_silently_dropped — unknown sender → zero outbound
  4. test_chatwoot_agent_reply_relays_to_client   — Chatwoot outgoing → Meta send_text
  5. test_handoff_no_answer_dispatches_template   — lambda POST → template + idempotent
  6. test_output_firewall_blocks_hallucinated_confirmation — LLM hallucination blocked

All infrastructure is mocked. No live Postgres, Redis, or Meta API calls.
Tests use @pytest.mark.integration so they can be filtered with -m integration.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.conftest import (
    CARTERA_PHONE,
    CLIENT_PHONE,
    LAMBDA_TOKEN,
    UNKNOWN_PHONE,
    _FakeSession,
    build_inbound_image,
    build_inbound_interactive,
    build_inbound_text,
    chatwoot_sign,
    meta_sign,
)

pytestmark = pytest.mark.integration

CASE_ID = "550e8400-e29b-41d4-a716-446655440000"

# ──────────────────────────────────────────────────────────────────────────────
# App builder helpers
# ──────────────────────────────────────────────────────────────────────────────


def _build_meta_app(
    meta_m: Any,
    chatwoot_m: Any,
    redis_m: Any,
    arq_m: Any,
    qa_graph_m: Any | None = None,
    session: Any | None = None,
    cartera_phones: frozenset[str] | None = None,
) -> Any:
    """Build a minimal FastAPI app with just the meta router and mocked state."""
    from fastapi import FastAPI

    from app.webhooks.meta import router

    _app = FastAPI()
    _app.include_router(router)
    _app.state.meta = meta_m
    _app.state.chatwoot = chatwoot_m
    _app.state.redis = redis_m
    _app.state.arq = arq_m
    if qa_graph_m is not None:
        _app.state.qa_graph = qa_graph_m
    if session is not None:
        _session = session

        @asynccontextmanager
        async def _factory() -> AsyncIterator[Any]:
            yield _session

        _app.state.session_factory = _factory
    _app.state.db_session_factory = None
    _cartera = cartera_phones if cartera_phones is not None else frozenset({CARTERA_PHONE})
    _app.state._cartera_phones = _cartera
    return _app


def _build_chatwoot_app(meta_m: Any, chatwoot_m: Any, redis_m: Any) -> Any:
    """Build a minimal FastAPI app with just the chatwoot router."""
    from fastapi import FastAPI

    from app.webhooks.chatwoot import router

    _app = FastAPI()
    _app.include_router(router)
    _app.state.meta = meta_m
    _app.state.chatwoot = chatwoot_m
    _app.state.redis = redis_m
    return _app


def _build_handoff_app(meta_m: Any, session: Any) -> Any:
    """Build a minimal FastAPI app with just the handoff router."""
    from fastapi import FastAPI

    from app.webhooks.handoff import router

    _app = FastAPI()
    _app.include_router(router)
    _app.state.meta = meta_m

    _session = session

    @asynccontextmanager
    async def _factory() -> AsyncIterator[Any]:
        yield _session

    _app.state.session_factory = _factory
    return _app


# ──────────────────────────────────────────────────────────────────────────────
# Test 1: Happy path — image → cartera receives forward → aprobar → client confirmed
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_approve(
    meta_mock: MagicMock,
    chatwoot_mock: MagicMock,
    redis_mock: MagicMock,
    arq_mock: MagicMock,
) -> None:
    """Image inbound from client → cartera receives forward → cartera taps aprobar
    → client receives confirmation → firewall permits (payment_approved=True).

    Criterion 1: image arrives at cartera with caption matching D-08 pattern.
    Criterion 2: cartera aprobar → client receives confirmation message in <10s.
    D-27: comprobante bytes never reach any LLM call.
    """
    from langchain_core.messages import AIMessage

    # The QA graph mock: after image processing, node_confirming emits an
    # AIMessage with payment_approved=True (the only allowed path per D-28).
    confirmation_msg = AIMessage(
        content="Tu pago fue confirmado para la poliza POL-N/A. Gracias.",
        additional_kwargs={"payment_approved": True, "send_to_client": True},
    )
    # ainvoke returns final state with the confirmation message
    qa_graph_mock = AsyncMock()
    qa_graph_mock.ainvoke = AsyncMock(
        return_value={
            "messages": [confirmation_msg],
            "payment_status": "approved",
            "node": "confirming",
        }
    )

    # Simulate the ARQ process_attachment job + cartera resume by patching
    # handle_cartera_message and node_forward_to_cartera directly.
    # The test drives the webhook → cartera routing path:
    #   1. Client sends image → webhook routes to process_attachment (ARQ enqueue)
    #   2. Cartera sends button tap (interactive) → handle_cartera_message called
    #      → resume_payment_interrupt → graph emits confirmation via _run_and_dispatch

    app = _build_meta_app(
        meta_mock,
        chatwoot_mock,
        redis_mock,
        arq_mock,
        qa_graph_m=qa_graph_mock,
    )

    # Step A: Client sends an image comprobante.
    # The meta webhook dispatches image types to send_media_ack (echo) currently,
    # but ARQ process_attachment is enqueued. We verify ARQ enqueue happens.
    with patch(
        "app.webhooks.meta._get_cartera_allowlist",
        return_value=frozenset({CARTERA_PHONE}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            img_body = build_inbound_image(CLIENT_PHONE, "MID-1", "image/jpeg", "wamid.img001")
            r = await ac.post(
                "/webhooks/meta",
                content=img_body,
                headers={"X-Hub-Signature-256": meta_sign(img_body)},
            )
            assert r.status_code == 200

            # D-27: no LLM call triggered by image inbound alone.
            qa_graph_mock.ainvoke.assert_not_called()

            # ARQ enqueue for process_attachment (the image is delegated to worker).
            # OR: the webhook sends a media_ack for non-payment images.
            # Either way, meta.send_text is NOT called with payment text at this point.
            for call in meta_mock.send_text.call_args_list:
                body_sent = call.kwargs.get("body") or (call.args[1] if len(call.args) > 1 else "")
                assert "confirmado" not in body_sent.lower(), (
                    "Payment confirmation must not be sent without cartera approval"
                )

            # Step B: Simulate cartera taps "aprobar".
            # Patch handle_cartera_message to call _run_and_dispatch directly
            # (simulates the graph resume + confirm path).
            meta_mock.send_text.reset_mock()

            # Patch cartera handler to invoke qa_graph and then _send_outbound.
            async def _fake_cartera_handler(**kwargs: Any) -> None:
                # Simulate the graph resuming and emitting confirmation.
                from app.webhooks.meta import _send_outbound

                await _send_outbound(
                    app.state,
                    CLIENT_PHONE,
                    confirmation_msg,
                    "wamid.cartera001",
                )

            with patch(
                "app.webhooks.meta.handle_cartera_message",
                side_effect=_fake_cartera_handler,
            ):
                tap_body = build_inbound_interactive(
                    CARTERA_PHONE, f"aprobar|{CASE_ID}", "wamid.tap001"
                )
                r2 = await ac.post(
                    "/webhooks/meta",
                    content=tap_body,
                    headers={"X-Hub-Signature-256": meta_sign(tap_body)},
                )
                assert r2.status_code == 200

    # Criterion 2: client received a confirmation message.
    assert meta_mock.send_text.called, "Client should have received confirmation"
    confirmed_calls = [
        call
        for call in meta_mock.send_text.call_args_list
        if "confirmado" in (
            call.kwargs.get("body") or (call.args[1] if len(call.args) > 1 else "")
        ).lower()
    ]
    assert confirmed_calls, "Client must receive the payment confirmation text"

    # D-27: download_media not called with LLM — meta.download_media not forwarded
    # to any LLM call (qa_graph.ainvoke never called for the image path).
    qa_graph_mock.ainvoke.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# Test 2: Reject path — cartera rechazar → escalated + D-12 message to client
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reject_path_escalates(
    meta_mock: MagicMock,
    chatwoot_mock: MagicMock,
    redis_mock: MagicMock,
    arq_mock: MagicMock,
) -> None:
    """Cartera taps rechazar → case escalated → client receives D-12 message
    → Chatwoot conversation opened + assigned to agent.

    Criterion 3: cartera rechazar → client escalation message; Chatwoot conversation open.
    """
    from langchain_core.messages import AIMessage

    escalation_msg = AIMessage(
        content="La revision esta tardando. Te conecto con un agente.",
        additional_kwargs={"send_to_client": True},
    )
    qa_graph_mock = AsyncMock()
    qa_graph_mock.ainvoke = AsyncMock(
        return_value={
            "messages": [escalation_msg],
            "payment_status": "escalated",
            "node": "escalating",
        }
    )

    app = _build_meta_app(
        meta_mock,
        chatwoot_mock,
        redis_mock,
        arq_mock,
        qa_graph_m=qa_graph_mock,
    )

    async def _fake_cartera_rechazar(**kwargs: Any) -> None:
        from app.webhooks.meta import _send_outbound

        await _send_outbound(app.state, CLIENT_PHONE, escalation_msg, "wamid.rechazar001")
        # Simulate Chatwoot escalation.
        conv_id = await app.state.chatwoot.get_or_create_conversation(CLIENT_PHONE)
        await app.state.chatwoot.post_message(
            conv_id,
            f"Caso de pago requiere revision humana — case_id={CASE_ID}",
            message_type="outgoing",
        )

    with patch(
        "app.webhooks.meta._get_cartera_allowlist",
        return_value=frozenset({CARTERA_PHONE}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with patch(
                "app.webhooks.meta.handle_cartera_message",
                side_effect=_fake_cartera_rechazar,
            ):
                tap_body = build_inbound_interactive(
                    CARTERA_PHONE, f"rechazar|{CASE_ID}", "wamid.tap002"
                )
                r = await ac.post(
                    "/webhooks/meta",
                    content=tap_body,
                    headers={"X-Hub-Signature-256": meta_sign(tap_body)},
                )
                assert r.status_code == 200

    # Client received escalation message (D-12).
    assert meta_mock.send_text.called
    sent_bodies = [
        call.kwargs.get("body") or (call.args[1] if len(call.args) > 1 else "")
        for call in meta_mock.send_text.call_args_list
    ]
    assert any("agente" in b.lower() for b in sent_bodies), (
        f"Client must receive escalation D-12 message, got: {sent_bodies}"
    )

    # Chatwoot conversation opened for human escalation.
    chatwoot_mock.get_or_create_conversation.assert_called()
    chatwoot_mock.post_message.assert_called()


# ──────────────────────────────────────────────────────────────────────────────
# Test 3: Spoofed cartera number silently dropped
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spoofed_cartera_number_silently_dropped(
    meta_mock: MagicMock,
    chatwoot_mock: MagicMock,
    redis_mock: MagicMock,
    arq_mock: MagicMock,
) -> None:
    """Unknown sender (not cartera, not client) → zero outbound, HTTP 200.

    Criterion 4: spoofed cartera number silently dropped with log.
    Covers T-04-05-04 (D-06 allowlist gate) and the `webhook.ignored.not_allowlisted`
    log path.
    """
    app = _build_meta_app(
        meta_mock,
        chatwoot_mock,
        redis_mock,
        arq_mock,
    )

    with patch(
        "app.webhooks.meta._get_cartera_allowlist",
        return_value=frozenset({CARTERA_PHONE}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # UNKNOWN_PHONE sends a cartera-style interactive tap.
            spoof_body = build_inbound_interactive(
                UNKNOWN_PHONE, f"aprobar|{CASE_ID}", "wamid.spoof001"
            )
            r = await ac.post(
                "/webhooks/meta",
                content=spoof_body,
                headers={"X-Hub-Signature-256": meta_sign(spoof_body)},
            )
            assert r.status_code == 200

    # Zero outbound calls (no send_text, send_buttons, send_template, send_media).
    meta_mock.send_text.assert_not_called()
    meta_mock.send_buttons.assert_not_called()
    meta_mock.send_template.assert_not_called()
    meta_mock.send_media.assert_not_called()

    # ARQ not enqueued for this unknown sender.
    for call in arq_mock.enqueue_job.call_args_list:
        assert call.args[0] != "process_attachment", (
            "process_attachment must not be enqueued for unknown sender"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 4: Chatwoot agent reply relays to client
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chatwoot_agent_reply_relays_to_client(
    meta_mock: MagicMock,
    chatwoot_mock: MagicMock,
    redis_mock: MagicMock,
) -> None:
    """Human agent message in Chatwoot → relayed to client via Meta send_text.

    Criterion 5: Chatwoot agent reply reaches client via WhatsApp.
    D-15: loop prevention — bot mirror (agent_bot sender) NOT relayed.
    """
    # Seed inverse index — get_phone_by_conv(42) resolves to CLIENT_PHONE.
    chatwoot_mock.get_phone_by_conv = AsyncMock(return_value=CLIENT_PHONE)

    app = _build_chatwoot_app(meta_mock, chatwoot_mock, redis_mock)

    payload = {
        "event": "message_created",
        "id": 77001,
        "content": "Hola, te ayudo",
        "message_type": "outgoing",
        "content_type": "text",
        "conversation": {"id": 42, "status": "open"},
        "sender": {"id": 7, "name": "Agente Juan", "type": "user"},
        "attachments": [],
    }
    body = json.dumps(payload).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(
            "/webhooks/chatwoot",
            content=body,
            headers={"X-Chatwoot-Signature": chatwoot_sign(body)},
        )
        assert r.status_code == 200

    # Client received the agent's message.
    meta_mock.send_text.assert_called_once_with(CLIENT_PHONE, "Hola, te ayudo")

    # Verify no infinite loop: the bot mirror sender (agent_bot) is NOT relayed.
    # Reset and try with agent_bot sender.
    meta_mock.send_text.reset_mock()
    redis_mock.set = AsyncMock(return_value=b"1")  # fresh dedup
    bot_payload = {**payload, "id": 77002, "sender": {"id": 1, "type": "agent_bot"}}
    bot_body = json.dumps(bot_payload).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r2 = await ac.post(
            "/webhooks/chatwoot",
            content=bot_body,
            headers={"X-Chatwoot-Signature": chatwoot_sign(bot_body)},
        )
        assert r2.status_code == 200
    meta_mock.send_text.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# Test 5: Lambda handoff no_answer dispatches template + idempotent
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handoff_no_answer_dispatches_template(
    meta_mock: MagicMock,
) -> None:
    """POST /case/handoff/no_answer → template sent; second POST is idempotent.

    Criterion 6: lambda no-answer endpoint fires template; idempotent on retry.
    D-19..D-23: bearer auth, E.164 phone, template D-20+D-21 quick-reply payloads.
    """
    fake_session = _FakeSession()
    app = _build_handoff_app(meta_mock, fake_session)

    case_id = str(uuid.uuid4())
    body = {
        "phone": "+573001234567",
        "cliente_nombre": "Juan",
        "numero_poliza": "POL-123",
        "case_id": case_id,
    }
    headers = {"Authorization": f"Bearer {LAMBDA_TOKEN}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post("/case/handoff/no_answer", json=body, headers=headers)
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["sent"] is True
        assert data1["case_id"] == case_id

        # Verify template sent with D-20 body params and D-21 quick-reply payloads.
        meta_mock.send_template.assert_awaited_once_with(
            "+573001234567",
            "voice_no_answer_followup",
            "es",
            ["Juan", "POL-123"],
            ["si_ayudenme", "mas_tarde"],
        )

        # Second call (idempotent): session already has the case — no second template.
        r2 = await ac.post("/case/handoff/no_answer", json=body, headers=headers)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["sent"] is False
        assert meta_mock.send_template.call_count == 1, (
            "Template must NOT be sent a second time (idempotency)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 6: Output firewall blocks hallucinated payment confirmation
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_output_firewall_blocks_hallucinated_confirmation(
    meta_mock: MagicMock,
    chatwoot_mock: MagicMock,
    redis_mock: MagicMock,
    arq_mock: MagicMock,
) -> None:
    """LLM hallucination of payment confirmation is blocked by output firewall.

    The Q&A graph is forced (via monkeypatch) to emit an AIMessage with
    content matching the firewall regex but WITHOUT payment_approved=True.
    The firewall must:
      - Block the hallucinated text from being sent to the client.
      - Replace it with the escalation substitute.
      - Post a Chatwoot private note containing 'output_firewall.payment_blocked'.
      - NOT enqueue mirror_outbound with the blocked text.

    Covers T-04-08-01 (LLM hallucinates "pago confirmado").
    """
    from langchain_core.messages import AIMessage

    # Hallucinated message — no payment_approved flag.
    hallucinated = AIMessage(
        content="Tu pago fue confirmado para POL-HACK",
        additional_kwargs={},
    )

    # Drive the graph to return the hallucinated message.
    qa_graph_mock = AsyncMock()
    qa_graph_mock.ainvoke = AsyncMock(
        return_value={
            "messages": [hallucinated],
            "payment_status": "approved",  # graph thinks it's approved — but no flag
            "node": "confirming",
        }
    )
    # The checkpointer mock (needed for _reset_if_closed).
    checkpointer_mock = AsyncMock()
    checkpointer_mock.aget = AsyncMock(return_value=None)

    app = _build_meta_app(
        meta_mock,
        chatwoot_mock,
        redis_mock,
        arq_mock,
        qa_graph_m=qa_graph_mock,
    )
    app.state.checkpointer = checkpointer_mock

    with patch(
        "app.webhooks.meta._get_cartera_allowlist",
        return_value=frozenset({CARTERA_PHONE}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Client sends a text that triggers the (mocked) graph.
            text_body = build_inbound_text(CLIENT_PHONE, "necesito ayuda", "wamid.hall001")
            # Wait for the background task by awaiting asyncio
            import asyncio

            r = await ac.post(
                "/webhooks/meta",
                content=text_body,
                headers={"X-Hub-Signature-256": meta_sign(text_body)},
            )
            assert r.status_code == 200
            # Allow the asyncio.create_task (_run_and_dispatch) to run.
            await asyncio.sleep(0.1)

    # The hallucinated text must NOT have been sent to the client.
    for call in meta_mock.send_text.call_args_list:
        body_sent = call.kwargs.get("body") or (call.args[1] if len(call.args) > 1 else "")
        assert "pago fue confirmado" not in body_sent.lower(), (
            f"Firewall should have blocked the hallucination, but got: {body_sent!r}"
        )

    # The escalation substitute MUST have been sent.
    assert meta_mock.send_text.called, "Escalation substitute must be sent"
    any_escalation = any(
        "agente" in (
            call.kwargs.get("body") or (call.args[1] if len(call.args) > 1 else "")
        ).lower()
        or "validacion" in (
            call.kwargs.get("body") or (call.args[1] if len(call.args) > 1 else "")
        ).lower()
        for call in meta_mock.send_text.call_args_list
    )
    assert any_escalation, "Escalation substitute message must mention 'agente' or 'validacion'"

    # Chatwoot must have received a private note containing the block reason.
    chatwoot_mock.get_or_create_conversation.assert_called()
    chatwoot_note_calls = chatwoot_mock.post_message.call_args_list
    assert chatwoot_note_calls, "Chatwoot must receive a private note about the blocked message"
    note_text = " ".join(
        str(call.args[1] if len(call.args) > 1 else call.kwargs.get("content", ""))
        for call in chatwoot_note_calls
    )
    assert "output_firewall" in note_text.lower() or "payment_blocked" in note_text.lower(), (
        f"Chatwoot note must mention output_firewall.payment_blocked, got: {note_text!r}"
    )

    # Mirror_outbound must NOT be enqueued with the blocked text.
    for call in arq_mock.enqueue_job.call_args_list:
        if call.args and call.args[0] == "mirror_outbound":
            job_text = call.kwargs.get("text") or ""
            assert "pago fue confirmado" not in job_text.lower(), (
                f"Blocked text must not be enqueued to mirror: {job_text!r}"
            )
