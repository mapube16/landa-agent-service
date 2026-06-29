"""SoftSeguros REST client (implemented in Plan 02-03).

Async httpx client to ``https://app.softseguros.com/`` with token auth
(``POST /api-token-auth/`` once, cache in memory + refresh on 401),
``tenacity`` retry (3x expo on ``httpx.HTTPError``/``TimeoutException``),
and ``pybreaker`` circuit breaker (5 failures → 30s open). Singleton
via ``@lru_cache(maxsize=1)``.

READ-ONLY INVARIANT (operator directive, F2)
============================================
Este cliente es READ-ONLY contra SoftSeguros por diseño. NO existe método
write (POST/PUT/PATCH/DELETE) sobre datos del cliente. El único POST permitido
es POST /api-token-auth/ (auth bootstrap), que NO modifica datos del cliente
y vive en una function top-level (no method on SoftSegurosClient).

Agregar un método write requiere:
1. ADR documentado en ``.planning/adr/``
2. Threat model actualizado en PROJECT.md §"Seguridad"
3. Scope update aprobado por el operador
4. Operator approval explícito

El CI guard ``tests/test_softseguros_readonly.py`` (Plan 02-03) introspecciona
esta clase y falla el build si aparecen verbos prohibidos (post, put, patch,
delete, create, update, set_, modify_) en method names.

**NEVER instantiate httpx clients to SoftSeguros elsewhere** — always go
through :func:`get_softseguros_client` so cache + circuit breaker + token
refresh stay coherent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from functools import lru_cache
from typing import Any

import httpx
import pybreaker
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.settings import settings
from app.models.softseguros import ClienteRaw, PolizaRaw

log = structlog.get_logger("integrations.softseguros")


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
# Circuit breaker — per-process state (Pitfall 6 — acceptable for v1: 1
# worker on Railway, ~50 msg/day). 5 consecutive failures → open for 30s.
softseguros_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=30,
    name="softseguros",
)

# Token holder + lock (RESEARCH Pattern 5 — token cached in process memory,
# refreshed on 401, protected by asyncio.Lock to avoid thundering herd).
# Mutable dict so monkeypatch + double-check work without rebinding the
# module attribute mid-flight.
_token_holder: dict[str, str | None] = {"v": None}
_token_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Auth helpers — top-level functions, NOT methods on SoftSegurosClient.
# They POST to /api-token-auth/ (auth bootstrap, NOT a data write). They are
# explicitly out-of-scope for the READ-ONLY CI guard, which introspects
# SoftSegurosClient methods only.
# ---------------------------------------------------------------------------


async def _get_token(http: httpx.AsyncClient) -> str:
    """Return a cached DRF auth token; fetch if missing (Pattern 5).

    Double-check inside the lock so concurrent callers don't stampede the
    upstream ``/api-token-auth/`` endpoint (Pitfall 5 — thundering herd).
    """
    cached = _token_holder["v"]
    if cached:
        return cached
    async with _token_lock:
        cached = _token_holder["v"]
        if cached:
            return cached
        r = await http.post(
            "/api-token-auth/",
            json={
                "username": settings.softseguros.username.get_secret_value(),
                "password": settings.softseguros.password.get_secret_value(),
            },
        )
        r.raise_for_status()
        token: str = r.json()["token"]
        _token_holder["v"] = token
        log.info(
            "softseguros.token.fetched",
            token_hash=hashlib.sha256(token.encode()).hexdigest()[:8],
        )
        return token


async def _refresh_token_on_401(http: httpx.AsyncClient) -> str:
    """Invalidate the cached token and fetch a new one (after a 401)."""
    async with _token_lock:
        _token_holder["v"] = None
    return await _get_token(http)


# ---------------------------------------------------------------------------
# Client class — READ-ONLY (operator directive)
# ---------------------------------------------------------------------------


class SoftSegurosClient:
    """READ-ONLY HTTP client for SoftSeguros (DRF backend).

    See module docstring for the READ-ONLY INVARIANT. CI guard
    ``tests/test_softseguros_readonly.py`` introspects this class and
    fails the build if write verbs appear in method names.
    """

    def __init__(self, http: httpx.AsyncClient, redis: Any | None = None) -> None:
        """Construct the client.

        ``redis`` is optional in tests (cache bypasses cleanly when None).
        In production, the factory leaves ``redis=None`` and ``app/main.py``
        lifespan late-binds it from ``app.state.redis`` so the cache layer
        is wired right after Redis is up.
        """
        self._http = http
        self._redis = redis

    # ------------------------------------------------------------------
    # Internal HTTP primitive — the ONLY one. Adding _post / _put / etc.
    # is prohibited; CI guard test fails the build if it happens.
    # ------------------------------------------------------------------

    @retry(
        # Tenacity OUTER / pybreaker INNER — INVARIANT (RESEARCH Pattern 2 +
        # Pitfall 2). ``pybreaker.CircuitBreakerError`` is NOT in this list,
        # so an open breaker bubbles past tenacity instantly (fail-fast).
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        """Authenticated GET with retry (outer) + breaker (inner).

        Order is INVARIANT:
          - tenacity ``@retry`` wraps the method (handles transient httpx errors).
          - ``async_call(softseguros_breaker, ...)`` is the inner call — when
            the breaker is OPEN it raises ``CircuitBreakerError``, which is
            NOT in tenacity's ``retry_if`` list, so tenacity stops retrying
            instantly.
          - On 401 → refresh token + retry ONCE (auth issue is not transient,
            tenacity should not loop on it).
        """
        # Import here to avoid a circular import if _circuit ever grows a
        # softseguros-specific helper. Local import is essentially free.
        from app.integrations._circuit import async_call

        async def _do() -> dict[str, Any]:
            token = await _get_token(self._http)
            # DRF TokenAuthentication shape: ``Token <hex>`` (NOT Bearer).
            headers = {"Authorization": f"Token {token}"}
            r = await self._http.get(path, params=params, headers=headers)
            if r.status_code == 401:
                # Refresh once + retry the request manually. We do NOT let
                # tenacity loop on auth — that's not a transient httpx error.
                new_token = await _refresh_token_on_401(self._http)
                headers = {"Authorization": f"Token {new_token}"}
                r = await self._http.get(path, params=params, headers=headers)
            r.raise_for_status()
            result: dict[str, Any] = r.json()
            return result

        return await async_call(softseguros_breaker, _do)

    # ------------------------------------------------------------------
    # Cache layer — Redis read-through (D-11). Key:
    # ``softseguros:{poliza_id}:{query_type}``. TTL 60s. Redis-down
    # bypasses cleanly: cache outage must NEVER break the bot.
    # ------------------------------------------------------------------

    async def _cached_get(
        self, cache_id: str, query_type: str, path: str, **params: Any
    ) -> PolizaRaw:
        """Read-through cache wrapper around :meth:`_get`.

        ``cache_id`` is the identifier used in the cache key (usually a
        poliza_id or cliente_id). Named generically to avoid colliding with
        callers that pass ``poliza_id`` as an HTTP query param via
        ``**params`` (e.g. :meth:`get_pagos`).
        """
        cache_key = f"softseguros:{cache_id}:{query_type}".encode()
        cached: bytes | None = None
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
            except Exception as exc:  # noqa: BLE001 — bypass-on-cache-down is intentional
                log.warning(
                    "softseguros.cache.read_error",
                    error_type=type(exc).__name__,
                )
        if cached is not None:
            decoded: dict[str, Any] = json.loads(cached)
            return decoded

        data = await self._get(path, **params)

        if self._redis is not None:
            try:
                await self._redis.set(cache_key, json.dumps(data).encode(), ex=60)
            except Exception as exc:  # noqa: BLE001 — bypass-on-cache-down is intentional
                log.warning(
                    "softseguros.cache.write_error",
                    error_type=type(exc).__name__,
                )
        return data

    # ------------------------------------------------------------------
    # Public READ methods (D-13). NO write counterparts — see CI guard.
    # ------------------------------------------------------------------

    async def get_poliza(self, poliza_id: str) -> PolizaRaw:
        """GET ``/api/poliza/{poliza_id}/``."""
        return await self._cached_get(poliza_id, "poliza", f"/api/poliza/{poliza_id}/")

    async def get_cliente(self, cliente_id: str) -> PolizaRaw:
        """GET ``/api/cliente/{cliente_id}/``.

        Caching is keyed by ``cliente_id`` under the same ``softseguros:*``
        namespace (collision-free because cliente_id ≠ poliza_id in practice).
        """
        return await self._cached_get(cliente_id, "cliente", f"/api/cliente/{cliente_id}/")

    async def get_estado(self, poliza_id: str) -> PolizaRaw:
        """DEPRECATED: ``/api/estadopoliza/{id}/`` returns 404 in SoftSeguros.

        SOFTSEGUROS_API_NOTES.md confirms the detail endpoint does NOT exist.
        Read the embedded ``estado_poliza_nombre`` / ``estado_poliza_codigo``
        from :meth:`get_poliza` instead. Kept as a thin alias for backwards
        compatibility — callers should migrate to ``get_poliza`` directly.
        """
        return await self.get_poliza(poliza_id)

    async def get_pagos(self, poliza_id: str) -> PolizaRaw:
        """GET ``/api/pagopoliza/?poliza_id={poliza_id}``.

        NOTE: per SOFTSEGUROS_API_NOTES.md, this endpoint times out at 504
        for some pólizas. Prefer the embedded ``poliza.total_pagos_poliza``
        field when available; this method is kept for completeness +
        diagnostic use.
        """
        return await self._cached_get(poliza_id, "pagos", "/api/pagopoliza/", poliza_id=poliza_id)

    async def get_clientes_by_documento(self, numero_documento: str) -> ClienteRaw:
        """GET ``/api/cliente/listar_cliente_por_documento/?numero_documento={doc}``.

        Returns a single ``ClienteRaw`` dict (NOT paginated) — confirmed shape
        from 03-00-PROBE.md Probe 2 (122 fields, ``id`` is the cliente PK for
        the secondary poliza call in ``get_polizas_by_cliente``).

        READ-ONLY INVARIANT: see module docstring.
        CI guard: name added to METHOD_ALLOWLIST in same commit
        (tests/test_softseguros_readonly.py).
        """
        # ponytail: cache_id uses "doc:{numero_documento}" prefix to avoid
        # collision with existing cliente/{id} keys in the same namespace.
        result: ClienteRaw = await self._cached_get(  # type: ignore[assignment]
            f"doc:{numero_documento}",
            "cliente",
            "/api/cliente/listar_cliente_por_documento/",
            numero_documento=numero_documento,
        )
        return result

    async def get_polizas_by_cliente(self, cliente_id: int) -> list[PolizaRaw]:
        """GET ``/api/poliza/?cliente={cliente_id}&limit=20``.

        Returns the paginated DRF response's ``results`` list — first 20 pólizas
        owned by the given cliente. Two-call pattern required by 03-00-PROBE.md
        (single-call fallback via ``cliente_numero_documento`` does NOT filter
        server-side and returns the full 52 898-poliza universe).

        READ-ONLY INVARIANT: see module docstring.
        CI guard: name added to METHOD_ALLOWLIST in same commit
        (tests/test_softseguros_readonly.py).
        """
        raw: dict[str, Any] = await self._cached_get(
            str(cliente_id),
            "polizas_by_cliente",
            "/api/poliza/",
            cliente=cliente_id,
            limit=20,
        )
        results: list[PolizaRaw] = raw.get("results", [])
        return results


# ---------------------------------------------------------------------------
# Factory (module-level singleton via lru_cache).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_softseguros_client() -> SoftSegurosClient:
    """Return the cached :class:`SoftSegurosClient` singleton.

    Redis is injected lazily on first access via ``app.state`` — see lifespan
    wiring in ``app/main.py`` (Plan 02-03 Task 2). Tests bypass this factory
    and construct :class:`SoftSegurosClient` directly with stubbed http +
    redis.

    NEVER instantiate httpx clients to SoftSeguros elsewhere — always go
    through this factory so cache + circuit breaker + token refresh stay
    coherent.
    """
    limits = httpx.Limits(
        max_keepalive_connections=10,
        max_connections=20,
        keepalive_expiry=30.0,
    )
    timeout = httpx.Timeout(10.0, connect=3.0, read=10.0, write=3.0, pool=2.0)
    http = httpx.AsyncClient(
        base_url=settings.softseguros.base_url,
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": "landa-agent-service/0.1.0"},
    )
    return SoftSegurosClient(http=http, redis=None)  # redis bound in lifespan


__all__ = ["SoftSegurosClient", "get_softseguros_client", "softseguros_breaker"]
