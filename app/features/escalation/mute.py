"""Bot mute during human takeover, with auto-recovery for abandoned escalations.

While a human owns a conversation the QA graph must NOT respond to the client,
or every client reply restarts the bot mid human chat (found live 2026-07-29).

But a bot-initiated escalation that no human ever picks up must NOT leave the
client botless for hours (found live 2026-07-29: a client tapped "hablar
humano" in a test, nobody resolved it, and the client stayed muted ~22h).

Two mute states, stored as the Redis key's VALUE:
- ``escalated`` — the bot escalated; no human has replied yet. Auto-expires
  after ``ESCALATED_GRACE_SECONDS`` of no human reply → the bot resumes.
- ``human`` — a human agent actually replied from Chatwoot (real takeover).
  Only cleared when the agent resolves the conversation (or the 24h TTL).

State: Redis key ``bot:muted:{+E164}`` = ``"{state}:{muted_at_epoch}"``, TTL 24h.

``is_muted`` is the single decision point: it returns False (and clears the
key) for an ``escalated`` mute whose grace window has passed, so an abandoned
escalation self-heals on the client's next message. A ``human`` mute ignores
the grace window entirely.

Unmute paths: agent resolves (Chatwoot ``conversation_resolved``), the 24h
TTL, the escalated-grace auto-expiry, or an operator deleting the key.

All helpers fail-open: Redis down must never block message flow — worst case
the bot answers during a human chat (the pre-mute behavior).
"""

from __future__ import annotations

import time
from typing import Any

import structlog

log = structlog.get_logger("features.escalation.mute")

MUTE_TTL_SECONDS = 24 * 3600
# An escalation nobody picks up resumes the bot after this much silence.
ESCALATED_GRACE_SECONDS = 30 * 60

STATE_ESCALATED = "escalated"
STATE_HUMAN = "human"


def _normalize_e164(raw: str) -> str:
    """Return ``raw`` always prefixed with ``'+'``. Idempotent."""
    raw = raw.strip()
    return raw if raw.startswith("+") else "+" + raw


def _key(phone: str) -> bytes:
    return f"bot:muted:{_normalize_e164(phone)}".encode()


async def _set(redis: Any, phone: str, state: str) -> None:
    # time.time() is real-world epoch for a grace window, not a wall-clock the
    # workflow layer forbids — this runs in the live webhook, not a workflow.
    value = f"{state}:{int(time.time())}".encode()
    await redis.set(_key(phone), value, ex=MUTE_TTL_SECONDS)


async def set_escalated(redis: Any, phone: str) -> None:
    """Bot escalated; mute is auto-recoverable if no human ever replies."""
    try:
        await _set(redis, phone, STATE_ESCALATED)
        log.info("bot.muted", phone_tail=phone[-4:], state=STATE_ESCALATED)
    except Exception as exc:  # noqa: BLE001
        log.warning("bot.mute.set_failed", error_type=type(exc).__name__)


async def set_human(redis: Any, phone: str) -> None:
    """A human agent replied — hard mute until the agent resolves (or 24h)."""
    try:
        await _set(redis, phone, STATE_HUMAN)
        log.info("bot.muted", phone_tail=phone[-4:], state=STATE_HUMAN)
    except Exception as exc:  # noqa: BLE001
        log.warning("bot.mute.set_failed", error_type=type(exc).__name__)


async def is_muted(redis: Any, phone: str) -> bool:
    """True if the bot must stay silent for ``phone`` right now.

    Self-healing: an ``escalated`` mute past its grace window is cleared and
    returns False, so an abandoned escalation resumes the bot on the client's
    next message. A ``human`` mute is never auto-cleared here.
    """
    try:
        raw = await redis.get(_key(phone))
        if raw is None:
            return False
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        state, _, ts = text.partition(":")
        if state == STATE_ESCALATED:
            muted_at = int(ts) if ts.isdigit() else 0
            if time.time() - muted_at >= ESCALATED_GRACE_SECONDS:
                await redis.delete(_key(phone))
                log.info("bot.unmuted", phone_tail=phone[-4:], reason="escalation_grace_expired")
                return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("bot.mute.check_failed", error_type=type(exc).__name__)
        return False


async def clear_muted(redis: Any, phone: str) -> None:
    """Re-activate the bot for ``phone`` (agent resolved the conversation)."""
    try:
        await redis.delete(_key(phone))
        log.info("bot.unmuted", phone_tail=phone[-4:], reason="cleared")
    except Exception as exc:  # noqa: BLE001
        log.warning("bot.mute.clear_failed", error_type=type(exc).__name__)


__all__ = [
    "ESCALATED_GRACE_SECONDS",
    "MUTE_TTL_SECONDS",
    "clear_muted",
    "is_muted",
    "set_escalated",
    "set_human",
]
