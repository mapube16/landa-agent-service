"""Q&A graph node functions — implemented in Plan 03-05.

Each function is an async LangGraph node that receives the full ``QAState``
dict and returns a partial dict of fields to merge back into state.

Node contract (per D-04):
- ``node_identify``: reads incoming message, extracts document number, calls
  ``SoftSegurosClient.get_clientes_by_documento``, then
  ``get_polizas_by_cliente``. Transitions to ``awaiting_policy_choice`` (N>=2)
  or ``answering_qa`` (N=1), or increments ``doc_retries`` on miss.
- ``node_choose_policy``: parses client's numeric choice, locks ``poliza_id``
  in state, transitions to ``answering_qa``.
- ``node_answer``: conversation LLM + tool calls + judge pipeline. On
  rejection after retry, transitions to ``escalating``.
- ``node_escalate``: logs escalation reason, sets terminal state.
- ``node_close``: sets state to closed (terminal).

Route functions are pure (no I/O) — they read the ``node`` field that upstream
node functions set, so the graph edges are data-driven.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import pybreaker
import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.features.qa.knowledge_base import load_kb
from app.features.qa.messages import T_01, T_02, T_03, T_06, T_07, T_08, interpolate_t04
from app.features.qa.prompts import system_prompt
from app.features.qa.state import QAState
from app.features.qa.tools import escalate_to_human, get_coberturas, get_estado, get_saldo
from app.integrations.openrouter import get_llm
from app.integrations.softseguros import get_softseguros_client
from app.security.judge import is_approved, judge_response

log = structlog.get_logger("features.qa.nodes")

_TOOLS = [get_saldo, get_estado, get_coberturas, escalate_to_human]

# Emoji number prefixes for policy lists (D-02)
_EMOJI_NUMS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

__all__ = [
    "node_answer",
    "node_choose_policy",
    "node_close",
    "node_escalate",
    "node_identify",
    "route_from_answering",
    "route_from_identification",
    "route_from_policy_choice",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _last_human_text(state: QAState) -> str:
    """Return the content of the most recent HumanMessage in state, or ''."""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            return str(msg.content).strip()
    return ""


def _build_policy_list(polizas: list[dict[str, Any]]) -> str:
    """Build the numbered emoji list for T-04."""
    lines = []
    for i, p in enumerate(polizas):
        emoji = _EMOJI_NUMS[i] if i < len(_EMOJI_NUMS) else f"{i + 1}."
        numero = p.get("numero_poliza", p.get("id", "?"))
        ramo = p.get("ramo_nombre", p.get("ramo", ""))
        estado = p.get("estado_poliza_nombre", p.get("estado", ""))
        lines.append(f"{emoji} POL-{numero} ({ramo}, {estado})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# node_identify
# ---------------------------------------------------------------------------


async def node_identify(state: QAState) -> dict[str, Any]:
    """Identify client by document number and fetch their pólizas.

    Returns state mutations — never raises (all exceptions caught and routed
    to ``escalating`` or retry).
    """
    text = _last_human_text(state)

    # First contact or no document requested yet → emit T-01 and wait
    if not state.get("asked_for_doc"):
        return {
            "node": "awaiting_identification",
            "asked_for_doc": True,
            "messages": [AIMessage(content=T_01)],
        }

    try:
        client = get_softseguros_client()
        # Step 1: get cliente by documento
        cliente = await client.get_clientes_by_documento(text)
        cliente_id: int | None = cliente.get("id") if isinstance(cliente, dict) else None

        if cliente_id is None:
            # Document not found (no cliente returned)
            doc_retries = state.get("doc_retries", 0)
            if doc_retries >= 1:
                return {
                    "node": "escalating",
                    "escalation_reason": "doc_exhausted",
                    "messages": [AIMessage(content=T_03)],
                }
            return {
                "node": "awaiting_identification",
                "doc_retries": doc_retries + 1,
                "cliente_doc": text,
                "messages": [AIMessage(content=T_02)],
            }

        # Step 2: get polizas for this cliente
        polizas: list[dict[str, Any]] = await client.get_polizas_by_cliente(cliente_id)

    except pybreaker.CircuitBreakerError:
        log.warning("node_identify.breaker_open")
        return {
            "node": "escalating",
            "escalation_reason": "breaker",
            "messages": [AIMessage(content=T_06)],
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("node_identify.error", error_type=type(exc).__name__)
        doc_retries = state.get("doc_retries", 0)
        if doc_retries >= 1:
            return {
                "node": "escalating",
                "escalation_reason": "doc_exhausted",
                "messages": [AIMessage(content=T_03)],
            }
        return {
            "node": "awaiting_identification",
            "doc_retries": doc_retries + 1,
            "cliente_doc": text,
            "messages": [AIMessage(content=T_02)],
        }

    n = len(polizas)

    if n == 0:
        doc_retries = state.get("doc_retries", 0)
        if doc_retries >= 1:
            return {
                "node": "escalating",
                "escalation_reason": "doc_exhausted",
                "messages": [AIMessage(content=T_03)],
            }
        return {
            "node": "awaiting_identification",
            "doc_retries": doc_retries + 1,
            "cliente_doc": text,
            "messages": [AIMessage(content=T_02)],
        }

    if n == 1:
        p = polizas[0]
        poliza_id = str(p.get("id", p.get("numero_poliza", "")))
        numero = p.get("numero_poliza", poliza_id)
        return {
            "node": "answering_qa",
            "poliza_id": poliza_id,
            "cliente_doc": text,
            "polizas_list": polizas,
            "messages": [
                AIMessage(
                    content=(
                        f"Identifiqué tu póliza POL-{numero}."
                        " ¿En qué puedo ayudarte?"
                        " Puedo consultar saldo, estado o coberturas."
                    )
                )
            ],
        }

    # N >= 2
    lista_numerada = _build_policy_list(polizas)
    return {
        "node": "awaiting_policy_choice",
        "polizas_list": polizas,
        "cliente_doc": text,
        "messages": [AIMessage(content=interpolate_t04(n, lista_numerada))],
    }


def route_from_identification(
    state: QAState,
) -> Literal["awaiting_policy_choice", "answering_qa", "escalating"] | str:
    """Conditional edge after node_identify — reads state.node set by the node.

    Returns END (via the string sentinel) when node_identify wants to wait for
    the next user message (T-01 or T-02 emitted). This stops the graph from
    looping back into node_identify within the same invocation.
    """
    from langgraph.graph import END

    n = state.get("node", "awaiting_identification")
    if n == "awaiting_identification":
        return END
    return n


# ---------------------------------------------------------------------------
# node_choose_policy
# ---------------------------------------------------------------------------

_NUMERIC_RE = re.compile(r"^\s*([1-9]\d*)\s*$")
_POLIZA_RE = re.compile(r"\b(POL-?\d+|\d{5,8})\b", re.IGNORECASE)


def _resolve_by_number_pattern(text: str, polizas: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Layer 2: match poliza number pattern in text."""
    m2 = _POLIZA_RE.search(text)
    if not m2:
        return None
    raw_match = re.sub(r"^POL-?", "", m2.group(1).upper())
    for p in polizas:
        numero = str(p.get("numero_poliza", ""))
        if numero == raw_match or numero.lstrip("0") == raw_match.lstrip("0"):
            return p
    return None


