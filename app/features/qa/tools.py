"""Q&A LangGraph tools — implemented in Plan 03-05.

All tools that require the locked ``poliza_id`` receive it via
``InjectedState("poliza_id")`` (LangGraph ``ToolNode`` injects from
``QAState`` at runtime). The LLM NEVER supplies ``poliza_id`` — the
tool's JSON schema exposes no argument for it, so the LLM cannot alter
the locked policy identity mid-conversation (CLAUDE.md capa 2, D-04).

Tool contract:
- ``get_saldo``: queries SoftSeguros for balance/next-payment, returns
  ``SaldoResponse``-shaped dict (allowlist only).
- ``get_estado``: queries SoftSeguros for policy status and validity dates,
  returns ``EstadoResponse``-shaped dict.
- ``get_coberturas``: queries SoftSeguros for policy coverages, returns
  ``CoberturasResponse``-shaped dict.
- ``escalate_to_human``: transitions graph to ``escalating`` node; returns
  empty string to LLM (no PII leaks through tool return, reason logged
  to LangSmith + structlog only).

Implemented in: Plan 03-05.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.features.qa.state import QAState

log = structlog.get_logger("features.qa.tools")

__all__ = [
    "escalate_to_human",
    "get_coberturas",
    "get_estado",
    "get_saldo",
]


@tool
def get_saldo(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> dict[str, Any]:
    """Consultar el saldo pendiente y el próximo pago de la póliza activa.

    Returns an allowlisted ``SaldoResponse``-shaped dict. ``poliza_id``
    is injected from graph state — the LLM cannot supply it.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")


@tool
def get_estado(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> dict[str, Any]:
    """Consultar el estado y fechas de vigencia de la póliza activa.

    Returns an allowlisted ``EstadoResponse``-shaped dict. ``poliza_id``
    is injected from graph state — the LLM cannot supply it.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")


@tool
def get_coberturas(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> dict[str, Any]:
    """Consultar las coberturas de la póliza activa.

    Returns an allowlisted ``CoberturasResponse``-shaped dict. ``poliza_id``
    is injected from graph state — the LLM cannot supply it.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")


@tool
def escalate_to_human(
    reason: str,
    state: Annotated[QAState, InjectedState()],
) -> str:
    """Escalar la conversación a un agente humano en Chatwoot.

    ``reason`` is logged to LangSmith + structlog for post-hoc analysis
    but NEVER returned to the LLM (returns empty string). The node
    function checks this call and transitions state to ``escalating``.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")
