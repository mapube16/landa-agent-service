"""Meta Cloud API webhook receiver — implemented in Plan 02-02.

Endpoints (D-09):

- ``GET /webhooks/meta``  — Meta subscription challenge. Compares
  ``hub.verify_token`` query param against
  ``settings.whatsapp.verify_token``; returns ``hub.challenge`` on
  match, 403 otherwise.
- ``POST /webhooks/meta`` — Inbound message events.

**INVARIANT — D-15 message processing order:**

    HMAC -> parse -> dedup -> allowlist -> echo

This order is **not negotiable**. Reordering breaks the threat model:

  1. HMAC first means we never spend CPU parsing untrusted JSON until
     authenticity is proved (T-02-04).
  2. Parse before dedup means a malformed payload that *somehow* clears
     HMAC does not pollute the idempotency keyspace (Redis writes only
     happen for parseable messages).
  3. Dedup before allowlist means a replayed message is rejected even
     for non-allowlisted senders, preventing a malicious replay from
     toggling rate-limit budgets repeatedly (T-02-06).
  4. Allowlist before echo prevents outbound leakage to unknown
     numbers (T-02-07).

**NEVER read ``request.json()`` before ``await request.body()``** — the
body can only be consumed once and HMAC must be computed over the raw
bytes (RESEARCH Pitfall 1 + 10).

**HMAC comparison rule (D-16):** always ``hmac.compare_digest``, never
``==``. ``==`` short-circuits on the first mismatching byte and leaks
prefix length via timing (T-02-05).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from app.config.settings import settings
from app.features.handoff.echo import format_echo, is_echo_allowed
from app.integrations.meta_cloud import _hash_phone
from app.models.meta import InboundEnvelope, InboundMessage

router = APIRouter(prefix="/webhooks", tags=["meta"])
log = structlog.get_logger("webhooks.meta")


def _verify_signature(raw_body: bytes, header_value: str, secret: str) -> bool:
    """Constant-time HMAC SHA-256 verify for Meta ``X-Hub-Signature-256`` (D-16).

    NEVER use ``==``: that leaks the prefix-match length via timing
    (T-02-05). Always ``hmac.compare_digest``. D-16 makes this a hard
    rule, not a preference.
    """
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value)


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
    """Receive Meta webhook events (HMAC -> parse -> dedup -> allowlist -> echo).

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

    # 4. Per-message dispatch (D-15 order: dedup -> allowlist -> echo).
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
                await _dispatch_message(msg=msg, meta=meta, redis=redis)

    return Response(status_code=200)


async def _dispatch_message(*, msg: InboundMessage, meta: Any, redis: Any) -> None:
    """Apply dedup -> allowlist -> echo, in that order (D-15).

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

    # 4d. Echo dispatch.
    if msg.type == "text" and msg.text is not None:
        reply = format_echo(msg.text.body)
        try:
            wamid = await meta.send_text(to=msg.from_, body=reply)
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
            "webhook.echo.sent",
            message_id=msg.id,
            phone_hash=phone_hash,
            reply_len=len(reply),
            outbound_wamid=wamid,
            result="echo_sent",
        )
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
