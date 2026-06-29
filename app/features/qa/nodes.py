"""Q&A graph node functions — implemented in Plan 03-05.

Each function is an async LangGraph node that receives the full ``QAState``
dict and returns a partial dict of fields to merge back into state.

Node contract (per D-04):
- ``node_identify``: reads incoming message, extracts document number, calls
  ``SoftSegurosClient.get_clientes_by_documento``, then
  ``get_polizas_by_cliente``. Transitions to ``awaiting_policy_choice`` (N≥2)
  or ``answering_qa`` (N=1), or increments ``doc_retries`` on miss.
- ``node_choose_policy``: parses client's numeric choice, locks ``poliza_id``
  in state, transitions to ``answering_qa``.
- ``node_answer``: conversation LLM + tool calls + judge pipeline. On
  rejection after retry, transitions to ``escalating``.
- ``node_escalate``: creates/updates Chatwoot conversation, sends T-08
  template, marks state ``escalating``.
- ``node_close``: marks Chatwoot conversation resolved, sets state ``closed``.

Implemented in: Plan 03-05.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.features.qa.state import QAState

log = structlog.get_logger("features.qa.nodes")

__all__ = [
    "node_answer",
    "node_choose_policy",
    "node_close",
    "node_escalate",
    "node_identify",
]


async def node_identify(state: QAState) -> dict[str, Any]:
    """Identify client by document number and fetch their pólizas.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")


async def node_choose_policy(state: QAState) -> dict[str, Any]:
    """Parse client's numeric policy choice and lock poliza_id in state.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")


async def node_answer(state: QAState) -> dict[str, Any]:
    """Run conversation LLM + tool calls + judge pipeline.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")


async def node_escalate(state: QAState) -> dict[str, Any]:
    """Create/update Chatwoot conversation and send escalation template.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")


async def node_close(state: QAState) -> dict[str, Any]:
    """Mark Chatwoot conversation resolved and set state to closed.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")
