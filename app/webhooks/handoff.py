"""Lambda-proyect handoff endpoint — Plan 04-07 (D-19, D-20, D-21, D-22, D-23).

``POST /case/handoff/no_answer`` is the trigger the voice agent calls when a
call went unanswered: it opens a payment Case (status ``awaiting_receipt``)
and sends the Meta UTILITY template ``voice_no_answer_followup`` that
re-opens the WhatsApp channel with the deudor. Template quick-reply taps
(``si_ayudenme`` / ``mas_tarde``) come back through the existing interactive
routing in ``webhooks/meta.py`` — nothing extra is wired here.

Auth (T-04-07-01): shared bearer token, compared with ``hmac.compare_digest``
(constant time — same timing-leak rule as the HMAC webhooks, D-16/D-23).

Idempotency (T-04-07-02): lambda may retransmit; the ``cases.case_id`` PK is
the dedup key. A retransmit returns 200 ``sent=false`` without a second
template send.

PII (T-04-07-03): only the hashed phone is logged; ``cliente_nombre`` never.
"""

from __future__ import annotations

import hmac
import uuid

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config.settings import settings
from app.integrations.meta_cloud import _hash_phone
from app.memory.case_store import Case

router = APIRouter(prefix="/case", tags=["handoff"])
log = structlog.get_logger("webhooks.handoff")


class NoAnswerHandoff(BaseModel):
    """Body contract for the lambda-proyect no-answer handoff (D-19)."""

    phone: str = Field(pattern=r"^\+\d{8,15}$")  # E.164 (T-04-07-04)
    cliente_nombre: str = Field(min_length=1, max_length=80)
    numero_poliza: str = Field(min_length=1, max_length=40)
    case_id: uuid.UUID


def _verify_bearer(authorization: str | None = Header(None)) -> None:
    """Constant-time bearer check against LAMBDA_PROYECT_INTERNAL_TOKEN (D-23)."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer")
    provided = authorization[7:].encode()
    expected = settings.lambda_proyect.internal_token.get_secret_value().encode()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid bearer")


@router.post("/handoff/no_answer", dependencies=[Depends(_verify_bearer)])
async def handoff_no_answer(body: NoAnswerHandoff, request: Request) -> dict[str, str | bool]:
    """Open a Case for the unanswered call and send the follow-up template.

    ponytail: SELECT-then-INSERT, not ON CONFLICT — lambda retries are
    sequential; switch to pg insert().on_conflict_do_nothing if concurrent
    retransmits ever appear.
    """
    meta = request.app.state.meta
    session_factory = request.app.state.session_factory
    case_id = str(body.case_id)

    async with session_factory() as session:
        existing = (
            await session.execute(select(Case).where(Case.case_id == case_id))
        ).scalar_one_or_none()
        if existing is not None:
            log.info("handoff.no_answer.idempotent_skip", case_id=case_id)
            return {"case_id": case_id, "sent": False}
        session.add(
            Case(
                case_id=case_id,
                phone=body.phone,
                cliente_nombre=body.cliente_nombre,
                poliza_id=body.numero_poliza,
                status="awaiting_receipt",
            )
        )
        await session.commit()

    await meta.send_template(
        body.phone,
        settings.payment.template_no_answer_name,
        "es",
        [body.cliente_nombre, body.numero_poliza],
        ["si_ayudenme", "mas_tarde"],
    )
    log.info(
        "handoff.no_answer.template_sent",
        case_id=case_id,
        phone_hash=_hash_phone(body.phone),
    )
    return {"case_id": case_id, "sent": True}
