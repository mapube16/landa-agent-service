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
from app.security.rate_limiter import check_rate_limit

router = APIRouter(prefix="/case", tags=["handoff"])
log = structlog.get_logger("webhooks.handoff")


class NoAnswerHandoff(BaseModel):
    """Body contract for the lambda-proyect no-answer handoff (D-19).

    ``documento`` is optional (backward compatible): when VOICE sends it, the
    handoff resolves the SoftSeguros poliza id and seeds the QA thread so the
    client is NEVER asked for their document (client decision 2026-07-29:
    the bot must already know which poliza the call was about). Without it,
    the QA greeting still carries the poliza context but asks for the
    document to confirm identity.
    """

    phone: str = Field(pattern=r"^\+\d{8,15}$")  # E.164 (T-04-07-04)
    cliente_nombre: str = Field(min_length=1, max_length=80)
    numero_poliza: str = Field(min_length=1, max_length=40)
    case_id: uuid.UUID
    documento: str | None = Field(default=None, max_length=20)


class CaseHandoff(BaseModel):
    """Body contract for Contrato A (Fase 6) — VOICE cedes a live case to WA.

    See ``.planning/contracts/lambda-handoff-contract.md``.
    """

    case_id: uuid.UUID
    debtor_id: str = Field(min_length=1, max_length=80)
    poliza_number: str = Field(min_length=1, max_length=40)
    phone: str = Field(pattern=r"^\+\d{8,15}$")
    call_id: str | None = None
    user_id: str | None = None
    initial_context: str | None = None
    message: str | None = None


