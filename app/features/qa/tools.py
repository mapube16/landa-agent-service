"""Q&A LangGraph tools — implemented in Plan 03-05.

All tools that require the locked ``poliza_id`` receive it via
``InjectedState("poliza_id")`` (LangGraph ``ToolNode`` injects from
``QAState`` at runtime). The LLM NEVER supplies ``poliza_id`` — the
tool's JSON schema exposes no argument for it, so the LLM cannot alter
the locked policy identity mid-conversation (CLAUDE.md capa 2, D-04).

Tool contract:
- ``get_saldo``: queries SoftSeguros for balance/next-payment, returns
  allowlisted dict serialized as JSON string.
- ``get_estado``: queries SoftSeguros for policy status and validity dates,
  returns allowlisted dict serialized as JSON string.
- ``get_coberturas``: queries SoftSeguros for policy coverages, returns
  allowlisted dict serialized as JSON string.
- ``escalate_to_human``: transitions graph to ``escalating`` node; returns
  empty string to LLM (no PII leaks through tool return, reason logged
  to structlog only).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any

import structlog
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.integrations.softseguros import get_softseguros_client

log = structlog.get_logger("features.qa.tools")

# ---------------------------------------------------------------------------
# Injection-pattern strip (applied to string values from SoftSeguros output)
# ---------------------------------------------------------------------------

_INJECTION_STRIP: re.Pattern[str] = re.compile(r"(?i)(system|instruction|assistant)\s*:|<\|.*?\|>")

# ---------------------------------------------------------------------------
# Allowlists per endpoint (Claude's Discretion — 03-CONTEXT.md)
# ---------------------------------------------------------------------------

SALDO_ALLOWLIST: list[str] = [
    "saldo_pendiente",
    "proximo_pago_monto",
    "proximo_pago_fecha",
    "moneda",
]

ESTADO_ALLOWLIST: list[str] = [
    "estado_poliza_nombre",
    "fecha_inicio",
    "fecha_fin",
    "ramo_nombre",
    "numero_poliza",
]

COBERTURAS_NESTED_ALLOWLIST: list[str] = ["nombre", "monto_asegurado", "deducible"]


def _clean_value(v: Any) -> Any:
    """Strip injection patterns from string values recursively."""
    if isinstance(v, str):
        return _INJECTION_STRIP.sub("", v).strip()
    if isinstance(v, list):
        return [_clean_value(item) for item in v]
    if isinstance(v, dict):
        return {k: _clean_value(val) for k, val in v.items()}
    return v


def sanitize_tool_output(data: dict[str, Any] | None, allowlist: list[str]) -> str:
    """Filter to allowlist fields and strip injection patterns from string values.

    Args:
        data: Raw dict from SoftSeguros (or None).
        allowlist: Permitted top-level keys that may reach the LLM.

    Returns:
        JSON string with only allowlisted fields, all string values cleaned.
    """
    if data is None:
        return json.dumps({})
    filtered: dict[str, Any] = {k: _clean_value(v) for k, v in data.items() if k in allowlist}
    return json.dumps(filtered, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def get_saldo(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> str:
    """Consulta el saldo pendiente y próximo pago de la póliza activa.

    No requiere argumentos — la póliza viene del estado de la conversación.
    """
    client = get_softseguros_client()
    raw = await client.get_poliza(poliza_id)
    payload = {
        "saldo_pendiente": raw.get("saldo_pendiente"),
        "proximo_pago_monto": raw.get("proximo_pago_monto"),
        "proximo_pago_fecha": raw.get("proximo_pago_fecha"),
        "moneda": raw.get("moneda", "COP"),
    }
    return sanitize_tool_output(payload, SALDO_ALLOWLIST)


@tool
async def get_estado(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> str:
    """Consulta el estado y fechas de vigencia de la póliza activa.

    No requiere argumentos — la póliza viene del estado de la conversación.

    Nota: ``/api/estadopoliza/{id}/`` devuelve 404 (documentado en
    SOFTSEGUROS_API_NOTES.md). El estado vive embebido en el objeto poliza
    como ``estado_poliza_nombre`` + ``estado_poliza_codigo``, así que leemos
    desde ``get_poliza`` (mismo endpoint que ``get_saldo``/``get_coberturas``).
    """
    client = get_softseguros_client()
    raw = await client.get_poliza(poliza_id)
    payload = {
        "estado_poliza_nombre": raw.get("estado_poliza_nombre"),
        "fecha_inicio": raw.get("fecha_inicio"),
        "fecha_fin": raw.get("fecha_fin"),
        "ramo_nombre": raw.get("ramo_nombre"),
        "numero_poliza": raw.get("numero_poliza"),
    }
    return sanitize_tool_output(payload, ESTADO_ALLOWLIST)


@tool
async def get_coberturas(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> str:
    """Consulta las coberturas de la póliza activa.

    No requiere argumentos — la póliza viene del estado de la conversación.
    """
    client = get_softseguros_client()
    raw = await client.get_poliza(poliza_id)
    raw_coberturas: list[dict[str, Any]] = raw.get("coberturas", [])
    clean_coberturas = [
        {k: _clean_value(v) for k, v in c.items() if k in COBERTURAS_NESTED_ALLOWLIST}
        for c in raw_coberturas
        if isinstance(c, dict)
    ]
    return json.dumps({"coberturas": clean_coberturas}, ensure_ascii=False)


@tool
async def escalate_to_human(reason: str) -> str:
    """Escalar la conversación a un agente humano.

    Llamar cuando el cliente lo pide explícitamente, expresa frustración,
    o cuando no puedes responder con la información disponible.
    El ``reason`` es para logging interno, no se muestra al cliente.
    """
    # ponytail: hash reason so PII in client phrasing never hits the log
    log.info(
        "qa.tool.escalate_to_human.fired",
        reason_hash=hashlib.sha256(reason.encode()).hexdigest()[:8],
    )
    return ""  # Empty return — conditional edge detects the tool call in messages


__all__ = [
    "COBERTURAS_NESTED_ALLOWLIST",
    "ESTADO_ALLOWLIST",
    "SALDO_ALLOWLIST",
    "escalate_to_human",
    "get_coberturas",
    "get_estado",
    "get_saldo",
    "sanitize_tool_output",
]
