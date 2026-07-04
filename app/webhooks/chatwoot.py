"""Chatwoot outbound webhook receiver — Plan 04-03 (D-15, D-16, D-17, D-18).

``POST /webhooks/chatwoot`` relays human-agent messages (text + attachments)
back to the client via the Meta Cloud API, using the inverse Redis index
``chatwoot:phone_by_conv:{conv_id}`` populated by
``ChatwootClient.get_or_create_conversation``.

Processing order: HMAC -> parse -> filters (event / message_type / sender)
-> dedup -> phone resolve -> relay. Filters run BEFORE dedup so ignored
events never consume idempotency keys.

Loop prevention (D-15, T-04-03-03): bot mirror messages posted via
``ChatwootClient.post_message`` carry ``sender.type == "agent_bot"`` and are
dropped here; only human agents (``sender.type == "user"``) relay outbound.

Attachment security asymmetry (T-04-03-06): agent attachments come from
Chatwoot (authenticated humans — trusted source), so only the mime allowlist
applies here. Magic-byte validation lives on the inbound comprobante path
(Plan 04-04) where files come from untrusted clients.

Failure semantics: once HMAC passes we return 200 for every downstream
decision (ignored, dup, unresolved phone, relay error). Non-2xx would make
Chatwoot retry forever, and a retry cannot fix a permanently-bad payload —
we log loudly instead.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import mimetypes
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import anyio
import orjson
import structlog
from fastapi import APIRouter, HTTPException, Request

from app.config.settings import settings
from app.integrations.meta_cloud import _hash_phone

# Canonical allowlist ships in Plan 04-02 (same wave, parallel worktree).
# importlib keeps this module importable — and mypy-clean — until 04-02
# merges; afterwards the canonical constant is picked up automatically.
ALLOWED_MIME_TYPES: frozenset[str]
try:
    ALLOWED_MIME_TYPES = importlib.import_module(
        "app.features.payment.attachment"
    ).ALLOWED_MIME_TYPES
except ImportError:  # pragma: no cover — only until 04-02 merges
    ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "application/pdf"})

router = APIRouter(prefix="/webhooks", tags=["chatwoot"])
log = structlog.get_logger("webhooks.chatwoot")


def _verify_chatwoot_signature(
    raw_body: bytes, header_value: str, secret: str, timestamp: str = ""
) -> bool:
    """Constant-time HMAC SHA-256 verify for ``X-Chatwoot-Signature`` (T-04-03-01).

    Chatwoot signs ``"{X-Chatwoot-Timestamp}.{raw_body}"`` per its webhook docs;
    older builds sign the raw body alone (chatwoot/chatwoot#13809 documents the
    inconsistency), so both message forms are accepted. ``sha256=<hex>`` and
    bare hex header values are both supported. NEVER ``==`` — always
    ``hmac.compare_digest`` (same timing-leak rule as webhooks/meta.py, D-16).
    """
    value = header_value.removeprefix("sha256=")
    key = secret.encode("utf-8")
    messages = [raw_body]
    if timestamp:
        messages.append(f"{timestamp}.".encode() + raw_body)
    for msg in messages:
        expected = hmac.new(key, msg, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, value):
            return True
    return False


async def _relay_attachments(
    attachments: list[dict[str, Any]],
    *,
    phone: str,
    content: str,
    chatwoot: Any,
    meta: Any,
) -> None:
    """Re-upload Chatwoot attachments to Meta and send them (D-18).

    Only ``file_type`` image/file with an allowlisted mime relays; everything
    else is skipped with a log. The agent's text rides as caption on the
    first attachment only, so it is never duplicated.
    """
    chatwoot_host = urlparse(settings.chatwoot.url).netloc
    for i, att in enumerate(attachments):
        file_type = att.get("file_type")
        if file_type not in ("image", "file"):
            log.warning(
                "chatwoot.webhook.attachment.skipped", reason="file_type", file_type=file_type
            )
            continue
        data_url = str(att.get("data_url") or "")
        # T-04-03-05: only download from Chatwoot's own host (also keeps the
        # api_access_token header from leaking to a foreign host).
        if not data_url or urlparse(data_url).netloc != chatwoot_host:
            log.warning("chatwoot.webhook.attachment.skipped", reason="foreign_host")
            continue
        mime = (
            mimetypes.guess_type(att.get("file_name") or data_url)[0] or "application/octet-stream"
        )
        if mime not in ALLOWED_MIME_TYPES:
            log.warning("chatwoot.webhook.attachment.skipped", reason="mime", mime=mime)
            continue
        blob = await chatwoot.download_attachment(data_url)
        suffix = Path(urlparse(data_url).path).suffix or mimetypes.guess_extension(mime) or ""
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)  # noqa: SIM115
        try:
            tmp.write(blob)
            tmp.close()  # Windows: reopen-by-name requires the handle closed
            media_id = await meta.upload_media(Path(tmp.name), mime)
            media_type = "image" if mime.startswith("image/") else "document"
            caption = content if (i == 0 and content) else None
            await meta.send_media(phone, media_id, media_type, caption=caption)
        finally:
            await anyio.Path(tmp.name).unlink(missing_ok=True)


def _filter_reason(payload: dict[str, Any]) -> str | None:
    """Return the ignore-reason for a non-relayable event, or None to proceed.

    Filters (D-15): only ``message_created`` + ``outgoing`` + human sender
    (``sender.type == "user"``) relay. ``agent_bot`` mirrors are dropped —
    loop prevention (T-04-03-03).
    """
    if payload.get("event") != "message_created":
        return "event"
    if payload.get("message_type") != "outgoing":
        return "message_type"
    if (payload.get("sender") or {}).get("type") != "user":
        return "sender_type"
    return None


async def _resolve_and_relay(
    request: Request, payload: dict[str, Any], msg_id: str
) -> dict[str, Any]:
    """Resolve conv_id -> phone via inverse index and relay text + attachments."""
    conv_id = (payload.get("conversation") or {}).get("id")
    if conv_id is None:
        log.warning("chatwoot.webhook.ignored", reason="no_conversation", msg_id=msg_id)
        return {"ignored": "no_conversation"}

    chatwoot = request.app.state.chatwoot
    phone = await chatwoot.get_phone_by_conv(int(conv_id))
    if phone is None:
        # 200 on purpose: 5xx would make Chatwoot retry forever and the
        # retry cannot resolve a phone that does not exist.
        log.warning("chatwoot.webhook.phone_unresolved", conv_id=conv_id, msg_id=msg_id)
        return {"error": "phone_not_found"}

    meta = request.app.state.meta
    content = str(payload.get("content") or "")
    attachments: list[dict[str, Any]] = payload.get("attachments") or []
    try:
        if content and not attachments:
            await meta.send_text(phone, content)
        if attachments:
            await _relay_attachments(
                attachments, phone=phone, content=content, chatwoot=chatwoot, meta=meta
            )
    except Exception as exc:  # noqa: BLE001
        # ponytail: ack 200 + log on relay failure; add ARQ retry if losses appear.
        log.error(
            "chatwoot.webhook.relay_failed",
            msg_id=msg_id,
            conv_id=conv_id,
            to_hash=_hash_phone(phone),
            error_type=type(exc).__name__,
        )
        return {"error": "relay_failed"}

    log.info(
        "chatwoot.webhook.relayed",
        msg_id=msg_id,
        conv_id=conv_id,
        to_hash=_hash_phone(phone),
        n_attachments=len(attachments),
        content_len=len(content),
    )
    return {"ok": True, "msg_id": msg_id}


@router.post("/chatwoot")
async def receive(request: Request) -> dict[str, Any]:
    """Receive Chatwoot outbound webhook events (HMAC -> filters -> dedup -> relay)."""
    # Raw body MUST be captured before any json parse — HMAC is computed
    # over the raw bytes (same rule as webhooks/meta.py).
    raw = await request.body()

    header_sig = request.headers.get("X-Chatwoot-Signature", "")
    header_ts = request.headers.get("X-Chatwoot-Timestamp", "")
    if not header_sig or not _verify_chatwoot_signature(
        raw,
        header_sig,
        settings.chatwoot.webhook_secret.get_secret_value(),
        timestamp=header_ts,
    ):
        log.warning(
            "chatwoot.webhook.bad_signature",
            header_present=bool(header_sig),
            timestamp_present=bool(header_ts),
        )
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload: dict[str, Any] = orjson.loads(raw)
    except orjson.JSONDecodeError:
        log.warning("chatwoot.webhook.malformed")
        return {"ignored": "malformed"}

    reason = _filter_reason(payload)
    if reason is not None:
        log.info("chatwoot.webhook.ignored", reason=reason)
        return {"ignored": reason}

    raw_msg_id = payload.get("id")
    if raw_msg_id is None:
        log.warning("chatwoot.webhook.ignored", reason="no_id")
        return {"ignored": "no_id"}
    msg_id = str(raw_msg_id)

    # Dedup (D-17): Chatwoot may redeliver; SET NX with 24h TTL.
    redis = request.app.state.redis
    first_see = await redis.set(f"chatwoot:msg:{msg_id}".encode(), b"1", ex=86400, nx=True)
    if first_see is None:
        log.info("chatwoot.webhook.dedup.skip", msg_id=msg_id)
        return {"ignored": "dup"}

    return await _resolve_and_relay(request, payload, msg_id)


__all__ = ["router"]
