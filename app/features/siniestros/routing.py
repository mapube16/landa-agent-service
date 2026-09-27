"""Asignacion de etiqueta segun lo que responde el cliente a una campana.

Dos campanas del terremoto del 10-ago-2026, textos aprobados por DPG (18-sep):

  seguimiento  -> a los 154 con reclamacion abierta. Marca: ``siniestro-sismo``.
                  Estados: sin-radicar | radicada-esperando | en-ajuste |
                  indemnizada | negada-problema | siniestro-sin-respuesta
  deteccion    -> a los 309 con poliza vigente de quienes DPG no sabe nada.
                  Marca: ``deteccion-sismo``.
                  Estados: afectado-sin-reportar | afectado-reportado |
                  sin-afectacion | requiere-orientacion

Ninguno de los dos mensajes lista opciones numeradas, asi que la respuesta
siempre es texto libre y la lee el modelo (``get_llm("judge")``, temp=0). Un
digito suelto NO clasifica: sin menu, "3" no significa nada.

La marca de campana en la conversacion decide que clasificador aplica. Sin
marca, no se toca nada: un hilo de cartera no es de esto.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import structlog
from pydantic import BaseModel

from app.integrations.openrouter import get_llm

log = structlog.get_logger("features.siniestros.routing")

SISMO_LABEL = "siniestro-sismo"
DETECCION_LABEL = "deteccion-sismo"
ESCALADO_LABEL = "escalado-humano"
NO_ANSWER_LABEL = "siniestro-sin-respuesta"


class SeguimientoRead(BaseModel):
    opcion: Literal[
        "sin_radicar",
        "radicada_esperando",
        "en_ajuste",
        "indemnizada",
        "negada_problema",
        "no_aplica",
    ] = "no_aplica"
    menciona_cobertura: bool = False
    confianza: Literal["alta", "media", "baja"] = "baja"


class DeteccionRead(BaseModel):
    opcion: Literal[
        "afectado_sin_reportar",
        "afectado_reportado",
        "sin_afectacion",
        "requiere_orientacion",
        "no_aplica",
    ] = "no_aplica"
    confianza: Literal["alta", "media", "baja"] = "baja"


@dataclass(frozen=True)
class Campaign:
    marker: str
    states: tuple[str, ...]
    to_label: dict[str, str]
    needs_agent: frozenset[str]
    model: type[BaseModel]
    system: str


_SEGUIMIENTO_SYSTEM = """\
Eres un analista de siniestros de DPG Seguros. Al cliente, que tiene una
reclamacion abierta por el terremoto del 10 de agosto de 2026, se le pregunto
en que estado esta su proceso. Clasifica su respuesta. Responde SOLO con el JSON.

- sin_radicar: aun no ha radicado / presentado la reclamacion formal.
- radicada_esperando: ya la radico y espera respuesta de la aseguradora.
- en_ajuste: le pidieron documentos, vino el perito, o ya le hicieron oferta.
- indemnizada: ya le pagaron o le consignaron.
- negada_problema: se la negaron, o tiene un problema, queja o molestia.
- no_aplica: no habla de su reclamacion (saluda, pregunta otra cosa, audio o
  foto sin texto).

Si menciona una cobertura adicional (arriendo, gastos, escombros), eso NO
cambia el estado: clasifica por donde va la reclamacion y marca
menciona_cobertura. confianza "alta" solo si es inequivoco.
"""

_DETECCION_SYSTEM = """\
Eres un analista de siniestros de DPG Seguros. Al cliente, que tiene poliza
vigente, se le pregunto si tuvo danos por el terremoto del 10 de agosto de 2026.
DPG no sabe nada de su caso. Clasifica su respuesta. Responde SOLO con el JSON.

- afectado_sin_reportar: dice que tuvo danos (aunque sean pequenos) y NO ha
  reportado ni reclamado.
- afectado_reportado: tuvo danos y ya reporto o ya tiene reclamacion.
- sin_afectacion: dice que no tuvo danos.
- requiere_orientacion: no esta seguro, pregunta que cubre, pide que lo llamen
  o lo orienten.
- no_aplica: no responde a la pregunta (saluda, otro tema, audio o foto sin
  texto).

