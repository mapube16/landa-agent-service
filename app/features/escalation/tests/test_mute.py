"""Tests for the human-takeover bot mute (features/escalation/mute.py)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

from app.features.escalation.mute import (
    ESCALATED_GRACE_SECONDS,
    MUTE_TTL_SECONDS,
    clear_muted,
    is_muted,
    set_escalated,
    set_human,
)


async def test_set_escalated_stores_state_and_ttl() -> None:
    redis = AsyncMock()
    await set_escalated(redis, "573123528153")  # Meta sends without '+'
    key, value = redis.set.await_args.args
    assert key == b"bot:muted:+573123528153"
    assert value.startswith(b"escalated:")
    assert redis.set.await_args.kwargs["ex"] == MUTE_TTL_SECONDS


async def test_set_human_stores_human_state() -> None:
    redis = AsyncMock()
    await set_human(redis, "+573123528153")
    _, value = redis.set.await_args.args
    assert value.startswith(b"human:")


async def test_is_muted_true_for_fresh_escalation() -> None:
    redis = AsyncMock()
    redis.get.return_value = f"escalated:{int(time.time())}".encode()
    assert await is_muted(redis, "573123528153") is True
    redis.delete.assert_not_awaited()


async def test_is_muted_auto_recovers_stale_escalation() -> None:
    redis = AsyncMock()
    stale = int(time.time()) - ESCALATED_GRACE_SECONDS - 1
    redis.get.return_value = f"escalated:{stale}".encode()
    # Past the grace window with no human reply → bot resumes + key cleared.
    assert await is_muted(redis, "+573123528153") is False
    redis.delete.assert_awaited_once_with(b"bot:muted:+573123528153")


async def test_is_muted_human_never_auto_recovers() -> None:
    redis = AsyncMock()
    stale = int(time.time()) - ESCALATED_GRACE_SECONDS - 10_000
    redis.get.return_value = f"human:{stale}".encode()
    # A real human takeover stays muted regardless of age.
    assert await is_muted(redis, "+573123528153") is True
    redis.delete.assert_not_awaited()


async def test_is_muted_false_when_absent() -> None:
    redis = AsyncMock()
    redis.get.return_value = None
    assert await is_muted(redis, "573123528153") is False


async def test_clear_muted_deletes_key() -> None:
    redis = AsyncMock()
    await clear_muted(redis, "573123528153")
    redis.delete.assert_awaited_once_with(b"bot:muted:+573123528153")


async def test_helpers_fail_open_on_redis_errors() -> None:
    redis = AsyncMock()
    redis.set.side_effect = RuntimeError("redis down")
    redis.get.side_effect = RuntimeError("redis down")
    redis.delete.side_effect = RuntimeError("redis down")

    await set_escalated(redis, "+57300")  # no raise
    await set_human(redis, "+57300")  # no raise
    assert await is_muted(redis, "+57300") is False  # fail-open = not muted
    await clear_muted(redis, "+57300")  # no raise
