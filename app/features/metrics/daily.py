"""Daily operational metrics for the WhatsApp channel.

Fills the hole the voice-side daily report (lambda-proyect ``reports.py``,
informe §12) explicitly leaves open: it reports call metrics but marks
``comprobantes_recibidos`` and everything WhatsApp as "no disponible en este
canal". This module computes those from OUR sources so the voice report — or
any consumer — can pull them via ``GET /metrics/daily``.

Two sources, both already in this service:
- **audit_log** (Postgres, append-only): comprobantes (``attachment_received``),
  escalations (``escalation``), template button taps (``template_button_tap``),
  handoffs received from voice (``handoff_received``), approvals
  (``payment_approved``). Counted by ``created_at`` within the day window.
- **Chatwoot labels** (applied by native rules + the bot): conversation state
  — ``sin-respuesta`` / ``en-conversacion`` / ``comprobante-recibido`` /
  ``escalado-humano`` / ``promesa-pago``.

All timestamps are Colombia-local day boundaries converted to UTC, matching
the voice report's ``_dia_utc_range`` so both reports agree on "el día".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

# Colombia is UTC-5, no DST. Hardcoded to avoid a tz-data dependency for a
# single fixed offset (the voice side uses pytz America/Bogota; same result).
_CO_OFFSET = timezone(timedelta(hours=-5))

# audit_log action → metric key
_ACTION_METRICS = {
    "attachment_received": "comprobantes_recibidos",
    "escalation": "escalaciones",
    "template_button_tap": "respuestas_a_plantilla",
    "handoff_received": "handoffs_de_voz",
    "payment_approved": "pagos_aprobados",
}

# Chatwoot label → metric key (conversation state distribution)
_LABEL_METRICS = {
    "sin-respuesta": "conv_sin_respuesta",
    "en-conversacion": "conv_en_conversacion",
    "escalado-humano": "conv_escaladas",
    "comprobante-recibido": "conv_con_comprobante",
    "promesa-pago": "conv_promesa_pago",
}


def today_co() -> date:
    """Today's calendar date in Colombia (UTC-5).

    The server runs in UTC; ``datetime.now().date()`` there flips to the next
    day at 19:00 Colombia time, so "hoy" would show an empty next-day report
    all evening. Anchoring to the Colombia offset keeps "hoy" = the day the
    team is actually working.
    """
    return datetime.now(_CO_OFFSET).date()


def day_range_utc(d: date) -> tuple[datetime, datetime]:
    """[start, end) of the Colombia-local calendar day ``d``, as UTC datetimes."""
    start_local = datetime.combine(d, time.min, tzinfo=_CO_OFFSET)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def summarize(
    action_counts: dict[str, int],
    label_counts: dict[str, int],
    total_conversations: int,
) -> dict[str, Any]:
    """Build the metrics payload from raw counts (pure — no I/O).

    ``action_counts``: audit action → count within the day.
    ``label_counts``: Chatwoot label → number of conversations carrying it.
    ``total_conversations``: conversations with activity in the day.
    """
    out: dict[str, Any] = {k: 0 for k in _ACTION_METRICS.values()}
    out.update({k: 0 for k in _LABEL_METRICS.values()})

    for action, key in _ACTION_METRICS.items():
        out[key] = action_counts.get(action, 0)
    for label, key in _LABEL_METRICS.items():
        out[key] = label_counts.get(label, 0)

    out["conversaciones_total"] = total_conversations
    # Response rate to the follow-up template: conversations where the client
    # engaged vs. total (sin-respuesta are the ones that never replied).
    respondidas = total_conversations - out["conv_sin_respuesta"]
    out["tasa_respuesta_plantilla"] = (
        round(respondidas / total_conversations, 3) if total_conversations else 0.0
    )
    return out


__all__ = ["day_range_utc", "summarize"]
