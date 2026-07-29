"""``GET /metrics/daily`` — WhatsApp-channel operational metrics for a day.

Consumed by the voice-side daily report (lambda-proyect) to fill the
WhatsApp hole it leaves open, and usable standalone for ops auditing.

Auth: same bearer as the Fase 6 handoff (``LAMBDA_PROYECT_INTERNAL_TOKEN``),
which the voice side already holds — no new secret to coordinate.
"""

from __future__ import annotations

import hmac
from datetime import UTC, date, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func, select

from app.config.settings import settings
from app.features.metrics.daily import day_range_utc, summarize
from app.security.audit_log import AuditLog

router = APIRouter(prefix="/metrics", tags=["metrics"])
log = structlog.get_logger("webhooks.metrics")


def _verify_bearer(authorization: str | None = Header(None)) -> None:
    """Constant-time bearer check (shared LAMBDA_PROYECT_INTERNAL_TOKEN)."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer")
    provided = authorization[7:].encode()
    expected = settings.lambda_proyect.internal_token.get_secret_value().encode()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid bearer")


@router.get("/daily", dependencies=[Depends(_verify_bearer)])
async def daily_metrics(
    request: Request,
    day: str = Query(default="", description="YYYY-MM-DD, Colombia day; default today"),
    audit: bool = Query(default=False, description="Run the LLM quality auditor (costs tokens)"),
) -> dict[str, object]:
    """Return the day's WhatsApp metrics (audit-log counts + Chatwoot labels).

    With ``audit=true``, additionally runs the LLM quality auditor over
    conversations that had real client activity and includes anomaly counts
    (intento de certificar pago, listas rotas, loops, frustración). Opt-in
    because it spends LLM tokens — the nightly report asks for it; cheap
    consumers omit it.
    """
    try:
        d = date.fromisoformat(day) if day else datetime.now(UTC).astimezone().date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD") from exc

    start, end = day_range_utc(d)

    # 1. audit_log action counts within the day (fail-soft to empty).
    action_counts: dict[str, int] = {}
    try:
        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            rows = await session.execute(
                select(AuditLog.action, func.count())
                .where(AuditLog.created_at >= start, AuditLog.created_at < end)
                .group_by(AuditLog.action)
            )
            action_counts = {action: n for action, n in rows.all()}
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics.daily.audit_failed", error_type=type(exc).__name__)

    # 2. Chatwoot label distribution over conversations active in the day.
    label_counts: dict[str, int] = {}
    total_conversations = 0
    chatwoot = getattr(request.app.state, "chatwoot", None)
    if chatwoot is not None:
        try:
            convs = await chatwoot.list_conversations(since_epoch=start.timestamp())
            total_conversations = len(convs)
            for c in convs:
                for lab in c.get("labels", []) or []:
                    label_counts[lab] = label_counts.get(lab, 0) + 1
        except Exception as exc:  # noqa: BLE001
            log.warning("metrics.daily.chatwoot_failed", error_type=type(exc).__name__)

    metrics = summarize(action_counts, label_counts, total_conversations)
    metrics["fecha"] = d.isoformat()
    metrics["canal"] = "whatsapp"

    if audit and chatwoot is not None:
        metrics.update(await _run_quality_audit(chatwoot, start.timestamp()))

    log.info("metrics.daily.served", fecha=d.isoformat(), audited=audit)
    return metrics


async def _run_quality_audit(chatwoot: Any, since_epoch: float) -> dict[str, int]:
    """LLM-audit conversations with real client activity (bounded concurrency).

    Only conversations where the client actually wrote are worth auditing (a
    template nobody answered has nothing to judge), keeping token cost
    proportional to real traffic. Fail-open to empty on any error.
    """
    import asyncio

    from app.features.metrics.quality import aggregate_quality, audit_conversation

    try:
        sem = asyncio.Semaphore(4)

        async def _audit_one(cid: int) -> Any:
            async with sem:
                msgs = await chatwoot.list_messages(cid)
                if any(m.get("message_type") == 0 for m in msgs):
                    return await audit_conversation(msgs)
                return None

        convs = await chatwoot.list_conversations(since_epoch=since_epoch)
        results = await asyncio.gather(
            *[_audit_one(c["id"]) for c in convs], return_exceptions=True
        )
        rubrics = [r for r in results if r is not None and not isinstance(r, BaseException)]
        return aggregate_quality(rubrics)
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics.daily.quality_audit_failed", error_type=type(exc).__name__)
        return {}


__all__ = ["router"]
