"""Payment graph node functions — Plan 04-04.

Five async LangGraph node functions driving the comprobante validation flow:
  node_receive_comprobante  — download, magic-byte-gate, store, insert DB rows
  node_forward_to_cartera   — upload to Meta CDN, send to cartera with buttons
  node_awaiting_cartera     — interrupt() until cartera taps a button
  node_confirming           — emit confirmation message, set payment_approved=True
  node_payment_escalate     — open Chatwoot and emit escalation message

I/O dependencies (meta client, chatwoot client, session factory) are accessed
via module-level provider functions (``_get_meta``, ``_get_chatwoot``,
``_session_factory_fn``) so tests can monkeypatch them without touching the
real singletons.

Security invariants:
  D-27 — comprobante bytes NEVER enter the messages list or any LLM call.
  D-28 — ``payment_approved=True`` is ONLY set in node_confirming.
  T-04-04-05 — no LLM path can generate "pago confirmado" outside this node.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from app.features.payment.business_hours import is_business_time, next_business_window_after
from app.features.payment.storage import store_attachment

log = structlog.get_logger("features.payment.nodes")

# Terminal case statuses — any of these triggers a fresh case_id (D-09).
_TERMINAL_STATUSES = frozenset({"approved", "rejected", "escalated", "closed"})

# ──────────────────────────────────────────────────────────────────────────────
# Provider functions (monkeypatch targets for tests)
# ──────────────────────────────────────────────────────────────────────────────


def _get_meta() -> Any:
    """Return the cached MetaCloudClient singleton."""
    from app.integrations.meta_cloud import get_meta_client

    return get_meta_client()


def _get_chatwoot() -> Any:
    """Return the cached ChatwootClient singleton."""
    from app.integrations.chatwoot import get_chatwoot_client

    return get_chatwoot_client()


def _session_factory_fn() -> Any:  # type: ignore[return]
    """Return an async context manager for a DB session.

    This is the monkeypatch target; real calls go through session_scope.
    Returns an async context manager that yields an AsyncSession.

    Tests replace this with an ``asynccontextmanager`` factory that yields
    a mock session.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager  # type: ignore[misc]
    async def _real() -> Any:  # type: ignore[misc]
        from app.config.db import session_scope
        from app.main import app as _app  # pragma: no cover

        async with session_scope(_app.state.session_factory) as s:  # pragma: no cover
            yield s  # pragma: no cover

    return _real()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


async def _get_or_create_case(
    session: Any,
    phone: str,
    poliza_id: str | None,
    cliente_doc: str | None,
    cliente_nombre: str | None,
) -> tuple[Any, bool]:
    """Return ``(case, is_new)`` for the given phone.

    Looks up the most recent case row by phone (descending created_at).
    - If none exists OR the existing case is in a terminal status → INSERT new.
    - Otherwise → reuse the existing case (D-07).

    Returns the Case ORM object and a bool indicating whether it was freshly
    inserted.
    """
    import sqlalchemy as sa

    from app.memory.case_store import Case

    stmt = sa.select(Case).where(Case.phone == phone).order_by(Case.created_at.desc()).limit(1)
    result = await session.execute(stmt)
    existing = result.scalars().first()

    if existing is None or existing.status in _TERMINAL_STATUSES:
        new_case = Case(
            case_id=str(uuid.uuid4()),
            phone=phone,
            poliza_id=poliza_id,
            cliente_doc=cliente_doc,
            cliente_nombre=cliente_nombre,
            status="awaiting_receipt",
            attachment_count=0,
        )
        session.add(new_case)
        await session.flush()  # assign case_id server-side if needed
        log.info("payment.case.created", case_id=new_case.case_id)
        return new_case, True

    log.info("payment.case.reused", case_id=existing.case_id, status=existing.status)
    return existing, False


# ──────────────────────────────────────────────────────────────────────────────
# node_receive_comprobante
# ──────────────────────────────────────────────────────────────────────────────


