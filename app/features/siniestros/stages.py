"""Lectura de la etapa de un siniestro a partir de lo que escribe el cliente.

El cliente cuenta en sus palabras como va su reclamacion ("ya vino el perito",
"me pidieron mas papeles") y de ahi sale la etiqueta que DPG ve en Chatwoot.

Modelo: ``get_llm("judge")`` = Gemini 2.5 Flash, temp=0 — el mismo barato y
deterministico del auditor de calidad.

REGLA CENTRAL — el modelo NUNCA retrocede una etapa. Si lee "perito" y la
conversacion ya iba en "ofrecimiento", gana la que ya estaba. Un error de
lectura no puede borrar progreso real: DPG prioriza con estas etiquetas.

Fail-open: si el modelo falla, la etapa queda como estaba (``None``), no se
inventa una.
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic import BaseModel, Field

from app.integrations.openrouter import get_llm

log = structlog.get_logger("features.siniestros.stages")

PREFIX = "siniestro-"

# Orden del proceso. El indice ES la precedencia: solo se avanza.
STAGES: tuple[str, ...] = (
    "aviso",
    "reclamacion",
    "presupuesto",
    "perito",
    "ofrecimiento",
    "acuerdo",
    "pago",
)
_RANK = {s: i for i, s in enumerate(STAGES)}

# Etiquetas con el prefijo que NO son etapa y el agente jamas debe pisar.
NOT_A_STAGE = frozenset({f"{PREFIX}sismo", f"{PREFIX}trabado"})

BLOQUEOS: tuple[str, ...] = (
    "documentos",
    "aseguradora",
    "perito",
    "monto",
    "ninguno",
    "otro",
)

_SYSTEM = """\
Eres un analista de siniestros de un corredor de seguros (DPG). Lees la
transcripcion de UNA conversacion de WhatsApp con un cliente que sufrio danos
por el terremoto del 10 de agosto de 2026 y determinas EN QUE ETAPA va su
reclamacion. Responde SOLO con el JSON.

Etapas, en orden del proceso:
- aviso: reporto el siniestro a la aseguradora o a DPG, nada mas todavia.
- reclamacion: esta reuniendo o ya envio los documentos formales.
- presupuesto: esta cotizando la reparacion o ya entrego cotizaciones.
- perito: espera la visita del ajustador, o ya lo visito y espera su informe.
- ofrecimiento: la aseguradora ya puso una cifra sobre la mesa.
- acuerdo: esta aceptando, firmando o negociando ese monto.
- pago: ya le consignaron la indemnizacion.

Reglas:
- Reporta la etapa MAS AVANZADA que el cliente mencione haber alcanzado.
- Si el cliente no da senales claras de ninguna etapa, usa confianza "baja".
- No infieras del silencio: que no mencione al perito no significa que no vino.
- bloqueo: que lo tiene detenido AHORA. "documentos" si le faltan papeles;
  "aseguradora" si espera respuesta de la compania; "perito" si espera la
  visita o el informe; "monto" si no esta de acuerdo con la cifra; "ninguno"
  si avanza bien; "otro" para cualquier otra cosa.
- evidencia: la frase TEXTUAL del cliente que sustenta la etapa (max 140
  caracteres). Si no hay frase clara, cadena vacia.
- confianza: "alta" solo si el cliente lo dice explicitamente; "media" si se
  deduce con cierta seguridad; "baja" si estas adivinando.
"""


class StageRead(BaseModel):
    """Lo que el modelo entiende de una conversacion."""

    etapa: (
        Literal["aviso", "reclamacion", "presupuesto", "perito", "ofrecimiento", "acuerdo", "pago"]
        | None
    ) = None
    bloqueo: Literal["documentos", "aseguradora", "perito", "monto", "ninguno", "otro"] = "otro"
    hubo_movimiento: bool = False
    evidencia: str = Field(default="", max_length=200)
    confianza: Literal["alta", "media", "baja"] = "baja"


def current_stage(labels: list[str]) -> str | None:
    """Etapa vigente segun las etiquetas actuales, o ``None``.

    Ignora ``siniestro-sismo`` y ``siniestro-trabado``, que no son etapa. Si
    por error hubiera dos etapas, gana la mas avanzada.
    """
    found = [
        lbl.removeprefix(PREFIX)
        for lbl in labels
        if lbl.startswith(PREFIX) and lbl not in NOT_A_STAGE and lbl.removeprefix(PREFIX) in _RANK
    ]
    return max(found, key=lambda s: _RANK[s]) if found else None


def decide_stage(actual: str | None, leida: StageRead) -> str | None:
    """Etapa a aplicar, o ``None`` para no tocar nada.

    Tres motivos para no tocar: el modelo no leyo etapa, la leyo con confianza
    baja, o la leida es ANTERIOR a la que ya tiene (no se retrocede).
    """
    if leida.etapa is None or leida.confianza == "baja":
        return None
    if actual is not None and _RANK[leida.etapa] <= _RANK[actual]:
        return None
    return leida.etapa


def stage_label(stage: str) -> str:
    """Nombre de etiqueta en Chatwoot para una etapa."""
    return f"{PREFIX}{stage}"


async def read_stage(transcript: str) -> StageRead:
    """Lee la etapa de una transcripcion (fail-open a 'no se pudo leer')."""
    if not transcript.strip():
        return StageRead()
    try:
        llm = get_llm("judge").with_structured_output(StageRead)
        result = await llm.ainvoke(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"Transcripcion:\n{transcript}"},
            ]
        )
        if isinstance(result, StageRead):
            return result
        return StageRead()
    except Exception as exc:  # noqa: BLE001 — leer la etapa nunca rompe el cron
        log.warning("siniestros.read_stage_failed", error_type=type(exc).__name__)
        return StageRead()
