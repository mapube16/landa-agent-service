"""Multi-level Redis sliding-window rate limiter (SEC-06).

Three concentric levels — per WhatsApp number, per poliza, global — evaluated
atomically via a Lua sorted-set script.  First level to exceed its limit wins
and returns that scope; all levels passing returns (True, None).

Key design choices:
- SHA-256-truncated digests as Redis key suffixes (Pitfall 2: key poisoning).
- Lua script: ZREMRANGEBYSCORE + ZCARD + conditional ZADD (no INCR/EXPIRE race).
- Fail-open on any Redis exception — the limiter is a shield, not a gate.
- Disabled path (settings.rate_limit.enabled=False) returns immediately with
  zero Redis calls.

Usage (wired in Plan 05-06 into _dispatch_message)::

    from app.security.rate_limiter import check_rate_limit, T_RATE_LIMITED

    result = await check_rate_limit(redis, phone=phone, poliza_id=poliza_id)
    if not result.allowed:
        await send_text(T_RATE_LIMITED)
        return

NEVER log raw phone or poliza_id — only scope + counts (CLAUDE.md PII rule).
"""

from __future__ import annotations

import hashlib
import math
import time
from typing import Any, NamedTuple

import structlog

from app.config.settings import settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Lua sliding-window script (RESEARCH Pattern 5, modified to return count).
#
# Returns -1 when the caller is rate-limited (count >= limit).
# Returns count+1 (>= 1) when the call is admitted (so callers can check
# the approaching threshold without a second round-trip).
#
# ARGV[1] = now_ms      (current epoch in milliseconds)
# ARGV[2] = window_ms   (sliding window size in milliseconds)
# ARGV[3] = limit       (max requests in window)
# ---------------------------------------------------------------------------

_SLIDING_WINDOW_LUA = """
local key     = KEYS[1]
local now     = tonumber(ARGV[1])
local window  = tonumber(ARGV[2])
local limit   = tonumber(ARGV[3])
local cutoff  = now - window

redis.call('ZREMRANGEBYSCORE', key, 0, cutoff)
local count = redis.call('ZCARD', key)

if count >= limit then
  return -1
end

redis.call('ZADD', key, now, tostring(now) .. '-' .. tostring(math.random()))
redis.call('EXPIRE', key, math.ceil(window / 1000) + 1)
return count + 1
"""


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class RateLimitResult(NamedTuple):
    """Result of a rate-limit evaluation.

    ``allowed=True, scope=None``  — all levels passed.
    ``allowed=False, scope=<str>`` — first level that was exceeded.
    """

    allowed: bool
    scope: str | None  # "phone" | "poliza" | "global" | None when allowed


# Client-facing Spanish message — lives here so this module owns the contract
# (not in qa/messages.py) and Plan 05-06 imports it directly.
T_RATE_LIMITED: str = (
    "Estas enviando muchos mensajes. Por favor espera un momento e intenta de nuevo."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _key(scope: str, raw: str) -> str:
    """Derive a Redis key from scope + raw identifier.

    sha256(raw)[:16] as a hex digest prevents key poisoning from crafted
    phone numbers or poliza IDs (RESEARCH Pitfall 2).
    """
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"rl:{scope}:{digest}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def check_rate_limit(
    redis: Any,  # redis.asyncio.Redis from app.state (binary-safe)
    *,
    phone: str,  # E.164-normalized (caller normalizes before passing)
    poliza_id: str | None = None,
) -> RateLimitResult:
    """Evaluate phone -> poliza -> global rate-limit levels atomically.

    Each level is checked via a single ``redis.eval`` call that runs the Lua
    sliding-window script atomically on the Redis server.

    Args:
        redis:     Binary-safe ``redis.asyncio.Redis`` instance from app.state.
        phone:     E.164-normalized WhatsApp number (e.g. "+573001234567").
        poliza_id: Optional poliza ID for the per-poliza level.

    Returns:
        ``RateLimitResult(True, None)`` — all levels passed; request allowed.
        ``RateLimitResult(False, scope)`` — first blocked level; request denied.

    Exceptions from Redis are caught and logged; the function returns
    ``(True, None)`` so a Redis outage never blocks legitimate traffic.
    """
    if not settings.rate_limit.enabled:
        return RateLimitResult(True, None)

    now_ms = int(time.time() * 1000)
    window_ms = settings.rate_limit.window_s * 1000

    # Build the ordered level list: phone, (poliza?), global.
    levels: list[tuple[str, str, int]] = [
        ("phone", _key("phone", phone), settings.rate_limit.phone_limit),
    ]
    if poliza_id is not None:
        levels.append(("poliza", _key("poliza", poliza_id), settings.rate_limit.poliza_limit))
    levels.append(("global", "rl:global", settings.rate_limit.global_limit))

    for scope, key, limit in levels:
        try:
            raw_result = await redis.eval(_SLIDING_WINDOW_LUA, 1, key, now_ms, window_ms, limit)
            result = int(raw_result)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "rate_limit.redis_failed",
                scope=scope,
                error_type=type(exc).__name__,
            )
            # Fail-open: continue to next level rather than blocking.
            continue

        if result == -1:
            log.warning(
                "rate_limit.exceeded",
                scope=scope,
                limit=limit,
            )
            return RateLimitResult(False, scope)

        # Alert at 80% utilisation — Sentry picks this up via the structlog
        # integration without a direct sentry_sdk call.
        if result >= math.ceil(limit * 0.8):
            log.warning(
                "rate_limit.approaching",
                scope=scope,
                count=result,
                limit=limit,
            )

    return RateLimitResult(True, None)


__all__ = ["check_rate_limit", "RateLimitResult", "T_RATE_LIMITED"]
