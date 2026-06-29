"""Async compensation wrapper for ``pybreaker`` (implemented in Plan 02-03).

``pybreaker 1.4.1`` does NOT support asyncio natively. Using its ``@breaker``
decorator over an ``async def`` silently mis-records success because the
decorator sees a coroutine object (always "truthy"), never the awaited
result (RESEARCH Pitfall 2).

:func:`async_call` is the compensator:

1. Checks ``breaker.current_state == 'open'`` → raises ``CircuitBreakerError``
   BEFORE awaiting the coroutine (fail-fast).
2. ``await coro_fn(*args, **kwargs)``.
3. On success: ``breaker.state.on_success()`` (under ``breaker._lock`` so
   counters stay consistent).
4. On failure: ``breaker.state.on_failure(exc)`` + re-raise so the caller
   (tenacity, etc.) can decide whether to retry.

``breaker.state.on_success()`` / ``on_failure(exc)`` are pseudo-public hooks
that pybreaker's own ``call()`` invokes internally; stable across pybreaker
1.x but not contractually guaranteed. If a future pybreaker minor breaks
these, migrate to ``purgatory`` (native async circuit breaker).

**Never** use ``@breaker`` as a decorator on async functions in this codebase.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pybreaker


async def async_call[T](
    breaker: pybreaker.CircuitBreaker,
    coro_fn: Callable[..., Awaitable[T]],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Run ``coro_fn(*args, **kwargs)`` under ``breaker``.

    See module docstring for protocol details (open-state fail-fast →
    await → on_success / on_failure under ``breaker._lock``).
    """
    if breaker.current_state == "open":
        raise pybreaker.CircuitBreakerError(f"Circuit '{breaker.name}' is open")
    try:
        result = await coro_fn(*args, **kwargs)
    except Exception as exc:
        with breaker._lock:
            breaker.state.on_failure(exc)
        raise
    else:
        with breaker._lock:
            breaker.state.on_success()
        return result


__all__ = ["async_call"]