async def node_receive_comprobante(state: dict[str, Any]) -> dict[str, Any]:
    """Download, magic-byte-gate, store, and insert DB rows for a comprobante.

    Reads ``state["_inbound_media"]`` (dict with media_id, mime_type, wamid)
    injected by the ARQ job (``process_attachment``).

    On success returns::
        {"case_id": <uuid>, "attachment_count": N, "payment_status": "forwarded"}

    On magic_byte_mismatch / attachment_too_large → sends rejection text,
    returns ``{"payment_status": "awaiting_receipt"}`` (no DB mutation).

    Security (D-27): comprobante bytes are NEVER added to ``state["messages"]``
    or passed to any LLM.
    """

    from app.memory.case_store import Attachment

    meta = _get_meta()
    inbound: dict[str, Any] = state.get("_inbound_media") or {}
    media_id: str = inbound.get("media_id", "")
    wamid: str = inbound.get("wamid", "")
    phone: str = state.get("wa_phone") or state.get("thread_id", "")
    poliza_id: str | None = state.get("poliza_id")
    cliente_doc: str | None = state.get("cliente_doc")
    cliente_nombre: str | None = state.get("cliente_nombre")

    # Download from Meta CDN (download_media already gates on file_size D-25).
    data, declared_mime = await meta.download_media(media_id)

    # Gate: magic-byte + size — failures short-circuit, no DB write.
    try:
        async with _make_session_ctx() as session:
            case, _is_new = await _get_or_create_case(
                session, phone, poliza_id, cliente_doc, cliente_nombre
            )
            case_id: str = case.case_id

            # store_attachment raises ValueError on gate failure.
            path, sha = store_attachment(data, case_id, wamid, declared_mime)

            # INSERT attachment row.
            att = Attachment(
                case_id=case_id,
                path=str(path),
                mime_type=declared_mime,
                sha256=sha,
                size_bytes=len(data),
                meta_media_id=media_id,
            )
            session.add(att)

            # Increment attachment_count on the case.
            case.attachment_count = (case.attachment_count or 0) + 1
            new_count: int = case.attachment_count
            case.status = "forwarded"

            # Flush so the INSERT is sent to DB within this transaction.
            await session.flush()

        log.info("payment.receive.ok", case_id=case_id, count=new_count)
        return {
            "case_id": case_id,
            "attachment_count": new_count,
            "payment_status": "forwarded",
        }

    except ValueError as exc:
        err = str(exc)
        if err == "magic_byte_mismatch":
            msg = "El archivo no parece valido, intenta con otro formato."
        elif err == "attachment_too_large":
            msg = "El archivo supera 5 MB, intenta con otro mas liviano."
        else:
            raise

        log.warning("payment.receive.rejected", reason=err, phone_hash=phone[:6])
        await meta.send_text(phone, msg)
        return {"payment_status": "awaiting_receipt"}


# ──────────────────────────────────────────────────────────────────────────────
# node_forward_to_cartera
# ──────────────────────────────────────────────────────────────────────────────


