"""L4 debtor flags — summarised cross-case history injected into the system prompt.

Derived entirely from this repo's own ``cases`` table (CLAUDE.md L4). There is
no VOICE→WA read endpoint in the frozen contract (Contrato B is WA→VOICE
only), so flags about voice-side history (``ultima_llamada_fecha``,
``promesa_de_pago``) are NOT fabricated here — only what WA itself has
observed for this phone across past cases.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from app.memory.case_store import Case

__all__ = ["get_debtor_flags"]


async def get_debtor_flags(session: Any, phone: str) -> dict[str, Any]:
    """Return summarised L4 flags for ``phone``, or ``{}`` if no case history.

    Fields: ``intentos`` (case count), ``escalado_previo`` (bool), and
    ``ultima_interaccion_wa`` (ISO timestamp of the most recent case update).
    """
    result = await session.execute(
        sa.select(Case.status, Case.updated_at).where(Case.phone == phone)
    )
    rows = result.all()
    if not rows:
        return {}

    return {
        "intentos": len(rows),
        "escalado_previo": any(status == "escalated" for status, _ in rows),
        "ultima_interaccion_wa": max(updated_at for _, updated_at in rows).isoformat(),
    }
