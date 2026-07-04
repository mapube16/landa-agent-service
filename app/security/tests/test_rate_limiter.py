"""Unit tests for app.security.rate_limiter.

TDD: these tests were written BEFORE the implementation (RED phase).
All tests use a _StubRedis that records eval calls and returns
configurable responses. No real Redis required for non-integration tests.

Integration test (marked `pytest.mark.integration`, skipped without REDIS_URL):
  - real Redis client, exercises the sliding window end-to-end.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Stub Redis
# ---------------------------------------------------------------------------


class _StubRedis:
    """Minimal async Redis stub that records eval calls.

    ``responses`` is a list consumed left-to-right. Exhausted list returns 1.
    Pass ``ConnectionError`` instances (not raised yet) in ``responses`` to
    trigger the error path.
    """

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.responses: list[Any] = responses if responses is not None else []

    async def eval(
        self, script: str, numkeys: int, key: str, *args: Any
    ) -> Any:
        self.calls.append((key, args))
        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return 1  # default: count=1 (well below any limit)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings_stub(
    enabled: bool = True,
    phone_limit: int = 20,
    poliza_limit: int = 10,
    global_limit: int = 500,
    window_s: int = 60,
) -> Any:
    """Return a simple namespace that mimics settings.rate_limit."""

    class _RL:
        pass

    rl = _RL()
    rl.enabled = enabled  # type: ignore[attr-defined]
    rl.phone_limit = phone_limit  # type: ignore[attr-defined]
    rl.poliza_limit = poliza_limit  # type: ignore[attr-defined]
    rl.global_limit = global_limit  # type: ignore[attr-defined]
    rl.window_s = window_s  # type: ignore[attr-defined]

    class _Settings:
        rate_limit = rl

    return _Settings()


# ---------------------------------------------------------------------------
# Unit tests (no real Redis)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phone_only_two_evals() -> None:
    """With only phone (no poliza_id), exactly 2 evals: phone + global."""
    from app.security.rate_limiter import check_rate_limit

    stub = _StubRedis(responses=[1, 1])
    stub_settings = _make_settings_stub()

    with patch("app.security.rate_limiter.settings", stub_settings):
        result = await check_rate_limit(stub, phone="+573001234567")

    assert result.allowed is True
    assert result.scope is None
    assert len(stub.calls) == 2


@pytest.mark.asyncio
async def test_poliza_id_three_evals_in_order() -> None:
    """With poliza_id, exactly 3 evals in order: phone, poliza, global."""
    from app.security.rate_limiter import check_rate_limit

    stub = _StubRedis(responses=[1, 1, 1])
    stub_settings = _make_settings_stub()

    with patch("app.security.rate_limiter.settings", stub_settings):
        result = await check_rate_limit(
            stub, phone="+573001234567", poliza_id="POL-001"
        )

    assert result.allowed is True
    assert result.scope is None
    assert len(stub.calls) == 3

    # Order: phone, poliza, global
    phone_key = stub.calls[0][0]
    poliza_key = stub.calls[1][0]
    global_key = stub.calls[2][0]

    assert phone_key.startswith("rl:phone:")
    assert poliza_key.startswith("rl:poliza:")
    assert global_key == "rl:global"


@pytest.mark.asyncio
async def test_keys_do_not_contain_raw_phone() -> None:
    """Keys passed to eval must not contain the raw phone string."""
    from app.security.rate_limiter import check_rate_limit

    raw_phone = "+573001234567"
    stub = _StubRedis(responses=[1, 1])
    stub_settings = _make_settings_stub()

    with patch("app.security.rate_limiter.settings", stub_settings):
        await check_rate_limit(stub, phone=raw_phone)

    for key, _args in stub.calls:
        assert raw_phone not in key, (
            f"Raw phone found in Redis key: {key!r}"
        )


@pytest.mark.asyncio
async def test_phone_key_format() -> None:
    """Phone key starts with 'rl:phone:' followed by exactly 16 hex chars."""
    import re
    from app.security.rate_limiter import check_rate_limit

    stub = _StubRedis(responses=[1, 1])
    stub_settings = _make_settings_stub()

    with patch("app.security.rate_limiter.settings", stub_settings):
        await check_rate_limit(stub, phone="+573001234567")

    phone_key = stub.calls[0][0]
    assert re.fullmatch(r"rl:phone:[0-9a-f]{16}", phone_key), (
        f"Phone key has unexpected format: {phone_key!r}"
    )


@pytest.mark.asyncio
async def test_phone_blocked_short_circuits() -> None:
    """Phone level returning -1 blocks immediately; no further evals."""
    from app.security.rate_limiter import RateLimitResult, check_rate_limit

    stub = _StubRedis(responses=[-1])
    stub_settings = _make_settings_stub()

    with patch("app.security.rate_limiter.settings", stub_settings):
        result = await check_rate_limit(
            stub, phone="+573001234567", poliza_id="POL-001"
        )

    assert result == RateLimitResult(allowed=False, scope="phone")
    assert len(stub.calls) == 1  # no poliza or global eval


@pytest.mark.asyncio
async def test_global_blocked_returns_global_scope() -> None:
    """Only global returning -1 returns (False, 'global')."""
    from app.security.rate_limiter import RateLimitResult, check_rate_limit

    # phone=1 (ok), global=-1 (blocked). No poliza_id so only 2 evals.
    stub = _StubRedis(responses=[1, -1])
    stub_settings = _make_settings_stub()

    with patch("app.security.rate_limiter.settings", stub_settings):
        result = await check_rate_limit(stub, phone="+573001234567")

    assert result == RateLimitResult(allowed=False, scope="global")
    assert len(stub.calls) == 2


@pytest.mark.asyncio
async def test_connection_error_fail_open() -> None:
    """ConnectionError on every eval -> (True, None) fail-open."""
    from app.security.rate_limiter import RateLimitResult, check_rate_limit

    stub = _StubRedis(
        responses=[
            ConnectionError("Redis down"),
            ConnectionError("Redis down"),
        ]
    )
    stub_settings = _make_settings_stub()

    with patch("app.security.rate_limiter.settings", stub_settings):
        result = await check_rate_limit(stub, phone="+573001234567")

    assert result == RateLimitResult(allowed=True, scope=None)


@pytest.mark.asyncio
async def test_disabled_returns_allowed_with_zero_evals() -> None:
    """settings.rate_limit.enabled=False -> (True, None), zero Redis calls."""
    from app.security.rate_limiter import RateLimitResult, check_rate_limit

    stub = _StubRedis()
    stub_settings = _make_settings_stub(enabled=False)

    with patch("app.security.rate_limiter.settings", stub_settings):
        result = await check_rate_limit(
            stub, phone="+573001234567", poliza_id="POL-001"
        )

    assert result == RateLimitResult(allowed=True, scope=None)
    assert len(stub.calls) == 0


@pytest.mark.asyncio
async def test_approaching_threshold_allowed_but_warning(caplog: Any) -> None:
    """Count >= 80% of limit -> allowed=True but rate_limit.approaching logged."""
    import logging
    from app.security.rate_limiter import check_rate_limit

    # phone_limit=10, 80% threshold = 8.  Return 8 (>= 8) for phone eval, 1 for global.
    stub = _StubRedis(responses=[8, 1])
    stub_settings = _make_settings_stub(phone_limit=10)

    with caplog.at_level(logging.WARNING):
        with patch("app.security.rate_limiter.settings", stub_settings):
            result = await check_rate_limit(stub, phone="+573001234567")

    assert result.allowed is True
    # Verify the approaching warning was emitted via structlog -> stdlib bridge
    # (structlog in tests routes through stdlib logging)
    warning_records = [r for r in caplog.records if "approaching" in r.getMessage()]
    assert warning_records, (
        "Expected rate_limit.approaching warning in logs, got none. "
        f"All records: {[r.getMessage() for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_exceeded_logs_warning(caplog: Any) -> None:
    """Phone level returning -1 should emit rate_limit.exceeded warning."""
    import logging
    from app.security.rate_limiter import check_rate_limit

    stub = _StubRedis(responses=[-1])
    stub_settings = _make_settings_stub()

    with caplog.at_level(logging.WARNING):
        with patch("app.security.rate_limiter.settings", stub_settings):
            await check_rate_limit(stub, phone="+573001234567")

    exceeded_records = [r for r in caplog.records if "exceeded" in r.getMessage()]
    assert exceeded_records, (
        "Expected rate_limit.exceeded warning in logs. "
        f"Got: {[r.getMessage() for r in caplog.records]}"
    )


def test_t_rate_limited_is_string() -> None:
    """T_RATE_LIMITED is a non-empty string (client-facing Spanish message)."""
    from app.security.rate_limiter import T_RATE_LIMITED

    assert isinstance(T_RATE_LIMITED, str)
    assert len(T_RATE_LIMITED) > 0


def test_lua_script_uses_sorted_set() -> None:
    """Lua script contains ZREMRANGEBYSCORE (sliding window, not fixed window)."""
    from app.security.rate_limiter import _SLIDING_WINDOW_LUA

    assert "ZREMRANGEBYSCORE" in _SLIDING_WINDOW_LUA
    assert "ZCARD" in _SLIDING_WINDOW_LUA
    assert "ZADD" in _SLIDING_WINDOW_LUA


def test_exports() -> None:
    """Module exports exactly the contract symbols."""
    import app.security.rate_limiter as mod

    assert hasattr(mod, "check_rate_limit")
    assert hasattr(mod, "RateLimitResult")
    assert hasattr(mod, "T_RATE_LIMITED")
    assert hasattr(mod, "_SLIDING_WINDOW_LUA")
    for name in ["check_rate_limit", "RateLimitResult", "T_RATE_LIMITED"]:
        assert name in mod.__all__, f"{name!r} missing from __all__"


# ---------------------------------------------------------------------------
# Integration test (requires real Redis, skipped locally)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("REDIS_URL"),
    reason="REDIS_URL not set — skipping real-Redis integration test",
)
@pytest.mark.asyncio
async def test_integration_phone_limit_blocks() -> None:
    """Real Redis: loop phone_limit+1 times with same phone -> last call blocked.

    Encodes ROADMAP criterion "100 mensajes en 1 min desde mismo número -> bloquea".
    Uses a unique UUID phone to avoid collisions between test runs.
    """
    import uuid
    import redis.asyncio as aioredis
    from app.security.rate_limiter import RateLimitResult, check_rate_limit

    phone = f"+1{uuid.uuid4().hex[:10]}"  # unique per run, E.164-ish
    client = aioredis.from_url(os.environ["REDIS_URL"])

    stub_settings = _make_settings_stub(phone_limit=5, window_s=60)

    try:
        with patch("app.security.rate_limiter.settings", stub_settings):
            for i in range(5):
                result = await check_rate_limit(client, phone=phone)
                assert result.allowed is True, (
                    f"Call {i+1}/5 should be allowed, got blocked"
                )
            # 6th call must be blocked (exceeded phone_limit=5)
            result = await check_rate_limit(client, phone=phone)
            assert result == RateLimitResult(allowed=False, scope="phone"), (
                f"6th call should be blocked by phone scope, got: {result}"
            )
    finally:
        await client.aclose()
