"""ARQ scheduled job bodies for the payment flow (Plan 04-06).

Two jobs:

check_pending_cases (every-minute cron, D-10/D-11/D-12/D-13/D-14):
    - If outside business hours: return immediately (no DB writes).
    - Query cases WHERE status='awaiting_cartera' AND work_hours_due_at <= NOW()
      AND escalated_at IS NULL.
    - For each row:
        - If reminder_sent_at IS NULL and business_minutes_between(created_at, now) >= 20:
          send a buttons reminder to the first cartera number; stamp reminder_sent_at.
        - Elif business_minutes_between(created_at, now) >= 90 (and reminder already sent):
          escalate to Chatwoot + send D-12 text to client; stamp status/escalated_at.
    - All idempotency enforced via DB column predicates (T-04-06-01).

cleanup_attachments_90d (daily cron at 02:00 UTC, D-02):
    - Delete Attachment rows whose received_at < now() - 90 days.
    - Unlink the corresponding volume files (missing_ok=True).
    - Case rows are NOT deleted (audit retention per T-04-06-02).

No asyncio.sleep. No in-memory state. All timer math via DB rows +
pure functions from business_hours.
"""

from __future__ import annotations

import datetime
from typing import Any

import anyio
import structlog
from sqlalchemy import delete, select, update

from app.features.payment.business_hours import (
    TZ_CO,
    business_minutes_between,
    is_business_time,
)
from app.memory.case_store import Attachment, Case

log = structlog.get_logger("features.payment.scheduler")

# ---------------------------------------------------------------------------
# Internal helpers (monkeypatch targets for tests)
# ---------------------------------------------------------------------------


def _now_utc() -> datetime.datetime:
    """Return the current UTC datetime. Monkeypatched in tests."""
    return datetime.datetime.now(datetime.UTC)


def _get_settings_payment() -> Any:
    """Return settings.payment. Monkeypatched in tests."""
    from app.config.settings import settings

    return settings.payment


# ---------------------------------------------------------------------------
# check_pending_cases
# ---------------------------------------------------------------------------


async def check_pending_cases(ctx: dict[str, Any]) -> dict[str, Any]:
    """Every-minute cron: send reminders and escalate stale payment cases.

    Returns:
        {"skipped": "off_hours"} if outside business hours.
        {"skipped": "no_cartera"} if cartera allowlist is empty.
        {"processed": N} otherwise.
    """
    now_utc = _now_utc()

    # D-10: bail outside business hours to avoid false triggers.
    if not is_business_time(now_utc.astimezone(TZ_CO)):
        return {"skipped": "off_hours"}

    # Retrieve cartera recipient.
    payment_settings = _get_settings_payment()
    cartera_allow = list(payment_settings.cartera_phone_allowlist)
    if not cartera_allow:
        log.error(
            "scheduler.no_cartera_configured",
            reason="CARTERA_PHONE_ALLOWLIST is empty or not set",
        )
        return {"skipped": "no_cartera"}

    cartera_phone = cartera_allow[0]

    meta = ctx["meta"]
    chatwoot = ctx["chatwoot"]
    db = ctx["db_session_factory"]

    processed = 0

    async with db() as session:
        # Poll: awaiting_cartera cases whose SLA window has passed (D-11/D-12).
        q = select(Case).where(
            Case.status == "awaiting_cartera",
            Case.work_hours_due_at <= now_utc,
            Case.escalated_at.is_(None),
        )
        result = await session.execute(q)
        cases = result.scalars().all()

        for case in cases:
            elapsed = business_minutes_between(case.created_at, now_utc)

            if case.reminder_sent_at is None and elapsed >= 20:
                # D-11: Send reminder to cartera; stamp reminder_sent_at.
                body = (
                    f"Sigue pendiente caso #{case.case_id} "
                    f"del cliente {case.cliente_nombre or '(sin nombre)'}"
                )
                buttons = [
                    (f"aprobar|{case.case_id}", "Aprobar"),
                    (f"rechazar|{case.case_id}", "Rechazar"),
                    (f"info|{case.case_id}", "Mas info"),
                ]
                await meta.send_buttons(to=cartera_phone, body=body, buttons=buttons)
                await session.execute(
                    update(Case)
                    .where(Case.case_id == case.case_id)
                    .values(reminder_sent_at=now_utc)
                )
                log.info(
                    "scheduler.reminder_sent",
                    case_id=case.case_id,
                    elapsed=elapsed,
                )
                processed += 1
                # Do not escalate in the same tick (D-12 requires reminder first).
                continue

            if elapsed >= 90 and case.reminder_sent_at is not None:
                # D-12: Auto-escalate to Chatwoot + notify client.
                conv_id = await chatwoot.get_or_create_conversation(case.phone)
                await chatwoot.post_message(
                    conv_id,
                    (
                        f"Auto-escalado: caso #{case.case_id} sin respuesta de cartera "
                        f"90 minutos habiles"
                    ),
                    message_type="outgoing",
                )
                await meta.send_text(
                    case.phone,
                    "La revision esta tardando. Te conecto con un agente humano.",
                )
                await session.execute(
                    update(Case)
                    .where(Case.case_id == case.case_id)
                    .values(status="escalated", escalated_at=now_utc)
                )
                log.info(
                    "scheduler.escalated",
                    case_id=case.case_id,
                    elapsed=elapsed,
                )
                processed += 1

        await session.commit()

    return {"processed": processed}


# ---------------------------------------------------------------------------
# cleanup_attachments_90d
# ---------------------------------------------------------------------------


async def cleanup_attachments_90d(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily cron (02:00 UTC): unlink attachment files older than 90 days + delete rows.

    Case rows are NOT deleted — retained for audit (T-04-06-02, D-02 policy).

    Returns:
        {"deleted": N} with the count of Attachment rows removed.
    """
    now_utc = _now_utc()
    cutoff = now_utc - datetime.timedelta(days=90)

    db = ctx["db_session_factory"]
    deleted = 0

    async with db() as session:
        q = select(Attachment).where(Attachment.received_at < cutoff)
        result = await session.execute(q)
        old_attachments = result.scalars().all()

        for att in old_attachments:
            try:
                await anyio.Path(att.path).unlink()
            except FileNotFoundError:
                pass  # missing_ok equivalent — file already gone
            await session.execute(delete(Attachment).where(Attachment.id == att.id))
            deleted += 1

        await session.commit()

    log.info("scheduler.cleanup_done", deleted=deleted, cutoff=cutoff.isoformat())
    return {"deleted": deleted}


__all__ = ["check_pending_cases", "cleanup_attachments_90d"]
