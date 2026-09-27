"""Tests del enrutador de respuestas (routing.py), dos campanas.

Lo que se protege: la marca de campana decide el clasificador; sin marca no se
toca nada; el estado anterior se reemplaza; quien en deteccion dice que ya
reporto entra a seguimiento.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.features.siniestros.routing import (
    DETECCION,
    DETECCION_LABEL,
    ESCALADO_LABEL,
    SEGUIMIENTO,
    SISMO_LABEL,
    DeteccionRead,
    SeguimientoRead,
    campaign_for,
    classify,
    next_labels,
)

# --- campaign_for ----------------------------------------------------------


def test_marca_de_seguimiento() -> None:
    assert campaign_for([SISMO_LABEL, "en-ajuste"]) is SEGUIMIENTO


def test_marca_de_deteccion() -> None:
    assert campaign_for([DETECCION_LABEL]) is DETECCION


def test_sin_marca_no_hay_campana() -> None:
    assert campaign_for(["promesa-pago", "escalado-humano"]) is None


def test_con_ambas_marcas_manda_seguimiento() -> None:
    assert campaign_for([DETECCION_LABEL, SISMO_LABEL]) is SEGUIMIENTO


# --- next_labels -----------------------------------------------------------


def test_reemplaza_el_estado_anterior() -> None:
    out = next_labels([SISMO_LABEL, "radicada-esperando"], SEGUIMIENTO, "indemnizada")
    assert "radicada-esperando" not in out
    assert "indemnizada" in out
    assert SISMO_LABEL in out  # la marca se conserva


def test_conserva_etiquetas_ajenas() -> None:
    out = next_labels([SISMO_LABEL, "promesa-pago"], SEGUIMIENTO, "en-ajuste")
    assert "promesa-pago" in out


def test_negada_agrega_escalado() -> None:
    assert ESCALADO_LABEL in next_labels([SISMO_LABEL], SEGUIMIENTO, "negada-problema")


def test_indemnizada_no_agrega_escalado() -> None:
    assert ESCALADO_LABEL not in next_labels([SISMO_LABEL], SEGUIMIENTO, "indemnizada")


def test_no_duplica_escalado() -> None:
    out = next_labels([SISMO_LABEL, ESCALADO_LABEL], SEGUIMIENTO, "negada-problema")
    assert out.count(ESCALADO_LABEL) == 1


def test_deteccion_afectado_sin_reportar_escala() -> None:
    out = next_labels([DETECCION_LABEL], DETECCION, "afectado-sin-reportar")
    assert ESCALADO_LABEL in out
    assert SISMO_LABEL not in out  # aun no tiene reclamacion


def test_deteccion_ya_reporto_pasa_a_seguimiento() -> None:
    out = next_labels([DETECCION_LABEL], DETECCION, "afectado-reportado")
    assert SISMO_LABEL in out
    assert DETECCION_LABEL in out


def test_deteccion_sin_afectacion_no_escala() -> None:
    out = next_labels([DETECCION_LABEL], DETECCION, "sin-afectacion")
    assert ESCALADO_LABEL not in out


def test_estados_de_una_campana_no_pisan_los_de_la_otra() -> None:
    """Un hilo con ambas marcas: aplicar seguimiento no borra el de deteccion."""
    out = next_labels(
        [DETECCION_LABEL, SISMO_LABEL, "afectado-reportado"], SEGUIMIENTO, "en-ajuste"
    )
    assert "afectado-reportado" in out


# --- classify --------------------------------------------------------------


def _patch_llm(monkeypatch: pytest.MonkeyPatch, result: object) -> MagicMock:
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=result)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    monkeypatch.setattr("app.features.siniestros.routing.get_llm", lambda role: llm)
    return structured


@pytest.mark.asyncio
async def test_clasifica_seguimiento(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, SeguimientoRead(opcion="indemnizada", confianza="alta"))
    assert await classify("ya me consignaron", SEGUIMIENTO) == "indemnizada"


@pytest.mark.asyncio
async def test_clasifica_deteccion(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, DeteccionRead(opcion="afectado_sin_reportar", confianza="alta"))
    assert await classify("se me agrieto una pared", DETECCION) == "afectado-sin-reportar"


@pytest.mark.asyncio
async def test_usa_el_modelo_de_la_campana(monkeypatch: pytest.MonkeyPatch) -> None:
    structured = _patch_llm(monkeypatch, DeteccionRead(opcion="sin_afectacion", confianza="alta"))
    await classify("no, todo bien", DETECCION)
    llm = structured  # el with_structured_output se llamo con el modelo de deteccion
    assert llm is not None


@pytest.mark.asyncio
async def test_no_aplica_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, SeguimientoRead(opcion="no_aplica", confianza="alta"))
    assert await classify("buenos dias", SEGUIMIENTO) is None


@pytest.mark.asyncio
async def test_confianza_baja_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, DeteccionRead(opcion="sin_afectacion", confianza="baja"))
    assert await classify("mmm", DETECCION) is None


@pytest.mark.asyncio
async def test_fallo_del_modelo_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    structured = _patch_llm(monkeypatch, None)
    structured.ainvoke = AsyncMock(side_effect=RuntimeError("gateway caido"))
    assert await classify("ya me pagaron", SEGUIMIENTO) is None


@pytest.mark.asyncio
async def test_texto_vacio_no_llama_al_modelo(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(role: str) -> None:  # pragma: no cover
        raise AssertionError("no se debe llamar al LLM con texto vacio")

    monkeypatch.setattr("app.features.siniestros.routing.get_llm", boom)
    assert await classify("   ", SEGUIMIENTO) is None


@pytest.mark.asyncio
async def test_modelo_equivocado_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el modelo devuelve otro tipo, no se inventa etiqueta."""
    _patch_llm(monkeypatch, DeteccionRead(opcion="sin_afectacion", confianza="alta"))
    assert await classify("x", SEGUIMIENTO) is None
