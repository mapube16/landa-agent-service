"""Redis async client + ConnectionPool factory.

Per RESEARCH.md Standard Stack + Pattern 1:
- Single Redis pool serves ARQ queue, SoftSeguros cache (TTL 60s),
  rate-limit tokens, and idempotency keys.
- ``decode_responses=False`` is the binary-safe default. Tools that expect
  ``str`` decode explicitly per call-site; future binary payloads (images,
  PDFs in F4) are not corrupted by accidental UTF-8 coercion.
- The pool is returned alongside the client so the lifespan can call
  ``disconnect(inuse_connections=True)`` on shutdown — redis-py's documented
  pattern for graceful pool teardown.

NEVER import this module from request handlers — the client is created once in
``app.main:lifespan`` (plan 01-04) and exposed via ``request.app.state``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from redis.asyncio import ConnectionPool, Redis

from app.config.settings import settings

# Type aliases for the binary-safe configuration we use everywhere.
# redis-stubs declares ``Redis`` and ``ConnectionPool`` as generic classes,
# but at RUNTIME the real ``redis.asyncio`` classes are NOT ``typing.Generic``
# subclasses — evaluating ``Redis[bytes]`` at module import would raise
# ``TypeError: <class 'redis.asyncio.client.Redis'> is not a generic class``.
# We therefore expose the parametrised aliases under TYPE_CHECKING only and
# fall back to plain class references at runtime. ``from __future__ import
# annotations`` (above) keeps function annotations lazy so they evaluate as
# strings — never as runtime ``Redis[bytes]`` lookups.
if TYPE_CHECKING:
    RedisClient = Redis[bytes]
    RedisPool = ConnectionPool[Any]
else:
    RedisClient = Redis
    RedisPool = ConnectionPool


def create_redis_pool() -> tuple[RedisClient, RedisPool]:
    """Build the application Redis client and its connection pool.

    Returns the tuple ``(client, pool)``. ``max_connections=20`` is a deliberate
    cap so a runaway feature can't exhaust Redis's default 10k connection limit
    on Railway plans — and sized big enough that ARQ + cache + rate-limit don't
    starve each other under v1 traffic.
    """
    # socket_timeout/socket_connect_timeout: redis-py defaults both to None
    # (no bound). This pool backs the rate limiter itself (app/security/
    # rate_limiter.py) plus SoftSeguros/Chatwoot caches — an unbounded hung
    # socket here doesn't raise, so it never reaches those call sites' own
    # fail-open exception handlers; it just pins the request.
    pool: RedisPool = ConnectionPool.from_url(
        settings.redis.url.get_secret_value(),
        max_connections=20,
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=10,
    )
    client: RedisClient = Redis(connection_pool=pool)
    return client, pool


async def close_redis_pool(client: RedisClient, pool: RedisPool) -> None:
    """Gracefully close the Redis client + drain the pool.

    Call from the lifespan shutdown phase. ``inuse_connections=True`` tears down
    even connections currently held by callers — important when the server is
    shutting down anyway and we'd rather not hang waiting for in-flight ops.

    NOTE: redis-py 5+ renamed ``close`` to ``aclose`` (the old name still works
    and remains the only one declared in ``redis-stubs``). We call ``close``
    here so mypy --strict passes without an ``aclose`` attr-defined ignore;
    behaviour is identical.
    """
    await client.close(close_connection_pool=False)
    await pool.disconnect(inuse_connections=True)


__all__ = [
    "close_redis_pool",
    "create_redis_pool",
]
