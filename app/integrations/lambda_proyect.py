"""REST client to lambda-proyect (voice agent) — Contrato B (Fase 6).

WA calls VOICE when a case linked to a voice-originated debtor (``Case.debtor_id``
is set) reaches a terminal outcome. See
``.planning/contracts/lambda-handoff-contract.md`` Contrato B.

Auth: ``Authorization: Bearer <WA_TO_VOICE_TOKEN>`` (distinct token from the
one VOICE uses to call us, per the contract's two-token recommendation).

Fail-open by design: VOICE integration is best-effort. If the token isn't
configured yet (ops pending) or the call fails, we log and move on — the
WhatsApp payment flow to the actual client must never block on this.

**NEVER instantiate httpx clients to lambda-proyect elsewhere** -- always go
through :func:`get_lambda_proyect_client` (same pattern as chatwoot.py).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
import structlog

from app.config.settings import settings

log = structlog.get_logger("integrations.lambda_proyect")

__all__ = ["LambdaProyectClient", "get_lambda_proyect_client"]


class LambdaProyectClient:
    """Async client for lambda-proyect's Contrato B endpoints."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def escalate_case(
        self, case_id: str, *, reason: str, channel: str = "whatsapp", note: str | None = None
    ) -> None:
        """POST /cobranza/case/{case_id}/escalate (B1). Logs and swallows on failure."""
        try:
            r = await self._http.post(
                f"/cobranza/case/{case_id}/escalate",
                json={"reason": reason, "channel": channel, "note": note},
            )
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — best-effort, never block the payment flow
            log.warning(
                "lambda_proyect.escalate_case.failed",
                case_id=case_id,
                error_type=type(exc).__name__,
            )
            return
        log.info("lambda_proyect.escalate_case.ok", case_id=case_id, reason=reason)

    async def update_debtor(self, debtor_id: str, **fields: Any) -> None:
        """POST /cobranza/debtor/{debtor_id}/update (B2). Logs and swallows on failure."""
        try:
            r = await self._http.post(f"/cobranza/debtor/{debtor_id}/update", json=fields)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — best-effort, never block the payment flow
            log.warning(
                "lambda_proyect.update_debtor.failed",
                debtor_id=debtor_id,
                error_type=type(exc).__name__,
            )
            return
        log.info("lambda_proyect.update_debtor.ok", debtor_id=debtor_id, fields=list(fields))


@lru_cache(maxsize=1)
def get_lambda_proyect_client() -> LambdaProyectClient:
    """Return the cached :class:`LambdaProyectClient` singleton."""
    token = settings.lambda_proyect.wa_to_voice_token
    headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
    timeout = httpx.Timeout(10.0, connect=3.0, read=10.0, write=3.0, pool=2.0)
    http = httpx.AsyncClient(
        base_url=settings.lambda_proyect.base_url,
        headers=headers,
        timeout=timeout,
    )
    return LambdaProyectClient(http=http)
