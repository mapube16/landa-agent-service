"""Deterministic output firewall for payment-confirmation text (D-28, Phase 4).

The text "pago confirmado" (and variants) may ONLY appear in outbound messages
when the ``payment_approved`` flag is explicitly True — meaning cartera tapped
the "Aprobar" button and the payment node set the flag.

This module is intentionally dependency-free (no imports from app.*) so it can
be imported anywhere without side effects. Wave 5 (Plan 04-05) wires it into
``_run_and_dispatch`` in ``app/webhooks/meta.py``.

Pattern rationale (D-28):
  - ``pago confirmado``  — literal confirmation phrase
  - ``pago aprobado``    — synonym used by some cartera agents
  - ``tu pago fue (registrado|aceptado|recibido)``
                         — indirect confirmation variants
  Case-insensitive because operator copy may vary.
"""

from __future__ import annotations

import re

# Compiled pattern — case-insensitive, no mutable state.
# Matches:
#   pago confirmado / pago aprobado (direct adjacency)
#   pago fue (confirmado|aprobado|registrado|aceptado|recibido) — indirect variants
#   tu pago fue (registrado|aceptado|recibido) — D-28 explicit variants
# Overly cautious is correct here (D-28): all appearances are blocked unless
# the payment_approved flag is True.
_PAYMENT_CONFIRMED_RE: re.Pattern[str] = re.compile(
    r"pago\s+(confirmado|aprobado)"
    r"|pago\s+fue\s+(confirmado|aprobado|registrado|aceptado|recibido)"
    r"|tu\s+pago\s+fue\s+(registrado|aceptado|recibido)",
    re.IGNORECASE,
)


def check_outbound(text: str, *, payment_approved: bool) -> tuple[bool, str | None]:
    """Check whether ``text`` is safe to send given the payment state.

    Args:
        text: The outbound message text to inspect.
        payment_approved: True iff cartera has explicitly approved the payment
            (``QAState.payment_approved`` flag set by ``node_confirming``).

    Returns:
        ``(True, None)`` — message is safe to send.
        ``(False, reason)`` — message must be blocked; ``reason`` describes
            the matched pattern for logging/audit.
    """
    match = _PAYMENT_CONFIRMED_RE.search(text)
    if match and not payment_approved:
        return (
            False,
            f"payment_confirmation_without_approval: {match.group()!r}",
        )
    return True, None


__all__ = ["check_outbound"]
