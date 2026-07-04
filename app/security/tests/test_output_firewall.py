"""Tests for app.security.output_firewall.check_outbound and _send_outbound wiring.

D-28: "pago confirmado" and variants are blocked unless payment_approved=True.
Task 1 (Plan 04-08): Integration tests verifying check_outbound is wired into
_send_outbound so the firewall gate is active on every outbound dispatch.
"""

from __future__ import annotations


class TestCheckOutbound:
    def test_pago_confirmado_blocked_without_flag(self) -> None:
        """'pago confirmado' in output blocked when payment_approved=False."""
        from app.security.output_firewall import check_outbound

        allowed, reason = check_outbound(
            "Tu pago fue confirmado para POL-123", payment_approved=False
        )
        assert allowed is False
        assert reason is not None
        assert "payment_confirmation_without_approval" in reason

    def test_pago_confirmado_allowed_with_flag(self) -> None:
        """'pago confirmado' allowed when payment_approved=True."""
        from app.security.output_firewall import check_outbound

        allowed, reason = check_outbound(
            "Tu pago fue confirmado para POL-123", payment_approved=True
        )
        assert allowed is True
        assert reason is None

    def test_pago_aprobado_blocked_without_flag(self) -> None:
        """'Pago aprobado.' blocked when payment_approved=False."""
        from app.security.output_firewall import check_outbound

        allowed, reason = check_outbound("Pago aprobado.", payment_approved=False)
        assert allowed is False
        assert reason is not None

    def test_neutral_text_always_allowed(self) -> None:
        """Neutral text without payment patterns is always allowed."""
        from app.security.output_firewall import check_outbound

        allowed, reason = check_outbound("Tu saldo es 100", payment_approved=False)
        assert allowed is True
        assert reason is None

    def test_case_insensitive_match(self) -> None:
        """Pattern matching is case-insensitive (D-28)."""
        from app.security.output_firewall import check_outbound

        allowed, reason = check_outbound("tu pago fue REGISTRADO POL-9", payment_approved=False)
        assert allowed is False
        assert reason is not None

    def test_tu_pago_fue_registrado_blocked(self) -> None:
        """'tu pago fue registrado' matches the pattern when flag is False."""
        from app.security.output_firewall import check_outbound

        allowed, reason = check_outbound("tu pago fue registrado", payment_approved=False)
        assert allowed is False

    def test_tu_pago_fue_aceptado_blocked(self) -> None:
        """'tu pago fue aceptado' matches the pattern when flag is False."""
        from app.security.output_firewall import check_outbound

        allowed, reason = check_outbound("Tu pago fue aceptado.", payment_approved=False)
        assert allowed is False

    def test_tu_pago_fue_recibido_blocked(self) -> None:
        """'tu pago fue recibido' matches the pattern when flag is False."""
        from app.security.output_firewall import check_outbound

        allowed, reason = check_outbound(
            "Gracias, tu pago fue recibido exitosamente.", payment_approved=False
        )
        assert allowed is False

    def test_all_patterns_allowed_with_flag(self) -> None:
        """All payment patterns allowed when payment_approved=True."""
        from app.security.output_firewall import check_outbound

        for text in [
            "pago confirmado",
            "pago aprobado",
            "tu pago fue registrado",
            "Tu pago fue aceptado",
            "tu pago fue recibido",
        ]:
            allowed, reason = check_outbound(text, payment_approved=True)
            assert allowed is True, f"Expected allowed for {text!r} with flag=True"
            assert reason is None

    def test_reason_contains_matched_text(self) -> None:
        """Reason string contains the matched pattern text."""
        from app.security.output_firewall import check_outbound

        _, reason = check_outbound("pago aprobado", payment_approved=False)
        assert reason is not None
        assert "pago aprobado" in reason.lower()


