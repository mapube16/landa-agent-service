"""L4 debtor flags — summarised cross-case history injected into the system prompt.

Case-history flags (``intentos``/``escalado_previo``/``ultima_interaccion_wa``)
come entirely from this repo's own ``cases`` table (CLAUDE.md L4). There is no
VOICE→WA read endpoint in the frozen contract (Contrato B is WA→VOICE only),
so voice-side flags (``ultima_llamada_fecha``, ``promesa_de_pago``) are NOT
fabricated here.

When ``poliza_id`` is given, cartera/mora status is enriched from SoftSeguros
(``get_cartera_status`` — already Capa-4-whitelisted by ``CarteraStatus``).
That enrichment fails open independently of the case-history lookup: a
SoftSeguros outage must not wipe out flags that already succeeded.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog

from app.memory.case_store import Case

log = structlog.get_logger("memory.debtor_flags")

__all__ = ["get_debtor_flags"]


async def get_debtor_flags(
    session: Any, phone: str, poliza_id: str | None = None
) -> dict[str, Any]:
    """Return summarised L4 flags for ``phone``, or ``{}`` if no case history.

    Fields: ``intentos`` (case count), ``escalado_previo`` (bool),
    ``ultima_interaccion_wa`` (ISO timestamp), plus — when ``poliza_id`` is
    given and SoftSeguros has cartera pendiente — ``dias_mora``,
    ``fecha_compromiso``, ``saldo_pendiente``, ``riesgo``.
    """
    result = await session.execute(
        sa.select(Case.status, Case.updated_at).where(Case.phone == phone)
    )
    rows = result.all()
    flags: dict[str, Any] = (
        {}
        if not rows
        else {
            "intentos": len(rows),
            "escalado_previo": any(status == "escalated" for status, _ in rows),
            "ultima_interaccion_wa": max(updated_at for _, updated_at in rows).isoformat(),
        }
    )

    if poliza_id:
        try:
            from app.integrations.softseguros import get_softseguros_client

            cartera = await get_softseguros_client().get_cartera_status(poliza_id)
        except Exception:  # noqa: BLE001 — SoftSeguros outage must not break L4 flags
            log.warning("debtor_flags.cartera_status.lookup_failed", exc_info=True)
        else:
            if cartera is not None:
                flags["dias_mora"] = cartera.edad_cartera
                flags["fecha_compromiso"] = cartera.fecha_realizara_pago
                flags["saldo_pendiente"] = cartera.saldo_pendiente
                flags["riesgo"] = cartera.riesgo

    return flags