def _verify_bearer(authorization: str | None = Header(None)) -> None:
    """Constant-time bearer check against LAMBDA_PROYECT_INTERNAL_TOKEN (D-23)."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer")
    provided = authorization[7:].encode()
    expected = settings.lambda_proyect.internal_token.get_secret_value().encode()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid bearer")


async def _check_handoff_rate_limit(request: Request, phone: str) -> None:
    """Reuses the sliding-window limiter already proven in webhooks/meta.py —
    without it, a misbehaving or compromised voice-side caller could flood a
    real WhatsApp number with unlimited outbound sends (SEC-audit finding)."""
    redis = request.app.state.redis
    try:
        rl = await check_rate_limit(redis, phone=phone)
    except Exception as exc:  # noqa: BLE001 — same belt-and-suspenders as meta.py
        log.error("handoff.rate_limit_check_failed", error_type=type(exc).__name__)
        return
    if not rl.allowed:
        log.warning("handoff.rate_limited", scope=rl.scope)
        raise HTTPException(status_code=429, detail="rate limited")


@router.post("/handoff/no_answer", dependencies=[Depends(_verify_bearer)])
async def handoff_no_answer(body: NoAnswerHandoff, request: Request) -> dict[str, str | bool]:
    """Open a Case for the unanswered call and send the follow-up template.

    ponytail: SELECT-then-INSERT, not ON CONFLICT — lambda retries are
    sequential; switch to pg insert().on_conflict_do_nothing if concurrent
    retransmits ever appear.
    """
    await _check_handoff_rate_limit(request, body.phone)

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
        # Informe técnico §7 Mensaje 1: "Hola, {{1}} 👋" — the template takes
        # exactly one positional body param (cliente_nombre).
        body_params=[body.cliente_nombre],
        quick_reply_payloads=["si_ayudenme", "mas_tarde"],
    )
    log.info(
        "handoff.no_answer.template_sent",
        case_id=case_id,
        phone_hash=_hash_phone(body.phone),
    )

    # Mirror a trace into Chatwoot so the DPG team sees the outreach (the
    # template body lives in Meta, not here — a trace avoids copy drift).
    from app.features.payment.nodes import mirror_outgoing_to_chatwoot

    await mirror_outgoing_to_chatwoot(
        body.phone,
        f"[ARIA envió plantilla de seguimiento por llamada no contestada a "
        f"{body.cliente_nombre} — botones: Sí, ayúdenme / Más tarde]",
    )

    await _seed_qa_thread(request, body)

    return {"case_id": case_id, "sent": True}


async def _seed_qa_thread(request: Request, body: NoAnswerHandoff) -> None:
    """Seed the QA thread with the handoff context (fail-open).

    Always seeds ``cliente_nombre`` + ``handoff_numero_poliza`` so the
    greeting is contextual. When ``documento`` is present, additionally
    resolves documento -> cliente -> polizas -> matching numero and seeds
    ``poliza_id`` — the entry router then dispatches straight to
    ``answering_qa`` and the client is never asked for their document.

    RESET FIRST: a new handoff means a NEW conversation about a specific
    poliza — any prior thread state (old poliza_id, old polizas_list, old
    cliente_doc) is stale by definition. Seeding used to MERGE into leftover
    state and the client saw the PREVIOUS document's policies (found live
    2026-07-29 on the heavily-reused test phone).
    """
    qa_graph = getattr(request.app.state, "qa_graph", None)
    if qa_graph is None:
        return

    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
        try:
            await checkpointer.adelete_thread(body.phone)
            log.info("handoff.no_answer.thread_reset", phone_hash=_hash_phone(body.phone))
        except Exception as exc:  # noqa: BLE001
            log.warning("handoff.no_answer.thread_reset_failed", error_type=type(exc).__name__)

    seed: dict[str, object] = {
        "wa_phone": body.phone.lstrip("+"),
        "cliente_nombre": body.cliente_nombre,
        "handoff_numero_poliza": body.numero_poliza,
    }

    if body.documento:
        # La voz llamó a esta persona por su póliza: YA tenemos su documento,
        # NO debemos volver a pedírselo (decisión cliente 2026-07-29). Seedeamos
        # cliente_doc + handoff_poliza_hint; node_identify los usa al primer
        # mensaje para identificar directo (documento) y lockear la póliza del
        # payload si aparece — sin preguntar nada.
        seed["cliente_doc"] = body.documento
        seed["handoff_poliza_hint"] = body.numero_poliza

    try:
        await qa_graph.aupdate_state(
            {"configurable": {"thread_id": body.phone}},
            values=seed,
            as_node=None,
        )
        log.info(
            "handoff.no_answer.qa_thread_seeded",
            phone_hash=_hash_phone(body.phone),
            poliza_locked="poliza_id" in seed,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("handoff.no_answer.seed_failed", error_type=type(exc).__name__)


@router.post("/handoff", dependencies=[Depends(_verify_bearer)])
async def case_handoff(body: CaseHandoff, request: Request) -> dict[str, str | bool]:
    """Contrato A (Fase 6): VOICE cedes a live case to WhatsApp.

    Idempotent by ``case_id`` (same pattern as ``/handoff/no_answer``). Links
    the case to the voice world via ``debtor_id``/``call_ids`` so the payment
    nodes know to notify VOICE back (B1/B2) on approve/escalate.

    ponytail: freeform send only — no 24h-window/template fallback yet (no
    real closed-window handoff has appeared). Add when one does.
    """
    from app.security import audit_log

    await _check_handoff_rate_limit(request, body.phone)

    meta = request.app.state.meta
    session_factory = request.app.state.session_factory
    case_id = str(body.case_id)

    async with session_factory() as session:
        existing = (
            await session.execute(select(Case).where(Case.case_id == case_id))
        ).scalar_one_or_none()
        if existing is not None:
            log.info("handoff.case.idempotent_skip", case_id=case_id)
            return {"case_id": case_id, "sent": False}
        session.add(
            Case(
                case_id=case_id,
                phone=body.phone,
                poliza_id=body.poliza_number,
                status="awaiting_receipt",
                debtor_id=body.debtor_id,
                call_ids=[body.call_id] if body.call_id else [],
            )
        )
        await session.commit()

    sent = False
    if body.message:
        await meta.send_text(body.phone, body.message)
        sent = True

        from app.features.payment.nodes import mirror_outgoing_to_chatwoot

        await mirror_outgoing_to_chatwoot(body.phone, body.message)

    audit_log.emit_task(
        action="handoff_received",
        actor="voice",
        conversation_id=body.phone,
        poliza_id=body.poliza_number,
        payload={
            "case_id": case_id,
            "debtor_id": body.debtor_id,
            "call_id": body.call_id,
            "user_id": body.user_id,
        },
    )
    log.info(
        "handoff.case.ok",
        case_id=case_id,
        debtor_id=body.debtor_id,
        phone_hash=_hash_phone(body.phone),
        sent=sent,
    )
    return {"case_id": case_id, "sent": sent}
