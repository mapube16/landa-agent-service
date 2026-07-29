"""Bot mute during human takeover.

While a conversation is escalated (bot-initiated) or a human agent has
replied from Chatwoot (proactive takeover), the QA graph must NOT respond to
the client — otherwise every client reply restarts the bot mid human
conversation (found live 2026-07-29: post-escalation messages reset the
thread and the bot greeted the client again asking for their document).

State: Redis key ``bot:muted:{+E164}`` with a 24h TTL.

Unmute paths (three, so a client is never botless forever):
1. Agent hits "Resolver" in Chatwoot -> ``conversation_resolved`` webhook
   event -> ``clear_muted`` (webhooks/chatwoot.py).
2. The 24h TTL expires on its own.
3. Operator deletes the key manually.

All helpers are fail-open: Redis being down must never block message flow —
worst case the bot answers during a human chat (the pre-mute behavior).
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger("features.escalation.mute")

MUTE_TTL_SECONDS = 24 * 3600


def _normalize_e164(raw: str) -> str:
    """Return ``raw`` always prefixed with ``'+'``. Idempotent."""
    raw = raw.strip()
    return raw if raw.startswith("+") else "+" + raw


def _key(phone: str) -> bytes:
    return f"bot:muted:{_normalize_e164(phone)}".encode()


async def set_muted(redis: Any, phone: str) -> None:
    """Mark ``phone`` as human-attended; the bot stays silent for 24h max."""
    try:
        await redis.set(_key(phone), b"1", ex=MUTE_TTL_SECONDS)
        log.info("bot.muted", phone_tail=phone[-4:])
    except Exception as exc:  # noqa: BLE001
        log.warning("bot.mute.set_failed", error_type=type(exc).__name__)


async def is_muted(redis: Any, phone: str) -> bool:
    """True if the bot must not respond to ``phone`` right now."""
    try:
        return await redis.get(_key(phone)) is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("bot.mute.check_failed", error_type=type(exc).__name__)
        return False


async def clear_muted(redis: Any, phone: str) -> None:
    """Re-activate the bot for ``phone`` (agent resolved the conversation)."""
    try:
        await redis.delete(_key(phone))
        log.info("bot.unmuted", phone_tail=phone[-4:])
    except Exception as exc:  # noqa: BLE001
        log.warning("bot.mute.clear_failed", error_type=type(exc).__name__)


__all__ = ["MUTE_TTL_SECONDS", "clear_muted", "is_muted", "set_muted"]
