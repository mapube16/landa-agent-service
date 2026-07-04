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

import hashlib
import re
from typing import Any

import pybreaker
import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.features.qa.knowledge_base import load_kb
from app.features.qa.messages import T_01, T_02, T_03, T_06, T_07, T_08
from app.features.qa.prompts import system_prompt
from app.features.qa.state import QAState
from app.features.qa.tools import escalate_to_human, get_coberturas, get_estado, get_saldo
from app.integrations.openrouter import get_llm
from app.integrations.softseguros import get_softseguros_client
from app.security import audit_log
from app.security.judge import is_approved, judge_response

log = structlog.get_logger("features.qa.nodes")

_TOOLS = [get_saldo, get_estado, get_coberturas, escalate_to_human]

# Emoji number prefixes for policy lists (D-02)
_EMOJI_NUMS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# WhatsApp interactive list cap per Meta docs.
_LIST_PAGE_SIZE = 9  # 9 polizas + 1 "Ver más" row = 10 total (Meta limit)
_MORE_BUTTON_ID = "__more"
_QA_BUTTON_IDS = {"saldo", "estado", "coberturas", "agente"}

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


def _polizas_list_message(polizas: list[dict[str, Any]], page: int) -> AIMessage:
    """Build a paged interactive list AIMessage from the polizas list.

    Meta caps interactive lists at 10 rows. We show ``_LIST_PAGE_SIZE`` rows
    plus a "Ver más" row whenever more pages exist. The selected row's id is
    the poliza_id (or numero_poliza fallback); the "Ver más" row uses
    ``_MORE_BUTTON_ID`` and ``node_choose_policy`` advances ``polizas_page``.
    """
    n = len(polizas)
    start = page * _LIST_PAGE_SIZE
    end = min(start + _LIST_PAGE_SIZE, n)
    page_slice = polizas[start:end]
    has_more = end < n
    rows: list[tuple[str, str, str | None]] = []
    for p in page_slice:
        pid = str(p.get("id", p.get("numero_poliza", "")))
        numero = p.get("numero_poliza", pid)
        ramo = p.get("ramo_nombre", p.get("ramo", ""))
        estado = p.get("estado_poliza_nombre", p.get("estado", ""))
        title = f"POL-{numero}"[:24]
        desc = f"{ramo} · {estado}"[:72] if ramo or estado else None
        rows.append((pid, title, desc))
    if has_more:
        rows.append((_MORE_BUTTON_ID, "Ver más pólizas", f"{n - end} restantes"))
    body = (
        f"Encontré {n} pólizas a tu nombre. Mostrando {start + 1}-{end}."
        if has_more or page > 0
        else f"Encontré {n} pólizas a tu nombre."
    )
    return AIMessage(
        content=body,
        additional_kwargs={
            "interactive": {
                "kind": "list",
                "body": body,
                "button_label": "Elegir póliza",
                "rows": rows,
                "section_title": "Tus pólizas",
            },
            "send_to_client": True,
        },
    )


def _qa_menu_message(numero: str) -> AIMessage:
    """3-button menu after a poliza is locked: saldo / estado / coberturas.

    User can also type freely — buttons are a shortcut, not the only path.
    """
    body = (
        f"Listo, sobre la póliza POL-{numero}. ¿Qué querés saber?"
        " Tocá una opción o escribí tu pregunta."
    )
    return AIMessage(
        content=body,
        additional_kwargs={
            "interactive": {
                "kind": "buttons",
                "body": body,
                "buttons": [
                    ("saldo", "Saldo"),
                    ("estado", "Estado"),
                    ("coberturas", "Coberturas"),
                ],
            },
            "send_to_client": True,
        },
    )


