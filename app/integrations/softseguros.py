"""SoftSeguros REST client skeleton — implemented in Plan 02-03.

Async httpx client to ``https://app.softseguros.com/`` with token auth
(``POST /api-token-auth/`` once, cache in memory + refresh on 401),
``tenacity`` retry (3x expo on ``httpx.HTTPError``/``TimeoutException``),
and ``pybreaker`` circuit breaker (5 failures → 30s open). Singleton
via ``@lru_cache(maxsize=1)``.

READ-ONLY INVARIANT (operator directive, F2)
============================================
Este cliente es READ-ONLY contra SoftSeguros por diseño. NO existe método
write (POST/PUT/PATCH/DELETE) sobre datos del cliente. El único POST permitido
es POST /api-token-auth/ (auth bootstrap), que NO modifica datos del cliente.

Agregar un método write requiere:
1. ADR documentado en ``.planning/adr/``
2. Threat model actualizado en PROJECT.md §"Seguridad"
3. Scope update aprobado por el operador

El CI guard ``tests/test_softseguros_readonly.py`` (Plan 02-03) introspecciona
esta clase y falla el build si aparecen verbos prohibidos (post, put, patch,
delete, create, update, set_, modify_) en method names.

**NEVER instantiate httpx clients to SoftSeguros elsewhere** — always go
through :func:`get_softseguros_client` so cache + circuit breaker + token
refresh stay coherent.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
import structlog

from app.config.settings import settings
from app.models.softseguros import PolizaRaw

log = structlog.get_logger("integrations.softseguros")


class SoftSegurosClient:
    """READ-ONLY async client for SoftSeguros REST. Implemented in Plan 02-03."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    # ---- private HTTP primitives -------------------------------------------------
    # ONLY ``_get`` is declared. _post/_put/_patch/_delete are deliberately
    # absent (READ-ONLY invariant — see module docstring).

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        """Issue an authenticated GET. Implemented in Plan 02-03."""
        raise NotImplementedError("Implemented in Plan 02-03")

    async def _get_token(self) -> str:
        """Return cached auth token, fetching via /api-token-auth/ once.

        Gated by ``asyncio.Lock`` with double-check. Implemented in Plan 02-03.
        """
        raise NotImplementedError("Implemented in Plan 02-03")

    async def _refresh_token_on_401(self) -> str:
        """Invalidate cached token then delegate to ``_get_token``. Implemented in Plan 02-03."""
        raise NotImplementedError("Implemented in Plan 02-03")

    # ---- public read methods (D-13 endpoints) -----------------------------------

    async def get_poliza(self, poliza_id: str) -> PolizaRaw:
        """GET /api/poliza/{poliza_id}/ — implemented in Plan 02-03."""
        raise NotImplementedError("Implemented in Plan 02-03")

    async def get_cliente(self, cliente_id: str) -> PolizaRaw:
        """GET /api/cliente/{cliente_id}/ — implemented in Plan 02-03."""
        raise NotImplementedError("Implemented in Plan 02-03")

    async def get_estado(self, poliza_id: str) -> PolizaRaw:
        """GET /api/estadopoliza/{poliza_id}/ — implemented in Plan 02-03."""
        raise NotImplementedError("Implemented in Plan 02-03")

    async def get_pagos(self, poliza_id: str) -> PolizaRaw:
        """GET /api/pagopoliza/?poliza_id={poliza_id} — implemented in Plan 02-03."""
        raise NotImplementedError("Implemented in Plan 02-03")


@lru_cache(maxsize=1)
def get_softseguros_client() -> SoftSegurosClient:
    """Return the cached :class:`SoftSegurosClient` singleton. Implemented in Plan 02-03."""
    # Reference settings so mypy --strict sees the dependency wire.
    _ = settings.softseguros.base_url
    raise NotImplementedError("Implemented in Plan 02-03")


__all__ = ["SoftSegurosClient", "get_softseguros_client"]
