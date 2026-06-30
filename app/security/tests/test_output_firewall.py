"""Tests for app.security.output_firewall.check_outbound.

D-28: "pago confirmado" and variants are blocked unless payment_approved=True.
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