async def node_forward_to_cartera(state: dict[str, Any]) -> dict[str, Any]:
    """Upload attachments to Meta and forward to cartera with buttons on the last.

    Outside business hours (D-13): send D-13 ack to client, defer forward,
    set work_hours_due_at, return ``payment_status="awaiting_cartera"``.

    During business hours: upload each attachment via upload_media, send_media
    to the first cartera number. Only the LAST send_media call carries the 3
    buttons ({action}|{case_id}). Saves cartera_message_wamid.

    Returns state update with ``payment_status="awaiting_cartera"``.
    """
    import sqlalchemy as sa

    from app.config.settings import settings
    from app.memory.case_store import Case

    meta = _get_meta()
    phone: str = state.get("wa_phone") or state.get("thread_id", "")
    case_id: str = state.get("case_id", "")
    poliza_id: str | None = state.get("poliza_id")
    cliente_doc: str | None = state.get("cliente_doc")
    cliente_nombre: str | None = state.get("cliente_nombre") or "Cliente"
    now = datetime.now(UTC)

    # D-13: outside business hours → ack + defer
    if not is_business_time(now):
        ack = (
            "Recibimos tu comprobante. Cartera revisa en horario laboral "
            "(L-V 8-12 + 14-16). Te confirmamos cuando este validado."
        )
        await meta.send_text(phone, ack)

        # Compute defer time (next window + 20 min buffer)
        next_window = next_business_window_after(now)
        due_at = next_window + timedelta(minutes=20)

        async with _make_session_ctx() as session:
            result = await session.execute(sa.select(Case).where(Case.case_id == case_id))
            case = result.scalars().first()
            if case:
                case.work_hours_due_at = due_at
                await session.flush()

        log.info("payment.forward.deferred", case_id=case_id, due_at=due_at.isoformat())
        return {"payment_status": "awaiting_cartera"}

    # Business hours: determine cartera recipient.
    cartera_list = list(settings.payment.cartera_phone_allowlist)
    if not cartera_list:
        # Allowlist empty — escalate via Chatwoot with loud log.
        log.error(
            "payment.forward.no_cartera",
            case_id=case_id,
            reason="CARTERA_PHONE_ALLOWLIST is empty",
        )
        chatwoot = _get_chatwoot()
        conv_id = await chatwoot.get_or_create_conversation(phone)
        await chatwoot.post_message(
            conv_id,
            f"ERROR: CARTERA_PHONE_ALLOWLIST vacio — caso {case_id} no reenviado.",
            message_type="outgoing",
        )
        return {"payment_status": "escalated"}

    # v1: single cartera — send to the first entry.
    # ponytail: multiple cartera routing needs Phase 5+ ADR.
    cartera_phone = cartera_list[0]

    # Load case + attachments from DB.
    async with _make_session_ctx() as session:
        result = await session.execute(sa.select(Case).where(Case.case_id == case_id))
        case = result.scalars().first()
        if case is None:
            log.error("payment.forward.case_not_found", case_id=case_id)
            return {"payment_status": "escalated"}

        attachments = list(case.attachments)
        total = len(attachments)

        if total == 0:
            log.error("payment.forward.no_attachments", case_id=case_id)
            return {"payment_status": "escalated"}

        # Build context fields from case or state.
        nombre = case.cliente_nombre or cliente_nombre
        doc = case.cliente_doc or cliente_doc or "N/A"
        poliza = case.poliza_id or poliza_id or "N/A"
        created = case.created_at or now
        ts_co = created.strftime("%Y-%m-%d %H:%M")

        last_wamid: str = ""
        for idx, att in enumerate(attachments, start=1):
            from pathlib import Path

            uploaded_id = await meta.upload_media(Path(att.path), att.mime_type)
            caption = (
                f"Comprobante [{idx}/{total}] - Caso #{case_id}\n"
                f"Cliente: {nombre} (Doc: {doc})\n"
                f"Poliza: POL-{poliza}\n"
                f"Recibido: {ts_co}"
            )
            media_kind = "image" if att.mime_type.startswith("image/") else "document"

            if idx == total:
                # Last attachment carries the 3 action buttons.
                buttons = [
                    (f"aprobar|{case_id}", "Aprobar"),
                    (f"rechazar|{case_id}", "Rechazar"),
                    (f"info|{case_id}", "Mas info"),
                ]
            else:
                buttons = None

            last_wamid = await meta.send_media(
                cartera_phone, uploaded_id, media_kind, caption=caption, buttons=buttons
            )

        # Persist cartera_message_wamid and update status.
        case.cartera_message_wamid = last_wamid
        case.status = "awaiting_cartera"
        case.work_hours_due_at = now + timedelta(minutes=20)
        await session.flush()

    log.info("payment.forward.ok", case_id=case_id, total=total, wamid=last_wamid)
    return {
        "payment_status": "awaiting_cartera",
        "cartera_message_wamid": last_wamid,
    }


# ──────────────────────────────────────────────────────────────────────────────
# node_awaiting_cartera
# ──────────────────────────────────────────────────────────────────────────────


async def node_awaiting_cartera(state: dict[str, Any]) -> dict[str, Any]:
    """Suspend graph execution via LangGraph interrupt() until cartera taps a button.

    On first entry: calls ``interrupt({"waiting_for": "cartera_tap", ...})``;
    LangGraph serialises the checkpoint and resumes when ``aupdate_state`` is
    called by Plan 04-05's cartera-button handler.

    On resume: the value returned by ``interrupt()`` is a dict shaped
    ``{"action": "aprobar"|"rechazar"|"info", "extra": str|None}``
    injected by Plan 04-05.

    "info" action: emit extra text to client and stay in awaiting_cartera
    (Plan 04-05 drives the second loop).
    """
    case_id: str = state.get("case_id", "")
    phone: str = state.get("wa_phone") or state.get("thread_id", "")

    # interrupt() suspends execution here; resumes with the dict that
    # Plan 04-05 injects via graph.aupdate_state().
    decision: dict[str, Any] = interrupt({"waiting_for": "cartera_tap", "case_id": case_id})

    action: str = decision.get("action", "")
    extra: str | None = decision.get("extra")

    if action == "aprobar":
        return {"payment_status": "approved"}

    if action == "rechazar":
        return {"payment_status": "escalated"}

    # "info" path — relay cartera's message to client and stay awaiting.
    if action == "info" and extra:
        meta = _get_meta()
        await meta.send_text(phone, extra)

    return {"payment_status": "awaiting_cartera"}