Ante la duda entre sin_afectacion y requiere_orientacion, elige
requiere_orientacion: perder un afectado es peor que una llamada de mas.
confianza "alta" solo si es inequivoco.
"""

SEGUIMIENTO = Campaign(
    marker=SISMO_LABEL,
    states=(
        "sin-radicar",
        "radicada-esperando",
        "en-ajuste",
        "indemnizada",
        "negada-problema",
        NO_ANSWER_LABEL,
    ),
    to_label={
        "sin_radicar": "sin-radicar",
        "radicada_esperando": "radicada-esperando",
        "en_ajuste": "en-ajuste",
        "indemnizada": "indemnizada",
        "negada_problema": "negada-problema",
    },
    needs_agent=frozenset({"negada-problema"}),
    model=SeguimientoRead,
    system=_SEGUIMIENTO_SYSTEM,
)

DETECCION = Campaign(
    marker=DETECCION_LABEL,
    states=(
        "afectado-sin-reportar",
        "afectado-reportado",
        "sin-afectacion",
        "requiere-orientacion",
    ),
    to_label={
        "afectado_sin_reportar": "afectado-sin-reportar",
        "afectado_reportado": "afectado-reportado",
        "sin_afectacion": "sin-afectacion",
        "requiere_orientacion": "requiere-orientacion",
    },
    needs_agent=frozenset({"afectado-sin-reportar", "requiere-orientacion"}),
    model=DeteccionRead,
    system=_DETECCION_SYSTEM,
)

# Si un hilo tuviera ambas marcas, manda seguimiento: ya hay reclamacion.
CAMPAIGNS: tuple[Campaign, ...] = (SEGUIMIENTO, DETECCION)


def campaign_for(labels: list[str]) -> Campaign | None:
    return next((c for c in CAMPAIGNS if c.marker in labels), None)


async def classify(text: str, campaign: Campaign) -> str | None:
    """Etiqueta de estado para ``text``, o ``None`` si no aplica, duda o falla."""
    if not (text or "").strip():
        return None
    try:
        llm = get_llm("judge").with_structured_output(campaign.model)
        read = await llm.ainvoke(
            [
                {"role": "system", "content": campaign.system},
                {"role": "user", "content": text[:2000]},
            ]
        )
    except Exception as exc:  # noqa: BLE001 — clasificar nunca rompe el webhook
        log.warning("siniestros.routing.failed", error_type=type(exc).__name__)
        return None
    if not isinstance(read, campaign.model):
        return None
    opcion = getattr(read, "opcion", "no_aplica")
    if opcion == "no_aplica" or getattr(read, "confianza", "baja") == "baja":
        return None
    return campaign.to_label.get(opcion)


def next_labels(current: list[str], campaign: Campaign, label: str) -> list[str]:
    """Conjunto final de etiquetas tras aplicar ``label``.

    Quita el estado anterior de la campana (son excluyentes), conserva todo lo
    demas, agrega escalado si hace falta. Quien en deteccion dice que YA
    reporto pasa ademas a seguimiento: tiene reclamacion, hay que vigilarla.
    """
    keep = [x for x in current if x not in campaign.states]
    out = [*keep, label]
    if label in campaign.needs_agent and ESCALADO_LABEL not in out:
        out.append(ESCALADO_LABEL)
    if label == "afectado-reportado" and SISMO_LABEL not in out:
        out.append(SISMO_LABEL)
    return list(dict.fromkeys(out))


async def apply_status(phone: str, text: str) -> str | None:
    """Etiqueta la conversacion segun la respuesta. Fail-open, nunca lanza.

    Devuelve la etiqueta aplicada o ``None``. Etiquetar es un extra sobre el
    flujo del webhook: no puede retrasar ni tumbar la respuesta al cliente.
    """
    from app.integrations.chatwoot import get_chatwoot_client

    try:
        client = get_chatwoot_client()
        conv_id = await client.get_or_create_conversation(phone)
        current = await client.get_labels(conv_id)
        campaign = campaign_for(current)
        if campaign is None:
            return None  # no es un hilo de estas campanas

        label = await classify(text, campaign)
        if label is None:
            return None

        await client.replace_labels(conv_id, next_labels(current, campaign, label))
        log.info("siniestros.status.applied", campaign=campaign.marker, label=label)
        return label
    except Exception as exc:  # noqa: BLE001
        log.warning("siniestros.status.failed", error_type=type(exc).__name__)
        return None
