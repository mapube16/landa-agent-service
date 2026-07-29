"""Per-conversation serialization lock for QA graph dispatch.

Each inbound message spawns ``_run_and_dispatch`` in its own asyncio task so
the webhook can return 200 fast. Without a lock, two messages from the same
client in quick succession (e.g. tapping "estado" then "coberturas") run two
graph invocations concurrently against the SAME LangGraph thread — they race
on the Postgres checkpoint and one (or both) fails silently, leaving the bot
"stuck" (found live 2026-07-29 with rapid button taps).

This is a best-effort Redis lock: ``SET NX`` with a short TTL, acquired before
the graph runs and released after. A second message waits (bounded polling)
for the first to finish, then proceeds — so replies come out in order instead
of colliding.

Fail-open: if Redis is unavailable the lock is skipped and the graph runs
anyway (back to the pre-lock behavior — occasional races, never a hard block).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog

log = structlog.get_logger("features.escalation.conv_lock")

LOCK_TTL_SECONDS = 30  # generous ceiling on a single graph turn (LLM + tools)
_WAIT_TOTAL_SECONDS = 25.0
_WAIT_STEP_SECONDS = 0.4


def _key(thread_id: str) -> bytes:
    return f"conv:lock:{thread_id}".encode()


@asynccontextmanager
async def conversation_lock(redis: Any, thread_id: str) -> AsyncIterator[bool]:
    """Hold a per-conversation lock for the duration of the block.

    Yields True if the lock was acquired (caller owns it and must proceed),
    or True after waiting for a prior holder to finish. Only yields False —
    "proceed without a lock" — when Redis itself is unavailable (fail-open).
    The lock is released on exit; a crashed holder is covered by the TTL.
    """
    if redis is None:
        yield True
        return

    acquired = False
    try:
        waited = 0.0
        while True:
            acquired = bool(await redis.set(_key(thread_id), b"1", ex=LOCK_TTL_SECONDS, nx=True))
            if acquired or waited >= _WAIT_TOTAL_SECONDS:
                break
            await asyncio.sleep(_WAIT_STEP_SECONDS)
            waited += _WAIT_STEP_SECONDS
        if not acquired:
            # Prior turn is taking unusually long; proceed anyway rather than
            # drop the client's message. Rare; logged for visibility.
            log.warning("conv_lock.wait_timeout", thread_tail=thread_id[-4:])
    except Exception as exc:  # noqa: BLE001
        log.warning("conv_lock.acquire_failed", error_type=type(exc).__name__)
        yield True
        return

    try:
        yield True
    finally:
        if acquired:
            try:
                await redis.delete(_key(thread_id))
            except Exception as exc:  # noqa: BLE001
                log.warning("conv_lock.release_failed", error_type=type(exc).__name__)


__all__ = ["LOCK_TTL_SECONDS", "conversation_lock"]
