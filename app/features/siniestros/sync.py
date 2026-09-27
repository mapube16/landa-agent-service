"""Sync de etapas: lee las conversaciones del sismo y mueve las etiquetas.

Corre cada 2 dias (cron en ``app/worker.py``). Por cada conversacion con
``siniestro-sismo``: lee la transcripcion, pregunta al modelo en que etapa va,
y si la etapa AVANZO mueve la etiqueta.

Modo por defecto: ``apply=False`` (silencioso). Escribe en el log lo que HARIA
sin tocar Chatwoot, para poder contrastar la lectura del modelo contra la
realidad antes de dejarlo escribir. Esto lo pidio el operador explicitamente.

Fail-soft por conversacion: un fallo puntual no aborta el barrido de las 145.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from app.features.metrics.quality import render_transcript
from app.features.siniestros.stages import (
    PREFIX,
    StageRead,
    current_stage,
    decide_stage,
    read_stage,
    stage_label,
)
from app.integrations.chatwoot import get_chatwoot_client

log = structlog.get_logger("features.siniestros.sync")

SISMO_LABEL = f"{PREFIX}sismo"
TRABADO_LABEL = f"{PREFIX}trabado"


@dataclass
class SyncReport:
    """Resultado de un barrido, para el log y el informe a DPG."""

    revisadas: int = 0
    avanzadas: int = 0
    sin_cambio: int = 0
    baja_confianza: int = 0
    errores: int = 0
    movimientos: list[dict[str, Any]] = field(default_factory=list)
    bloqueos: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "revisadas": self.revisadas,
            "avanzadas": self.avanzadas,
            "sin_cambio": self.sin_cambio,
            "baja_confianza": self.baja_confianza,
            "errores": self.errores,
            "bloqueos": self.bloqueos,
        }


def _labels_of(conv: dict[str, Any]) -> list[str]:
    raw = conv.get("labels") or conv.get("meta", {}).get("labels") or []
    return [str(x) for x in raw]


async def sync_stages(*, apply: bool = False, limit: int | None = None) -> SyncReport:
    """Recorre las conversaciones del sismo y actualiza sus etapas.

    ``apply=False`` (default) solo reporta; no escribe en Chatwoot.
    """
    client = get_chatwoot_client()
    rep = SyncReport()

    try:
        convs = await client.list_conversations_by_label(SISMO_LABEL)
    except Exception as exc:  # noqa: BLE001
        log.warning("siniestros.sync.list_failed", error_type=type(exc).__name__)
        return rep

    # El filtro ya viene del servidor; se revalida por si la etiqueta cambio
    # entre el listado y el barrido.
    target = [c for c in convs if SISMO_LABEL in _labels_of(c)]
    if limit is not None:
        target = target[:limit]
    log.info("siniestros.sync.start", total=len(target), apply=apply)

    for conv in target:
        conv_id = conv.get("id")
        if not conv_id:
            continue
        rep.revisadas += 1
        try:
            leida = await _read_one(client, int(conv_id))
        except Exception as exc:  # noqa: BLE001 — una conversacion no tumba el barrido
            rep.errores += 1
            log.warning(
                "siniestros.sync.conv_failed", conv_id=conv_id, error_type=type(exc).__name__
            )
            continue

        rep.bloqueos[leida.bloqueo] = rep.bloqueos.get(leida.bloqueo, 0) + 1
        if leida.confianza == "baja":
            rep.baja_confianza += 1

        actual = current_stage(_labels_of(conv))
        nueva = decide_stage(actual, leida)
        if nueva is None:
            rep.sin_cambio += 1
            continue

        rep.avanzadas += 1
        rep.movimientos.append(
            {
                "conv_id": conv_id,
                "de": actual,
                "a": nueva,
                "bloqueo": leida.bloqueo,
                "confianza": leida.confianza,
                "evidencia": leida.evidencia,
            }
        )
        log.info(
            "siniestros.sync.avance",
            conv_id=conv_id,
            de=actual,
            a=nueva,
            confianza=leida.confianza,
            apply=apply,
        )
        if apply:
            await client.set_stage_label(int(conv_id), stage_label(nueva), PREFIX)

    log.info("siniestros.sync.done", **rep.as_dict())
    return rep


async def _read_one(client: Any, conv_id: int) -> StageRead:
    msgs = await client.list_messages(conv_id)
    transcript = render_transcript(msgs)
    # Sin una sola linea del cliente no hay relato que leer: son hilos donde
    # solo hablo el bot (plantillas de campana sin respuesta). Se salta la
    # llamada al modelo — en la prueba del 15-sep eran 12 de 12.
    if not any(line.startswith("CLIENTE:") for line in transcript.splitlines()):
        return StageRead()
    return await read_stage(transcript)
