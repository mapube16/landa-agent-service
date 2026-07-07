"""Pydantic-friendly types for SoftSeguros REST responses.

Phase 2 established ``PolizaRaw = dict[str, Any]`` as a passthrough alias
(no real response captured yet). Phase 3 narrows additional models using
confirmed field shapes from the 03-00 probe (Task 1 — 122-field cliente
shape, Task 3 — 8 estado enum values).

``ClienteRaw`` is a TypedDict narrowed to the ~20 fields that Phase 3
Q&A tools actually consume (per 03-00-PROBE.md "Fields relevant for
Plan 03-01"). Remaining 100+ fields stay out via
``model_config = ConfigDict(extra="ignore")``.

``EstadoCodigo`` enumerates the 8 values observed at ``/api/estadopoliza/``
(see SOFTSEGUROS_API_NOTES.md — note: ``codigo`` is NOT unique; we key by
``nombre``).

Sanitized DTOs (``SaldoResponse``, ``EstadoResponse``, ``CoberturasResponse``,
``PolizaSummary``) define the allowlist of fields that tool outputs expose to
the LLM (CLAUDE.md Capa 4 — tool output sanitization). Fields beyond the
allowlist are stripped before the LLM ever sees them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Phase 2 passthrough alias — kept for F2 callers; do NOT remove.
# ---------------------------------------------------------------------------

PolizaRaw = dict[str, Any]


# ---------------------------------------------------------------------------
# Phase 3 additions
# ---------------------------------------------------------------------------


class EstadoCodigo(StrEnum):
    """Human-readable estado names from ``/api/estadopoliza/`` lookup table.

    Values observed in 03-00 probe / SOFTSEGUROS_API_NOTES.md.
    Note: ``codigo`` (01/02/…) is NOT unique in that table — we use
    ``nombre`` as the canonical key here.
    """

    VIGENTE = "Vigente"
    COTIZACION = "Cotizacion"
    DEVENGADA = "Devengada"
    EXPEDICION = "Expedicion"
    NO_RENOVADA = "No renovada"
    CANCELADA = "Cancelada"
    NUEVA = "Nueva"
    VENCIDA = "Vencida"


class ClienteRaw(TypedDict, total=False):
    """Narrowed TypedDict for ``/api/cliente/listar_cliente_por_documento/`` responses.

    Only the ~20 fields Phase 3 Q&A tools need are declared (03-00-PROBE.md
    §"Fields relevant for Plan 03-01 ClienteRaw"). The remaining 100+ fields
    in the 122-field response are silently ignored — callers parse with
    ``TypedDict`` or ``BaseModel(model_config=ConfigDict(extra='ignore'))``.

    PII fields (``celular``, ``email``, ``direccion``, ``pais``, ``provincia``,
    ``ciudad``) are included here because ``node_identify`` (Plan 03-05) needs
    the ``id`` for the secondary poliza call, but the sanitized LLM DTO
    (``ClienteSanitized``) strips them before any LLM interaction.
    """

    id: int
    nombres: str
    apellidos: str
    numero_documento: str
    tipo_documento: str
    model_tipo_documento_code: str
    model_tipo_documento_name: str
    digito_verificacion: str | None
    email: str
    celular: str
    telefono: str
    direccion: str
    pais: str
    provincia: str
    ciudad: str
    es_consorcio: bool
    tipo_cliente: str
    activo: bool
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Sanitized DTOs — allowlist per CONTEXT.md "Tool output sanitization
# allowlist por endpoint SoftSeguros" (Claude's Discretion section).
# Fields beyond each allowlist NEVER reach the LLM.
# ---------------------------------------------------------------------------


class SaldoResponse(BaseModel):
    """Allowlist for ``get_saldo`` tool output (saldo + próximo pago)."""

    model_config = ConfigDict(extra="ignore")

    saldo_pendiente: float | None = None
    proximo_pago_monto: float | None = None
    proximo_pago_fecha: str | None = None
    moneda: str | None = None


class EstadoResponse(BaseModel):
    """Allowlist for ``get_estado`` tool output (estado + fechas vigencia)."""

    model_config = ConfigDict(extra="ignore")

    estado_poliza_nombre: str | None = None
    fecha_inicio: str | None = None
    fecha_fin: str | None = None
    ramo_nombre: str | None = None
    numero_poliza: str | None = None


class Cobertura(BaseModel):
    """Single cobertura entry inside ``CoberturasResponse``."""

    model_config = ConfigDict(extra="ignore")

    nombre: str | None = None
    monto_asegurado: str | None = None
    deducible: str | None = None


class CoberturasResponse(BaseModel):
    """Allowlist for ``get_coberturas`` tool output."""

    model_config = ConfigDict(extra="ignore")

    coberturas: list[Cobertura] = []


class PolizaSummary(BaseModel):
    """Compact poliza descriptor for T-04 numbered list (awaiting_policy_choice node)."""

    model_config = ConfigDict(extra="ignore")

    poliza_id: str
    numero_poliza: str
    ramo_nombre: str
    estado: str


class CarteraStatus(BaseModel):
    """Allowlist for ``get_cartera_status`` (Capa 4) — L4 flags enrichment.

    Sourced from ``list_pagospolizas_filtro_paginados`` (~150 raw fields incl.
    commissions/PII); this DTO is the only shape that leaves
    ``SoftSegurosClient`` for this endpoint. ``riesgo`` maps from the raw
    ``poliza_codio_objeto_asegurado`` field (sic — typo in the upstream API);
    for ``ramo_nombre="AUTOMÓVILES"`` it holds the vehicle plate.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    fecha_pago: str | None = None
    fecha_realizara_pago: str | None = None
    fecha_realizo_pago: str | None = None
    saldo_pendiente: str | None = None
    edad_cartera: int | None = None
    ramo_nombre: str | None = None
    riesgo: str | None = Field(default=None, validation_alias="poliza_codio_objeto_asegurado")


__all__ = [
    "CarteraStatus",
    "ClienteRaw",
    "Cobertura",
    "CoberturasResponse",
    "EstadoCodigo",
    "EstadoResponse",
    "PolizaRaw",
    "PolizaSummary",
    "SaldoResponse",
]