async def _resolve_by_llm_fallback(
    text: str, polizas: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Layer 3: LLM fallback with allowlist — only when layers 1+2 fail."""
    allowlist_nums = [str(p.get("numero_poliza", "")) for p in polizas]
    try:
        llm = get_llm("intent")
        fallback_prompt = (
            f"El cliente respondió: '{text}'.\n"
            f"¿A cuál de estos numero_poliza se refiere? Allowlist: {allowlist_nums}\n"
            "Responde SOLO el numero exacto de la lista, o NONE si no podés decidir."
        )
        resp = await llm.ainvoke(fallback_prompt)
        llm_text = str(getattr(resp, "content", resp)).strip()
        for p in polizas:
            if str(p.get("numero_poliza", "")) == llm_text:
                return p
    except Exception as exc:  # noqa: BLE001
        log.warning("node_choose_policy.llm_fallback_failed", error_type=type(exc).__name__)
    return None


async def node_choose_policy(state: QAState) -> dict[str, Any]:
    """Parse client's numeric policy choice and lock poliza_id in state."""
    text = _last_human_text(state)
    polizas: list[dict[str, Any]] = state.get("polizas_list", [])

    resolved: dict[str, Any] | None = None

    # Layer 1: numeric index
    m = _NUMERIC_RE.match(text)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= len(polizas):
            resolved = polizas[idx - 1]

    if resolved is None:
        resolved = _resolve_by_number_pattern(text, polizas)

    if resolved is None:
        resolved = await _resolve_by_llm_fallback(text, polizas)

    if resolved is None:
        # Stay in awaiting_policy_choice — re-prompt
        return {
            "node": "awaiting_policy_choice",
            "messages": [
                AIMessage(
                    content=(
                        "No entendí bien."
                        " Por favor respondé con el número (1, 2, 3)"
                        " o el número de póliza (POL-XXXXX) de la lista."
                    )
                )
            ],
        }

    poliza_id = str(resolved.get("id", resolved.get("numero_poliza", "")))
    numero = resolved.get("numero_poliza", poliza_id)
    return {
        "node": "answering_qa",
        "poliza_id": poliza_id,
        "messages": [AIMessage(content=f"Listo, sobre la póliza POL-{numero}. ¿Qué querés saber?")],
    }


def route_from_policy_choice(
    state: QAState,
) -> Literal["answering_qa", "awaiting_policy_choice"]:
    """Conditional edge after node_choose_policy."""
    return state.get("node", "awaiting_policy_choice")  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# node_answer
# ---------------------------------------------------------------------------


async def node_answer(state: QAState) -> dict[str, Any]:
    """Run conversation LLM + tool calls + judge pipeline.

    Outbound dispatch: node returns AIMessage with additional_kwargs
    ``{"send_to_client": True}``; the webhook dispatcher extracts this and
    calls meta.send_text + arq.enqueue_job("mirror_outbound", ...) after
    the graph completes. Keeps nodes pure (no I/O side effects beyond LLM).
    """
    poliza_id: str | None = state.get("poliza_id")
    judge_retries: int = state.get("judge_retries", 0)
    last_rationale: str | None = state.get("last_rejection_rationale")

    # Build system prompt
    sp = system_prompt(kb_content=load_kb(), poliza_id=poliza_id)
    if judge_retries > 0 and last_rationale:
        sp += (
            f"\n\nLa respuesta anterior fue rechazada por el judge: {last_rationale}."
            " Reformulá sin incluir ese contenido."
        )

    llm = get_llm("conversation").bind_tools(_TOOLS)
    messages_with_system = [SystemMessage(content=sp), *state["messages"]]

    # First LLM call
    result = await llm.ainvoke(messages_with_system)

    # Check for escalate_to_human tool call (escape hatch Layer 2)
    if hasattr(result, "tool_calls") and result.tool_calls:
        for tc in result.tool_calls:
            if tc.get("name") == "escalate_to_human":
                return {
                    "node": "escalating",
                    "escalation_reason": "escape_hatch",
                    "messages": [AIMessage(content=T_08)],
                }

        # Execute other tool calls via ToolNode pattern (manual execution)
        from langgraph.prebuilt import ToolNode

        tool_node = ToolNode(_TOOLS)
        # Build state with the tool call message appended
        tool_input_state = dict(state)
        tool_input_state["messages"] = [*state["messages"], result]
        tool_results = await tool_node.ainvoke(tool_input_state)

        # Get tool messages from results
        tool_messages: list[Any] = tool_results.get("messages", [])

        # Second LLM call with tool results
        messages_with_tools = [
            SystemMessage(content=sp),
            *state["messages"],
            result,
            *tool_messages,
        ]
        result = await llm.ainvoke(messages_with_tools)

    final_response = str(getattr(result, "content", result))

    # Judge pipeline
    rubric = await judge_response(state["messages"], final_response)
    if rubric is None or not is_approved(rubric):
        if judge_retries >= 1:
            return {
                "node": "escalating",
                "escalation_reason": "judge_rejected",
                "messages": [AIMessage(content=T_07)],
            }
        return {
            "node": "answering_qa",
            "judge_retries": judge_retries + 1,
            "last_rejection_rationale": rubric.rationale if rubric else "no rubric parsed",
            # ponytail: no message emitted — next turn will produce a new response
        }

    return {
        "node": "answering_qa",
        "judge_retries": 0,
        "last_rejection_rationale": None,
        "messages": [
            AIMessage(
                content=final_response,
                additional_kwargs={"send_to_client": True},
            )
        ],
    }


def route_from_answering(
    state: QAState,
) -> Literal["answering_qa", "escalating", "closed"]:
    """Conditional edge after node_answer."""
    return state.get("node", "answering_qa")  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# node_escalate
# ---------------------------------------------------------------------------


async def node_escalate(state: QAState) -> dict[str, Any]:
    """Log escalation reason. Side effects (Chatwoot, meta) handled by webhook dispatcher."""
    reason = state.get("escalation_reason", "unknown")
    log.info("node_escalate.terminal", reason=reason)
    # Template already set by upstream node — return no-op dict
    return {"node": "escalating"}


# ---------------------------------------------------------------------------
# node_close
# ---------------------------------------------------------------------------


async def node_close(state: QAState) -> dict[str, Any]:
    """Mark conversation closed. Chatwoot.mark_resolved handled by webhook dispatcher."""
    log.info("node_close.terminal")
    return {"node": "closed"}