class TestSendOutboundFirewallWiring:
    """Task 1 (Plan 04-08): check_outbound is wired into _send_outbound.

    These tests verify that the firewall gate is active on every outbound
    dispatch path without requiring live infrastructure.
    """

    def _make_app_state(
        self,
        meta_mock: object,
        chatwoot_mock: object | None = None,
        arq_mock: object | None = None,
    ) -> object:
        """Build a minimal app_state object with the mocks attached."""

        class _FakeState:
            pass

        state = _FakeState()
        state.meta = meta_mock  # type: ignore[attr-defined]
        if chatwoot_mock is not None:
            state.chatwoot = chatwoot_mock  # type: ignore[attr-defined]
        if arq_mock is not None:
            state.arq = arq_mock  # type: ignore[attr-defined]
        return state

    async def test_send_outbound_blocks_payment_text_without_flag(self) -> None:
        """Payment text without payment_approved=True is blocked; escalation sent instead."""
        from unittest.mock import AsyncMock

        from langchain_core.messages import AIMessage

        from app.webhooks.meta import _send_outbound

        meta_mock = AsyncMock()
        chatwoot_mock = AsyncMock()
        chatwoot_mock.get_or_create_conversation = AsyncMock(return_value=42)
        chatwoot_mock.post_message = AsyncMock()
        arq_mock = AsyncMock()
        arq_mock.enqueue_job = AsyncMock()

        app_state = self._make_app_state(meta_mock, chatwoot_mock, arq_mock)

        msg = AIMessage(
            content="Tu pago fue confirmado para POL-9",
            additional_kwargs={},
        )

        await _send_outbound(app_state, "+573500000001", msg, "wamid-test-block")

        # The ORIGINAL payment text must NOT have been sent.
        for call in meta_mock.send_text.call_args_list:
            body = call.kwargs.get("body") or (call.args[1] if len(call.args) > 1 else "")
            assert (
                "pago fue confirmado" not in body.lower()
            ), f"Firewall should have blocked the original text, but got: {body!r}"

        # The escalation substitute MUST have been sent.
        assert meta_mock.send_text.called, "Escalation substitute should have been sent"
        sent_body = (
            meta_mock.send_text.call_args.kwargs.get("body")
            or meta_mock.send_text.call_args.args[1]
        )
        assert (
            "agente" in sent_body.lower() or "validacion" in sent_body.lower()
        ), f"Expected escalation substitute message, got: {sent_body!r}"

        # Chatwoot escalation must have been triggered.
        assert chatwoot_mock.get_or_create_conversation.called or chatwoot_mock.post_message.called

        # ARQ mirror must NOT have been enqueued with the blocked text.
        for call in arq_mock.enqueue_job.call_args_list:
            job_text = call.kwargs.get("text") or ""
            assert (
                "pago fue confirmado" not in job_text.lower()
            ), f"Blocked text must not be enqueued to mirror: {job_text!r}"

    async def test_send_outbound_allows_payment_text_with_flag(self) -> None:
        """Payment text with payment_approved=True passes through unchanged."""
        from unittest.mock import AsyncMock

        from langchain_core.messages import AIMessage

        from app.webhooks.meta import _send_outbound

        meta_mock = AsyncMock()
        arq_mock = AsyncMock()
        arq_mock.enqueue_job = AsyncMock()
        app_state = self._make_app_state(meta_mock, arq_mock=arq_mock)

        msg = AIMessage(
            content="Tu pago fue confirmado para POL-9",
            additional_kwargs={"payment_approved": True},
        )

        await _send_outbound(app_state, "+573500000001", msg, "wamid-test-allow")

        # Original text must have been sent.
        assert meta_mock.send_text.called
        sent_body = (
            meta_mock.send_text.call_args.kwargs.get("body")
            or meta_mock.send_text.call_args.args[1]
        )
        assert (
            "confirmado" in sent_body.lower()
        ), f"Expected original payment text, got: {sent_body!r}"

    async def test_send_outbound_unrelated_text_passes_through(self) -> None:
        """Unrelated text (no payment pattern) is sent as-is."""
        from unittest.mock import AsyncMock

        from langchain_core.messages import AIMessage

        from app.webhooks.meta import _send_outbound

        meta_mock = AsyncMock()
        arq_mock = AsyncMock()
        arq_mock.enqueue_job = AsyncMock()
        app_state = self._make_app_state(meta_mock, arq_mock=arq_mock)

        msg = AIMessage(content="Tu saldo es 100", additional_kwargs={})

        await _send_outbound(app_state, "+573500000001", msg, "wamid-test-neutral")

        assert meta_mock.send_text.called
        sent_body = (
            meta_mock.send_text.call_args.kwargs.get("body")
            or meta_mock.send_text.call_args.args[1]
        )
        assert sent_body == "Tu saldo es 100"
