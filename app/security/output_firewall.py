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

# Certificaciones de solvencia (extensión D-28, decisión DPG 29-jul): afirmar
# "está al día / sin saldo pendiente / puedes estar tranquilo" equivale a
# confirmar un pago — y esa verificación es EXCLUSIVA del equipo de cartera.
# Observado en vivo: el bot certificó "tu póliza está al día" tras recibir un
# comprobante. Informar un saldo pendiente CONCRETO ("tu saldo pendiente es de
# $X") sigue permitido — estos patrones solo capturan la afirmación de NO deuda.
_NO_DEBT_ASSERTION_RE: re.Pattern[str] = re.compile(
    r"no\s+(tienes|tiene|hay|aparece|registra|queda)\s+(ning[uú]n\s+)?saldo"
    r"|sin\s+saldo\s+pendiente"
    r"|est[aá]\s+al\s+d[ií]a"
    r"|puedes?\s+estar\s+tranquil[oa]"
    r"|ya\s+(est[aá]|qued[oó])\s+pagad[oa]"
    r"|no\s+debes?\s+nada"
    # El bot NO debe especular sobre por qué el sistema muestra saldo cero
    # ("el sistema no se había actualizado con tu pago") — eso es concluir
    # que el pago ya entró, competencia exclusiva de cartera (visto en vivo).
    r"|sistema\s+.{0,40}\bno\s+.{0,15}\bactualiz" r"|no\s+.{0,15}\bactualiz.{0,40}\btu\s+pago",
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
    match = _NO_DEBT_ASSERTION_RE.search(text)
    if match and not payment_approved:
        return (
            False,
            f"no_debt_assertion_without_approval: {match.group()!r}",
        )
    return True, None


__all__ = ["check_outbound"]