# ──────────────────────────────────────────────────────────────────────────────
# node_confirming
# ──────────────────────────────────────────────────────────────────────────────


async def node_confirming(state: dict[str, Any]) -> dict[str, Any]:
    """Emit payment confirmation message and set payment_approved=True (D-28).

    This is the ONLY node that may set ``payment_approved=True`` and emit the
    "pago confirmado" text. The output firewall (Plan 04-05/04-08) blocks that
    text on any other path.

    Returns::
        {"messages": [AIMessage(...)], "payment_approved": True, "payment_status": "approved"}
    """
    import sqlalchemy as sa

    from app.memory.case_store import Case

    case_id: str = state.get("case_id", "")
    poliza_id: str | None = state.get("poliza_id") or "N/A"

    # Update case status to approved.
    async with _make_session_ctx() as session:
        result = await session.execute(sa.select(Case).where(Case.case_id == case_id))
        case = result.scalars().first()
        if case:
            case.status = "approved"
            await session.flush()

    content = f"Tu pago fue confirmado para la poliza POL-{poliza_id}. Gracias."
    msg = AIMessage(
        content=content,
        additional_kwargs={"payment_approved": True, "send_to_client": True},
    )

    log.info("payment.confirming.ok", case_id=case_id)
    return {
        "messages": [msg],
        "payment_approved": True,
        "payment_status": "approved",
    }


# ──────────────────────────────────────────────────────────────────────────────
# node_payment_escalate
# ──────────────────────────────────────────────────────────────────────────────


async def node_payment_escalate(state: dict[str, Any]) -> dict[str, Any]:
    """Escalate to Chatwoot and emit escalation message to client.

    Reuses Phase 3 escape-hatch (``get_or_create_conversation`` +
    ``post_message``) to create/find the Chatwoot conversation and leave a
    private note for the agent (Plan 04-04 action spec).

    Returns::
        {"messages": [AIMessage(...)], "payment_status": "escalated"}
    """
    import sqlalchemy as sa

    from app.memory.case_store import Case

    phone: str = state.get("wa_phone") or state.get("thread_id", "")
    case_id: str = state.get("case_id", "")
    chatwoot = _get_chatwoot()

    # Open / find Chatwoot conversation for this phone.
    conv_id = await chatwoot.get_or_create_conversation(phone)

    # Leave an internal note for the agent.
    await chatwoot.post_message(
        conv_id,
        f"Caso de pago requiere revision humana — case_id={case_id}",
        message_type="outgoing",
    )

    # Update case status.
    now = datetime.now(UTC)
    async with _make_session_ctx() as session:
        result = await session.execute(sa.select(Case).where(Case.case_id == case_id))
        case = result.scalars().first()
        if case:
            case.status = "escalated"
            case.escalated_at = now
            await session.flush()

    content = "La revision esta tardando. Te conecto con un agente."
    msg = AIMessage(content=content, additional_kwargs={"send_to_client": True})

    log.info("payment.escalate.ok", case_id=case_id, conv_id=conv_id)
    return {
        "messages": [msg],
        "payment_status": "escalated",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Internal async context manager helper
# ──────────────────────────────────────────────────────────────────────────────


def _make_session_ctx() -> Any:  # type: ignore[return]
    """Return an async context manager for a DB session.

    Delegates to the patchable ``_session_factory_fn``.
    """
    return _session_factory_fn()


__all__ = [
    "node_awaiting_cartera",
    "node_confirming",
    "node_forward_to_cartera",
    "node_payment_escalate",
    "node_receive_comprobante",
]
