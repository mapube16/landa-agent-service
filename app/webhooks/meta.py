"""Meta Cloud API webhook receiver — implemented in Plan 02-02, extended Plan 03-05.

Endpoints (D-09):

- ``GET /webhooks/meta``  — Meta subscription challenge. Compares
  ``hub.verify_token`` query param against
  ``settings.whatsapp.verify_token``; returns ``hub.challenge`` on
  match, 403 otherwise.
- ``POST /webhooks/meta`` — Inbound message events.

**INVARIANT — D-15 message processing order:**

    HMAC -> parse -> dedup -> allowlist -> firewall -> graph dispatch

This order is **not negotiable**. Reordering breaks the threat model:

  1. HMAC first means we never spend CPU parsing untrusted JSON until
     authenticity is proved (T-02-04).
  2. Parse before dedup means a malformed payload that *somehow* clears
     HMAC does not pollute the idempotency keyspace (Redis writes only
     happen for parseable messages).
  3. Dedup before allowlist means a replayed message is rejected even
     for non-allowlisted senders, preventing a malicious replay from
     toggling rate-limit budgets repeatedly (T-02-06).
  4. Allowlist before firewall prevents outbound leakage to unknown
     numbers (T-02-07).
  5. Firewall before graph dispatch: injected payload never reaches LLM
     (T-LLM01).

**NEVER read ``request.json()`` before ``await request.body()``** — the
body can only be consumed once and HMAC must be computed over the raw
bytes (RESEARCH Pitfall 1 + 10).

**HMAC comparison rule (D-16):** always ``hmac.compare_digest``, never
``==``. ``==`` short-circuits on the first mismatching byte and leaks
prefix length via timing (T-02-05).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from app.config.settings import settings
from app.features.handoff.echo import is_echo_allowed
from app.features.qa.messages import ESCAPE_REGEX, T_06
from app.integrations.meta_cloud import _hash_phone
from app.models.meta import InboundEnvelope, InboundMessage
from app.security.prompt_firewall import sanitize

# Explicit user reset commands — wipe the checkpoint so the user can recover
# from a stuck conversation (e.g. stale ``awaiting_policy_choice`` state).
_RESET_RE = re.compile(
    r"^\s*(hola|reiniciar|reinicio|menu|men[uú]|empezar|inicio|nuevo|salir)\s*\W*\s*$",
    re.IGNORECASE,
)

router = APIRouter(prefix="/webhooks", tags=["meta"])
log = structlog.get_logger("webhooks.meta")


def _normalize_e164(raw: str) -> str:
    """Return ``raw`` always prefixed with ``'+'``. Idempotent."""
    raw = raw.strip()
    return raw if raw.startswith("+") else "+" + raw


def _verify_signature(raw_body: bytes, header_value: str, secret: str) -> bool:
    """Constant-time HMAC SHA-256 verify for Meta ``X-Hub-Signature-256`` (D-16).

    NEVER use ``==``: that leaks the prefix-match length via timing
    (T-02-05). Always ``hmac.compare_digest``. D-16 makes this a hard
    rule, not a preference.
    """
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value)


def _log_task_error(task: asyncio.Task[Any]) -> None:
    """Callback for asyncio.create_task — logs exception WITHOUT exc.args (RESEARCH Pitfall 1)."""
    if exc := task.exception():
        log.error("qa_graph.task.error", error_type=type(exc).__name__)


def _extract_outbound(final_state: dict[str, Any]) -> str | None:
    """Extract the message that should be sent to the client.

    Preference order:
    1. The most recent AIMessage tagged ``send_to_client=True`` (judge-approved
       conversation responses).
    2. The most recent AIMessage without the tag (template messages like T-02,
       T-03, T-06, T-07, T-08, the policy list, the identification ack).

    HumanMessages are never returned — that's the user's own text.
    """
    from langchain_core.messages import AIMessage

    messages = final_state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.additional_kwargs.get("send_to_client"):
            return str(msg.content)
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = str(msg.content)
            if content.strip():
                return content
    return None


async def _send_outbound(app_state: Any, phone: str, outbound: str, wamid: str) -> None:
    """Send outbound message via meta + enqueue mirror job."""
    if not hasattr(app_state, "meta"):
        return
    try:
        await app_state.meta.send_text(to=phone, body=outbound)
    except Exception as exc:  # noqa: BLE001
        log.error("qa_graph.outbound.send_failed", error_type=type(exc).__name__)
    if hasattr(app_state, "arq") and app_state.arq is not None:
        try:
            await app_state.arq.enqueue_job(
                "mirror_outbound", phone=phone, text=outbound, wamid=wamid + ":out"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("qa_graph.mirror_outbound.failed", error_type=type(exc).__name__)


async def _run_and_dispatch(
    *,
    app_state: Any,
    initial_state: dict[str, Any],
    thread_id: str,
    phone: str,
    wamid: str,
) -> None:
    """Run qa_graph.ainvoke + dispatch outbound message + Chatwoot side-effects.

    Wrapped in asyncio.create_task by the webhook handler so it does not
    block the 200 OK response to Meta (Meta retries on non-200).
    """
    try:
        final_state = await app_state.qa_graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
        )
    except Exception as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", None)
        body = getattr(getattr(exc, "response", None), "text", "")[:300]
        url = str(getattr(getattr(exc, "request", None), "url", ""))[:200]
        log.error(
            "qa_graph.run_failed",
            error_type=type(exc).__name__,
            status=status,
            body=body,
            url=url,
        )
        return

    outbound = _extract_outbound(final_state)
    if outbound:
        await _send_outbound(app_state, phone, outbound, wamid)

    # Chatwoot mark_resolved on terminal states
    terminal_node = final_state.get("node")
    if terminal_node in ("escalating", "closed") and hasattr(app_state, "chatwoot"):
        try:
            conv_id = getattr(app_state, "_chatwoot_conv_cache", {}).get(thread_id)
            if conv_id:
                await app_state.chatwoot.mark_resolved(conv_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("qa_graph.chatwoot.mark_resolved.failed", error_type=type(exc).__name__)


@router.get("/meta")
async def verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
) -> Response:
    """GET challenge per Meta webhook subscription flow (D-09, D-17).

    Meta sends ``hub.mode=subscribe`` once when the operator configures
    the webhook URL in the Meta dashboard. We echo back ``hub.challenge``
    as plain text iff ``hub.verify_token`` matches the secret-stored
    verify token. Plain stdlib ``==`` is acceptable here because
    ``verify_token`` is set by the operator and exists only at
    webhook-setup time — there is no timing-attack vector on a one-shot
    config-time check.
    """
    if (
        hub_mode == "subscribe"
        and hub_verify_token == settings.whatsapp.verify_token.get_secret_value()
    ):
        return PlainTextResponse(content=hub_challenge, status_code=200)
    log.warning("webhook.verify.invalid", mode=hub_mode, has_token=bool(hub_verify_token))
    raise HTTPException(status_code=403, detail="forbidden")


@router.post("/meta")
async def receive(request: Request) -> Response:
    """Receive Meta webhook events (HMAC -> parse -> dedup -> allowlist -> firewall -> graph).

    Returns HTTP 200 once HMAC succeeds, regardless of downstream
    decisions (skip, dedup-hit, dispatch). Returning 5xx would make
    Meta retry for up to 24h (Pitfall 7), and malformed payloads cannot
    be fixed by retry — so we acknowledge to silence the retry loop and
    log loudly instead.
    """
    # 1. Raw body MUST be captured before any json parse (Pitfall 1, 10).
    raw = await request.body()

    # 2. HMAC verify (D-16). Reject without parsing if invalid.
    header_sig = request.headers.get("X-Hub-Signature-256", "")
    if not header_sig or not _verify_signature(
        raw, header_sig, settings.whatsapp.webhook_secret.get_secret_value()
    ):
        log.warning("webhook.hmac.invalid", header_present=bool(header_sig))
        raise HTTPException(status_code=401, detail="invalid signature")

    # 3. Pydantic parse only after HMAC succeeded.
    try:
        envelope = InboundEnvelope.model_validate_json(raw)
    except ValidationError as exc:
        log.warning("webhook.malformed", error=type(exc).__name__, error_count=exc.error_count())
        return Response(status_code=200)

    # 4. Per-message dispatch (D-15 order: dedup -> allowlist -> firewall -> graph).
    meta = request.app.state.meta
    redis = request.app.state.redis

    for entry in envelope.entry:
        for change in entry.changes:
            value = change.value
            # 4a. Status updates — acknowledge only (D-05).
            if value.statuses is not None:
                for status in value.statuses:
                    log.info(
                        "webhook.status.received",
                        status=status.get("status"),
                        recipient_hash=_hash_phone(str(status.get("recipient_id", ""))),
                        result="status_received",
                    )
                continue

            for msg in value.messages or []:
                await _dispatch_message(msg=msg, meta=meta, redis=redis, request=request)

    return Response(status_code=200)


async def _reset_if_closed(app_state: Any, thread_id: str) -> None:
    """If the thread's last state.node == 'closed', reset checkpoint."""
    checkpointer = getattr(app_state, "checkpointer", None)
    if checkpointer is None:
        return
    try:
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        existing = await checkpointer.aget(config)
        if existing is not None:
            channel_values = existing.get("channel_values", {})
            # Reset on any terminal node so next message starts fresh
            if channel_values.get("node") in ("closed", "escalating"):
                if hasattr(checkpointer, "adelete_thread"):
                    await checkpointer.adelete_thread(thread_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook.checkpoint.read_failed", error_type=type(exc).__name__)


async def _force_reset(app_state: Any, thread_id: str) -> None:
    """Delete the checkpoint unconditionally — used for explicit reset commands."""
    checkpointer = getattr(app_state, "checkpointer", None)
    if checkpointer is None or not hasattr(checkpointer, "adelete_thread"):
        return
    try:
        await checkpointer.adelete_thread(thread_id)
        log.info("webhook.checkpoint.force_reset", thread_hash=_hash_phone(thread_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook.checkpoint.force_reset_failed", error_type=type(exc).__name__)


async def _send_searching_ack(*, app_state: Any, thread_id: str, phone: str, meta: Any) -> None:
    """Send a brief 'searching' text when the thread is awaiting document input.

    Peeks at the checkpointer state; if asked_for_doc=True the next graph turn
    will call SoftSeguros — send an ack so the user doesn't feel abandoned.
    """
    checkpointer = getattr(app_state, "checkpointer", None)
    if checkpointer is None:
        return
    try:
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        existing = await checkpointer.aget(config)
        if existing is None:
            return
        channel_values = existing.get("channel_values", {})
        if (
            channel_values.get("asked_for_doc")
            and channel_values.get("node") == "awaiting_identification"
        ):
            await meta.send_text(to=phone, body="Buscando tu información, un momento... 🔍")
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook.searching_ack.failed", error_type=type(exc).__name__)


async def _handle_text_message(
    *, msg: InboundMessage, meta: Any, phone_hash: str, request: Request
) -> None:
    """Firewall + escape hatch + graph dispatch for inbound text messages."""
    raw_text = msg.text.body  # type: ignore[union-attr]

    # Step 1: Prompt firewall (D-15, T-LLM01). Blocked → send T-06, no graph.
    sanitize_result = sanitize(raw_text)
    if sanitize_result.blocked:
        log.warning(
            "webhook.firewall.blocked",
            reason=sanitize_result.reason,
            phone_hash=phone_hash,
            result="blocked_firewall",
        )
        try:
            await meta.send_text(to=msg.from_, body=T_06)
        except Exception as exc:  # noqa: BLE001
            log.error("webhook.firewall.send_t06.failed", error_type=type(exc).__name__)
        return

    # Step 2: Layer 1 escape hatch regex (D-15). Match → flag force_escalate.
    force_escalate = bool(ESCAPE_REGEX.search(raw_text))

    # Step 3: Build thread_id (E.164 normalized phone) + reset if closed.
    thread_id = _normalize_e164(msg.from_)
    app_state = request.app.state
    if _RESET_RE.match(raw_text):
        await _force_reset(app_state, thread_id)
    else:
        await _reset_if_closed(app_state, thread_id)

    # Step 3b: If the thread is awaiting a document, send a "searching" ack
    # immediately so the user sees activity while SoftSeguros is queried.
    await _send_searching_ack(app_state=app_state, thread_id=thread_id, phone=msg.from_, meta=meta)

    # Step 4: Build initial state and dispatch graph via asyncio.create_task.
    initial: dict[str, Any] = {
        "messages": [HumanMessage(content=sanitize_result.cleaned)],
        "force_escalate": force_escalate,
        "wa_phone": msg.from_,
    }
    if hasattr(app_state, "qa_graph"):
        task = asyncio.create_task(
            _run_and_dispatch(
                app_state=app_state,
                initial_state=initial,
                thread_id=thread_id,
                phone=msg.from_,
                wamid=msg.id,
            )
        )
        task.add_done_callback(_log_task_error)

    # Step 5: Mirror inbound via ARQ (async, non-blocking).
    if hasattr(app_state, "arq") and app_state.arq is not None:
        try:
            await app_state.arq.enqueue_job(
                "mirror_inbound", phone=msg.from_, text=raw_text, wamid=msg.id
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("webhook.mirror_inbound.failed", error_type=type(exc).__name__)

    log.info(
        "webhook.text.dispatched",
        message_id=msg.id,
        phone_hash=phone_hash,
        force_escalate=force_escalate,
        result="dispatched",
    )


async def _dispatch_message(
    *, msg: InboundMessage, meta: Any, redis: Any, request: Request
) -> None:
    """Apply dedup -> allowlist -> firewall -> graph dispatch, in that order (D-15).

    ``meta`` and ``redis`` are typed ``Any`` because their concrete types
    live in ``app.state`` (MetaCloudClient and redis.asyncio.Redis) and
    importing them at module scope would create circular deps. The
    functional contract is enforced by test stubs.
    """
    # 4b. Idempotency check (D-14, D-15 — MUST precede side effect).
    #     redis is binary-safe (decode_responses=False) so encode key + value.
    key = f"wa:msg:{msg.id}".encode()
    first_see = await redis.set(key, b"1", nx=True, ex=86400)
    if first_see is None:
        log.info(
            "webhook.dedup.skip",
            message_id=msg.id,
            message_type=msg.type,
            result="ignored_duplicate",
        )
        return

    phone_hash = _hash_phone(msg.from_)

    # 4c. Allowlist (D-02 + Pitfall 8 E.164 normalization).
    if not is_echo_allowed(msg.from_):
        log.info(
            "webhook.ignored.not_allowlisted",
            message_id=msg.id,
            message_type=msg.type,
            phone_hash=phone_hash,
            result="ignored_not_allowlisted",
        )
        return

    # 4d. Text message: firewall + escape hatch + graph dispatch (Plan 03-05).
    if msg.type == "text" and msg.text is not None:
        await _handle_text_message(msg=msg, meta=meta, phone_hash=phone_hash, request=request)
        return

    if msg.type in {"image", "audio", "sticker", "video", "document", "voice", "location"}:
        try:
            wamid = await meta.send_media_ack(to=msg.from_, media_type=msg.type)
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "webhook.echo.error",
                message_id=msg.id,
                phone_hash=phone_hash,
                error_type=type(exc).__name__,
                result="error",
            )
            return
        log.info(
            "webhook.echo.media.sent",
            message_id=msg.id,
            phone_hash=phone_hash,
            message_type=msg.type,
            outbound_wamid=wamid,
            result="echo_media_sent",
        )
        return

    # Unsupported types (contacts/interactive/button/unknown). Defensive log + skip.
    log.info(
        "webhook.unsupported_type",
        message_id=msg.id,
        message_type=msg.type,
        phone_hash=phone_hash,
        result="ignored_unsupported_type",
    )


__all__ = ["router"]
