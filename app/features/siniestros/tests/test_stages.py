"""Tests de la lectura de etapa (features/siniestros/stages.py).

Lo que se protege aqui: el modelo mueve etiquetas con las que DPG prioriza 200
reclamaciones del terremoto. Un retroceso silencioso mandaria un caso que ya
iba en ofrecimiento de vuelta a aviso, y nadie lo notaria.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.features.siniestros.stages import (
    NOT_A_STAGE,
    STAGES,
    StageRead,
    current_stage,
    decide_stage,
    read_stage,
    stage_label,
)

# --- current_stage ---------------------------------------------------------


def test_lee_la_etapa_de_las_etiquetas() -> None:
    assert current_stage(["siniestro-sismo", "siniestro-perito"]) == "perito"


def test_ignora_sismo_y_trabado() -> None:
    """Son marcas, no etapas: una conversacion solo con ellas no tiene etapa."""
    assert current_stage(["siniestro-sismo", "siniestro-trabado"]) is None


def test_ignora_etiquetas_ajenas() -> None:
    assert current_stage(["escalado-humano", "promesa-pago"]) is None


def test_con_dos_etapas_gana_la_mas_avanzada() -> None:
    """No deberia pasar, pero si pasa no se pierde progreso."""
    assert current_stage(["siniestro-aviso", "siniestro-ofrecimiento"]) == "ofrecimiento"


# --- decide_stage: la regla que no se negocia ------------------------------


def test_no_retrocede() -> None:
    leida = StageRead(etapa="aviso", confianza="alta")
    assert decide_stage("ofrecimiento", leida) is None


def test_no_reaplica_la_misma() -> None:
    leida = StageRead(etapa="perito", confianza="alta")
    assert decide_stage("perito", leida) is None


def test_avanza_cuando_la_leida_es_posterior() -> None:
    leida = StageRead(etapa="perito", confianza="alta")
    assert decide_stage("reclamacion", leida) == "perito"


def test_primera_etapa_sin_etiqueta_previa() -> None:
    leida = StageRead(etapa="aviso", confianza="media")
    assert decide_stage(None, leida) == "aviso"


def test_confianza_baja_no_toca_nada() -> None:
    """Aunque sea un avance: adivinar no mueve la prioridad de DPG."""
    leida = StageRead(etapa="pago", confianza="baja")
    assert decide_stage("aviso", leida) is None


def test_sin_etapa_leida_no_toca_nada() -> None:
    assert decide_stage("aviso", StageRead(confianza="alta")) is None


def test_todas_las_etapas_avanzan_desde_la_anterior() -> None:
    """Recorre el proceso completo: cada etapa aplica sobre la previa."""
    for prev, nxt in zip(STAGES[:-1], STAGES[1:], strict=True):
        leida = StageRead(etapa=nxt, confianza="alta")
        assert decide_stage(prev, leida) == nxt, f"{prev} -> {nxt}"


def test_stage_label_no_colisiona_con_las_protegidas() -> None:
    assert all(stage_label(s) not in NOT_A_STAGE for s in STAGES)


# --- read_stage ------------------------------------------------------------


def _llm(result: object) -> MagicMock:
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=result)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


@pytest.mark.asyncio
async def test_lee_la_etapa_del_modelo(monkeypatch: pytest.MonkeyPatch) -> None:
    esperado = StageRead(etapa="perito", bloqueo="perito", confianza="alta", evidencia="ya vino")
    monkeypatch.setattr("app.features.siniestros.stages.get_llm", lambda role: _llm(esperado))
    assert (await read_stage("CLIENTE: ya vino el perito")).etapa == "perito"


@pytest.mark.asyncio
async def test_transcripcion_vacia_no_llama_al_modelo(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(role: str) -> None:  # pragma: no cover — no debe ejecutarse
        raise AssertionError("no se debe llamar al LLM con transcripcion vacia")

    monkeypatch.setattr("app.features.siniestros.stages.get_llm", boom)
    assert (await read_stage("   ")).etapa is None


@pytest.mark.asyncio
async def test_fallo_del_modelo_deja_la_etapa_intacta(monkeypatch: pytest.MonkeyPatch) -> None:
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=RuntimeError("gateway caido"))
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    monkeypatch.setattr("app.features.siniestros.stages.get_llm", lambda role: llm)

    leida = await read_stage("CLIENTE: algo")
    assert leida.etapa is None
    assert decide_stage("perito", leida) is None  # y por tanto no se toca nada
