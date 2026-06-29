"""Async compensation wrapper for ``pybreaker`` (skeleton — implemented in Plan 02-03).

``pybreaker 1.4.1`` does NOT support asyncio natively. Using its ``@breaker``
decorator over an ``async def`` silently mis-records success because the
decorator sees a coroutine object (always "truthy"), never the awaited
result (RESEARCH Pitfall 2).

Plan 02-03 implements ``async_call(breaker, coro_fn, *args, **kwargs)`` that:

1. Checks ``breaker.current_state == 'open'`` → raises ``CircuitBreakerError``.
2. ``await coro_fn(*args, **kwargs)``.
3. On success: ``breaker.state.on_success()``; on failure:
   ``breaker.state.on_failure(exc)`` + re-raise.

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
    """Run ``coro_fn(*args, **kwargs)`` under ``breaker`` (implemented in Plan 02-03)."""
    raise NotImplementedError("Implemented in Plan 02-03")


__all__ = ["async_call"]