def _retry_or_escalate(doc_retries: int, text: str) -> dict[str, Any]:
    """Return retry state or escalate after exhausting doc_retries (system errors only)."""
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
            return _retry_or_escalate(state.get("doc_retries", 0), text)

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
        status = getattr(getattr(exc, "response", None), "status_code", None)
        body = getattr(getattr(exc, "response", None), "text", "")[:300]
        log.warning("node_identify.error", error_type=type(exc).__name__, status=status, body=body)

        # 404 = documento no existe en SoftSeguros (user error, not system error).
        # Ask to verify — no retry limit, just keep asking until they get it right.
        if status == 404:
            return {
                "node": "awaiting_identification",
                "messages": [
                    AIMessage(
                        content=(
                            "No encontré ningún cliente con ese número de documento "
                            "en el sistema de DPG. 🔍\n\n"
                            "¿Podés verificar que el número esté correcto? "
                            "Puede ser cédula de ciudadanía, NIT o cédula de extranjería."
                        )
                    )
                ],
            }

        # Any other error (5xx, timeout, network) = system issue → escalate after 1 retry
        return _retry_or_escalate(state.get("doc_retries", 0), text)

    n = len(polizas)

    if n == 0:
        return _retry_or_escalate(state.get("doc_retries", 0), text)

    if n == 1:
        p = polizas[0]
        poliza_id = str(p.get("id", p.get("numero_poliza", "")))
        numero = p.get("numero_poliza", poliza_id)
        return {
            "node": "answering_qa",
            "poliza_id": poliza_id,
            "cliente_doc": text,
            "polizas_list": polizas,
            "messages": [_qa_menu_message(numero)],
        }

    # N >= 2 — interactive list (Meta limits 10 rows; we page in chunks of 9).
    return {
        "node": "awaiting_policy_choice",
        "polizas_list": polizas,
        "polizas_page": 0,
        "cliente_doc": text,
        "messages": [_polizas_list_message(polizas, page=0)],
    }


def route_from_identification(state: QAState) -> str:
    """Conditional edge after node_identify.

    Always ends the turn except on escalation. node_identify emits a message
    on every path that requires a user response (T-01, T-02, T-04, the N=1
    confirmation), so we never chain to another node in the same invocation.
    """
    from langgraph.graph import END

    if state.get("node") == "escalating":
        return "escalating"
    return END


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
    """Parse client's policy choice (interactive tap, numeric, or text) and lock poliza_id.

    Resolution order:
    1. ``__more`` button → advance ``polizas_page`` and re-emit the list.
    2. Interactive list tap → ``text`` is the poliza_id we sent in the row id.
    3. Numeric index (1, 2, 3…) for fallback typed input.
    4. POL-XXXX pattern match.
    5. LLM fallback over the allowlist.
    """
    text = _last_human_text(state)
    polizas: list[dict[str, Any]] = state.get("polizas_list", [])

    # 1. "Ver más" pagination
    if text == _MORE_BUTTON_ID:
        next_page = state.get("polizas_page", 0) + 1
        return {
            "node": "awaiting_policy_choice",
            "polizas_page": next_page,
            "messages": [_polizas_list_message(polizas, page=next_page)],
        }

    resolved: dict[str, Any] | None = None

    # 2. Interactive list tap — text == poliza_id (matches row id we sent)
    for p in polizas:
        pid = str(p.get("id", p.get("numero_poliza", "")))
        if pid == text:
            resolved = p
            break

    # 3. Numeric index (typed)
    if resolved is None:
        m = _NUMERIC_RE.match(text)
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= len(polizas):
                resolved = polizas[idx - 1]

    # 4. POL-XXXX pattern
    if resolved is None:
        resolved = _resolve_by_number_pattern(text, polizas)

    # 5. LLM fallback
    if resolved is None:
        resolved = await _resolve_by_llm_fallback(text, polizas)

    if resolved is None:
        # Stay in awaiting_policy_choice — re-prompt with the same list
        page = state.get("polizas_page", 0)
        return {
            "node": "awaiting_policy_choice",
            "messages": [_polizas_list_message(polizas, page=page)],
        }

    poliza_id = str(resolved.get("id", resolved.get("numero_poliza", "")))
    numero = resolved.get("numero_poliza", poliza_id)
    return {
        "node": "answering_qa",
        "poliza_id": poliza_id,
        "messages": [_qa_menu_message(numero)],
    }


