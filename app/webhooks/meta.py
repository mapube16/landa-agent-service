"""Meta Cloud API webhook receiver skeleton — implemented in Plan 02-02.

Endpoints (D-09):

- ``GET /webhooks/meta``  — Meta subscription challenge. Compares
  ``hub.verify_token`` query param against ``settings.whatsapp.verify_token``;
  returns ``hub.challenge`` on match, 403 otherwise.
- ``POST /webhooks/meta`` — Inbound message events. Order is INVARIANT
  (enforced by D-15): raw body → HMAC verification → JSON parse →
  idempotency dedup (Redis) → allowlist check → echo dispatch.

**NEVER read ``request.json()`` before ``await request.body()`** — the body
can only be consumed once and HMAC must be computed over the raw bytes
(RESEARCH Pitfall 1).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response

from app.config.settings import settings

router = APIRouter(prefix="/webhooks", tags=["meta"])
log = structlog.get_logger("webhooks.meta")


@router.get("/meta")
async def verify(request: Request) -> Response:
    """GET subscription challenge (D-09, D-17). Implemented in Plan 02-02."""
    # Reference settings so mypy --strict sees the dependency wire.
    _ = settings.whatsapp.verify_token
    raise NotImplementedError("Implemented in Plan 02-02")


@router.post("/meta")
async def receive(request: Request) -> Response:
    """POST inbound events: raw body → HMAC → parse → dedup → allowlist → echo.

    Implemented in Plan 02-02.
    """
    _ = settings.whatsapp.webhook_secret
    raise NotImplementedError("Implemented in Plan 02-02")


__all__ = ["router"]
