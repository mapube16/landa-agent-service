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
    chatwoot: Any = None,
) -> None:
    """Alert the DPG team on escalation (fail-open, deduped).

    Two channels: a private note inside the client's Chatwoot conversation
    (so the team sees it in context) plus a WhatsApp to cartera (always-on
    channel). Both are best-effort and independent.
    """
    from app.config.settings import settings

    phone_norm = _normalize_e164(client_phone)
    quien = f"{cliente_nombre} ({phone_norm})" if cliente_nombre else phone_norm
    motivo = {
        "escape_hatch": "el cliente pidió hablar con una persona",
        "doc_exhausted": "no se pudo identificar al cliente",
        "judge_exhausted": "el asistente no pudo dar una respuesta validada",
        "breaker": "falla técnica consultando SoftSeguros",
    }.get(reason or "", reason or "solicitud de atención humana")

    # Private note inside the Chatwoot conversation (team sees it in context).
    # BEFORE the dedupe gate — a note per escalation is useful history; the
    # dedupe only throttles the outbound WhatsApp blast to cartera.
    if chatwoot is not None:
        try:
            conv_id = await chatwoot.get_or_create_conversation(phone_norm)
            await chatwoot.post_private_note(
                conv_id, f"🔔 Escalación: {quien} necesita atención humana — {motivo}."
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("escalation_alert.chatwoot_note_failed", error_type=type(exc).__name__)

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

    cartera_list = list(settings.payment.cartera_phone_allowlist)
    if not cartera_list:
        log.warning("escalation_alert.no_cartera_configured")
        return

    body = (
        f"🔔 ALERTA DPG: {quien} necesita atención humana en WhatsApp — {motivo}. "
        f"La conversación está abierta en Chatwoot (chat.landatech.org)."
    )

    try:
        # Plantilla UTILITY aprobada → entrega SIEMPRE, con o sin ventana de
        # 24h abierta con cartera (el texto libre solo llega con ventana
        # abierta — hueco observado 29-jul). Mientras la plantilla esté
        # pendiente de aprobación, el envío falla y cae al texto libre.
        try:
            await meta.send_template(
                cartera_list[0],
                "alerta_atencion_humana",
                "es",
                body_params=[quien, motivo],
            )
        except Exception:  # noqa: BLE001
            await meta.send_text(to=cartera_list[0], body=body)
        log.info(
            "escalation_alert.sent",
            phone_tail=phone_norm[-4:],
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("escalation_alert.send_failed", error_type=type(exc).__name__)


__all__ = ["ALERT_DEDUPE_SECONDS", "notify_team_escalation"]