def route_from_policy_choice(state: QAState) -> str:
    """Conditional edge after node_choose_policy — always end the turn.

    Whether the choice resolved (poliza locked, confirmation emitted) or
    didn't (re-prompt emitted), the next step needs the user's reply.
    """
    from langgraph.graph import END

    return END


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
    # Layer 1 escape hatch (D-15): webhook sets force_escalate=True when the
    # client's text matches ESCAPE_REGEX (humano/agente/asesor/…). Honor it
    # before any LLM call — zero LLM cost, deterministic path to T_08.
    if state.get("force_escalate"):
        return {
            "node": "escalating",
            "escalation_reason": "escape_hatch",
            "messages": [AIMessage(content=T_08)],
        }

    poliza_id: str | None = state.get("poliza_id")
    conv_id: str | None = state.get("thread_id") or state.get("conversation_id")
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

    # Audit: llm_turn — fire-and-forget, never raises (05-01 guarantee)
    audit_log.emit_task(
        action="llm_turn",
        actor="bot",
        conversation_id=conv_id,
        poliza_id=poliza_id,
        payload={
            "model_role": "conversation",
            "response_sha256": hashlib.sha256(
                str(getattr(result, "content", result)).encode()
            ).hexdigest(),
            "has_tool_calls": bool(getattr(result, "tool_calls", None)),
        },
    )

    # Check for escalate_to_human tool call (escape hatch Layer 2)
    if hasattr(result, "tool_calls") and result.tool_calls:
        for tc in result.tool_calls:
            if tc.get("name") == "escalate_to_human":
                return {
                    "node": "escalating",
                    "escalation_reason": "escape_hatch",
                    "messages": [AIMessage(content=T_08)],
                }

        # Audit: tool_call — comma-joined tool names, never raises (05-01 guarantee)
        audit_log.emit_task(
            action="tool_call",
            actor="bot",
            conversation_id=conv_id,
            poliza_id=poliza_id,
            payload={
                "tools": ",".join(tc.get("name", "") for tc in result.tool_calls),
            },
        )

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

    # Audit: judge_decision — emit before branching on approval (05-01 guarantee)
    if rubric is not None:
        audit_log.emit_task(
            action="judge_decision",
            actor="judge",
            conversation_id=conv_id,
            poliza_id=poliza_id,
            payload={
                "approved": bool(is_approved(rubric)),
                **{
                    f"flag_{k}": bool(v) for k, v in rubric.model_dump().items() if k != "rationale"
                },
            },
        )

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
                additional_kwargs={
                    "send_to_client": True,
                    # Attach quick-reply buttons so the client can keep tapping
                    # through saldo/estado/coberturas without typing. The text
                    # path still works — see _handle_text_message.
                    "interactive": {
                        "kind": "buttons",
                        "body": final_response,
                        "buttons": [
                            ("saldo", "Saldo"),
                            ("estado", "Estado"),
                            ("agente", "Hablar humano"),
                        ],
                    },
                },
            )
        ],
    }


def route_from_answering(state: QAState) -> str:
    """Conditional edge after node_answer.

    Three outcomes:
    - Escalation / close → terminal nodes
    - Judge rejected (retry pending) → self-loop into node_answer
    - Approved (last message is an AIMessage tagged send_to_client) → END

    Only the LAST message matters: on judge-retry, no message is appended so
    the tail is the user's HumanMessage; on approval, a fresh AIMessage with
    send_to_client=True is appended. Walking back past the tail would let a
    previous turn's approved AIMessage end the current turn prematurely.
    """
    from langgraph.graph import END

    node = state.get("node", "answering_qa")
    if node in ("escalating", "closed"):
        return node
    msgs = state.get("messages", [])
    last = msgs[-1] if msgs else None
    if isinstance(last, AIMessage) and last.additional_kwargs.get("send_to_client"):
        return END
    return "answering_qa"


# ---------------------------------------------------------------------------
# node_escalate
# ---------------------------------------------------------------------------


async def node_escalate(state: QAState) -> dict[str, Any]:
    """Log escalation reason. Side effects (Chatwoot, meta) handled by webhook dispatcher."""
    reason = state.get("escalation_reason", "unknown")
    log.info("node_escalate.terminal", reason=reason)

    # Audit: escalation — fire-and-forget, never raises (05-01 guarantee)
    audit_log.emit_task(
        action="escalation",
        actor="bot",
        conversation_id=state.get("thread_id") or state.get("conversation_id"),
        poliza_id=state.get("poliza_id"),
        payload={"reason": str(reason)},
    )

    # Template already set by upstream node — return no-op dict
    return {"node": "escalating"}


# ---------------------------------------------------------------------------
# node_close
# ---------------------------------------------------------------------------


async def node_close(state: QAState) -> dict[str, Any]:
    """Mark conversation closed. Chatwoot.mark_resolved handled by webhook dispatcher."""
    log.info("node_close.terminal")
    return {"node": "closed"}
