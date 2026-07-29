"""LLM-as-auditor: quality anomalies in bot conversations (P3 calidad).

The daily volume metrics count events; this reads the actual message flow of
a conversation and flags QUALITY problems a counter can't see — things we
watched fail live on 2026-07-29:
  - the bot certifying solvency / a payment ("está al día", "no tienes saldo")
    even when the firewall would substitute it — we want to know the LLM TRIED
  - broken policy lists ("1, 1, 1…")
  - dead-end loops (same list re-sent, client stuck)
  - a frustrated / angry client

Model: ``get_llm("judge")`` = Gemini 2.5 Flash, temp=0 — the same cheap,
deterministic model the output judge uses. Only conversations with real client
activity are worth auditing (a template nobody answered has nothing to judge),
so the caller filters first — keeping token cost proportional to real traffic.

Fail-open: an auditor error returns an all-clear rubric (a monitoring feature
must never break the endpoint that serves it).
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel

from app.integrations.openrouter import get_llm

log = structlog.get_logger("features.metrics.quality")

_AUDITOR_SYSTEM = """\
Eres un auditor de CALIDAD de un bot de cobranza de seguros (ARIA, DPG). Lees
la transcripción de UNA conversación entre el bot y un cliente y marcas
problemas de calidad. Responde SOLO con el JSON del rubric.

Flags (true = se detectó el problema):
- intento_certificar_pago: el bot afirmó o insinuó que la póliza está al día,
  sin saldo, pagada, o que un pago fue recibido/aplicado. SOLO cartera puede
  confirmar pagos; el bot NUNCA debe hacerlo. Marca true aunque el bot lo
  haya dicho de forma suave ("no veo saldo pendiente", "parece que está al día").
- lista_rota: una lista de opciones/pólizas salió con títulos inútiles o
  repetidos ("1, 1, 1", opciones vacías, todas iguales).
- loop_sin_salida: el bot repitió el mismo mensaje/lista varias veces sin
  avanzar y el cliente quedó atascado sin poder lograr su objetivo.
- cliente_frustrado: el cliente muestra molestia, frustración, enojo o queja
  (grosería, "no sirve", "llevo rato", "ya les dije", signos de exasperación).
- rationale: 1 frase en español explicando lo detectado (o "sin problemas").
"""


class QualityRubric(BaseModel):
    """Quality-anomaly flags for one conversation (true = problem present)."""

    intento_certificar_pago: bool
    lista_rota: bool
    loop_sin_salida: bool
    cliente_frustrado: bool
    rationale: str


_CLEAN = QualityRubric(
    intento_certificar_pago=False,
    lista_rota=False,
    loop_sin_salida=False,
    cliente_frustrado=False,
    rationale="sin problemas",
)


def render_transcript(messages: list[dict]) -> str:  # type: ignore[type-arg]
    """Flatten Chatwoot messages to a compact 'ROLE: text' transcript."""
    lines: list[str] = []
    for m in messages:
        content = str(m.get("content") or "").strip()
        if not content or m.get("private"):
            continue
        role = "CLIENTE" if m.get("message_type") == 0 else "BOT"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def audit_conversation(messages: list[dict]) -> QualityRubric:  # type: ignore[type-arg]
    """Return the quality rubric for one conversation (fail-open to all-clear)."""
    transcript = render_transcript(messages)
    if not transcript.strip():
        return _CLEAN
    try:
        llm = get_llm("judge").with_structured_output(QualityRubric)
        result = await llm.ainvoke(
            [
                {"role": "system", "content": _AUDITOR_SYSTEM},
                {"role": "user", "content": f"Transcripción:\n{transcript}"},
            ]
        )
        if isinstance(result, QualityRubric):
            return result
        return _CLEAN
    except Exception as exc:  # noqa: BLE001
        log.warning("quality.audit_failed", error_type=type(exc).__name__)
        return _CLEAN


def aggregate_quality(rubrics: list[QualityRubric]) -> dict[str, int]:
    """Count anomaly flags across audited conversations."""
    return {
        "anom_intento_certificar_pago": sum(r.intento_certificar_pago for r in rubrics),
        "anom_lista_rota": sum(r.lista_rota for r in rubrics),
        "anom_loop_sin_salida": sum(r.loop_sin_salida for r in rubrics),
        "anom_cliente_frustrado": sum(r.cliente_frustrado for r in rubrics),
        "conversaciones_auditadas": len(rubrics),
    }


__all__ = ["QualityRubric", "aggregate_quality", "audit_conversation", "render_transcript"]
