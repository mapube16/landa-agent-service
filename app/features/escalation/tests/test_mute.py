"""Tests for the human-takeover bot mute (features/escalation/mute.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.features.escalation.mute import MUTE_TTL_SECONDS, clear_muted, is_muted, set_muted


async def test_set_muted_uses_normalized_key_and_ttl() -> None:
    redis = AsyncMock()
    await set_muted(redis, "573123528153")  # Meta sends without '+'
    redis.set.assert_awaited_once_with(b"bot:muted:+573123528153", b"1", ex=MUTE_TTL_SECONDS)


async def test_is_muted_normalizes_both_directions() -> None:
    redis = AsyncMock()
    redis.get.return_value = b"1"
    assert await is_muted(redis, "+573123528153") is True
    redis.get.assert_awaited_once_with(b"bot:muted:+573123528153")

    redis.get.reset_mock()
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

    await set_muted(redis, "+57300")  # no raise
    assert await is_muted(redis, "+57300") is False  # fail-open = not muted
    await clear_muted(redis, "+57300")  # no raise
