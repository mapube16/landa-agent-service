"""Active WhatsApp alert to the DPG team when a client escalates.

Chatwoot's round-robin assigns the conversation and notifies the assignee
in-app, but that only works if the agent is watching Chatwoot. This sends a
WhatsApp message to the cartera number (the team's always-on channel, same
pattern the voice side uses for §7 alerts) so an escalation is never missed.

Scope: QA escalations only (client asked for a human, judge exhausted,
system errors). Payment-flow escalations are excluded on purpose — those
originate from cartera's own button taps or from cartera being unresponsive,
so alerting cartera about them is noise.

Dedupe: max one alert per client per 30 minutes (Redis SET NX), so a client
who types "agente" five times generates one alert, not five.

Fail-open everywhere: an alert failure must never break the escalation flow.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger("features.escalation.alerts")

ALERT_DEDUPE_SECONDS = 30 * 60


def _normalize_e164(raw: str) -> str:
    raw = raw.strip()
    return raw if raw.startswith("+") else "+" + raw


async def notify_team_escalation(
    meta: Any,
    redis: Any,
    *,
    client_phone: str,
    reason: str | None = None,
    cliente_nombre: str | None = None,
) -> None:
    """Send the escalation alert to the cartera WhatsApp (fail-open, deduped)."""
    from app.config.settings import settings

    cartera_list = list(settings.payment.cartera_phone_allowlist)
    if not cartera_list:
        log.warning("escalation_alert.no_cartera_configured")
        return

    phone_norm = _normalize_e164(client_phone)

    if redis is not None:
        try:
            first = await redis.set(
                f"alert:escalation:{phone_norm}".encode(),
                b"1",
                ex=ALERT_DEDUPE_SECONDS,
                nx=True,
            )
            if first is None:
                log.info("escalation_alert.deduped", phone_tail=phone_norm[-4:])
                return
        except Exception as exc:  # noqa: BLE001
            log.warning("escalation_alert.dedupe_failed", error_type=type(exc).__name__)

    quien = f"{cliente_nombre} ({phone_norm})" if cliente_nombre else phone_norm
    motivo = {
        "escape_hatch": "el cliente pidió hablar con una persona",
        "doc_exhausted": "no se pudo identificar al cliente",
        "judge_exhausted": "el asistente no pudo dar una respuesta validada",
        "breaker": "falla técnica consultando SoftSeguros",
    }.get(reason or "", reason or "solicitud de atención humana")
    body = (
        f"🔔 ALERTA DPG: {quien} necesita atención humana en WhatsApp — {motivo}. "
        f"La conversación está abierta en Chatwoot (chat.landatech.org)."
    )

    try:
        await meta.send_text(to=cartera_list[0], body=body)
        log.info(
            "escalation_alert.sent",
            phone_tail=phone_norm[-4:],
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("escalation_alert.send_failed", error_type=type(exc).__name__)


__all__ = ["ALERT_DEDUPE_SECONDS", "notify_team_escalation"]
