"""Q&A + Payment LangGraph builder.

Phase 3 (Plan 03-05): 5-node Q&A graph (D-04).
Phase 4 (Plan 04-04): 5 payment nodes integrated into the same compiled graph
  (RESEARCH Open Question #1 — vertical extension over a separate subgraph
  avoids cross-graph state serialisation complexity).

Entry router (``_route_entry``) dispatches to the payment flow when
``state["payment_status"]`` is one of the active payment statuses, or when
``state["_inbound_media"]`` is present (fresh comprobante arriving from the
ARQ job).

Call ``build_qa_graph().compile(checkpointer=...)`` in ``app/main.py``
lifespan to get the runnable graph.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.features.payment.graph import (
    NODE_AWAITING_CARTERA,
    NODE_CONFIRMING,
    NODE_FORWARD_TO_CARTERA,
    NODE_PAYMENT_ESCALATE,
    NODE_RECEIVE_COMPROBANTE,
    _route_from_awaiting,
)
from app.features.payment.nodes import (
    node_awaiting_cartera,
    node_confirming,
    node_forward_to_cartera,
    node_payment_escalate,
    node_receive_comprobante,
)
from app.features.qa.nodes import (
    node_answer,
    node_choose_policy,
    node_close,
    node_escalate,
    node_identify,
    route_from_answering,
    route_from_identification,
    route_from_policy_choice,
)
from app.features.qa.state import QAState

# Payment statuses that indicate an active or ongoing payment flow.
_PAYMENT_ACTIVE_STATUSES = frozenset({"awaiting_receipt", "forwarded", "awaiting_cartera"})


def _route_entry(state: dict[str, Any]) -> str:
    """Pick entry node based on persisted state.

    Payment flow takes priority: if ``payment_status`` is in an active payment
    state, OR an ``_inbound_media`` payload is present (fresh comprobante from
    ARQ ``process_attachment``), dispatch to the payment receive node.

    Otherwise fall through to the Q&A identification / policy-choice /
    answering flow.
    """
    payment_status = state.get("payment_status")

    # Active payment flow — dispatch to payment receive
    if payment_status in _PAYMENT_ACTIVE_STATUSES or state.get("_inbound_media"):
        return NODE_RECEIVE_COMPROBANTE

    if state.get("poliza_id"):
        return "answering_qa"
    if state.get("polizas_list") and state.get("node") == "awaiting_policy_choice":
        return "awaiting_policy_choice"
    return "awaiting_identification"


__all__ = ["build_qa_graph"]


def build_qa_graph() -> StateGraph:  # type: ignore[type-arg]
    """Return a compiled-ready ``StateGraph`` for the Q&A + Payment features.

    Call ``build_qa_graph().compile(checkpointer=...)`` in ``app/main.py``
    lifespan to get the runnable graph.
    """
    builder: StateGraph = StateGraph(QAState)  # type: ignore[type-arg]

    # ── Q&A nodes (Phase 3) ──────────────────────────────────────────────────
    builder.add_node("awaiting_identification", node_identify)
    builder.add_node("awaiting_policy_choice", node_choose_policy)
    builder.add_node("answering_qa", node_answer)
    builder.add_node("escalating", node_escalate)
    builder.add_node("closed", node_close)

    # ── Payment nodes (Phase 4, Plan 04-04) ─────────────────────────────────
    builder.add_node(NODE_RECEIVE_COMPROBANTE, node_receive_comprobante)
    builder.add_node(NODE_FORWARD_TO_CARTERA, node_forward_to_cartera)
    builder.add_node(NODE_AWAITING_CARTERA, node_awaiting_cartera)
    builder.add_node(NODE_CONFIRMING, node_confirming)
    builder.add_node(NODE_PAYMENT_ESCALATE, node_payment_escalate)

    # ── Conditional entry ────────────────────────────────────────────────────
    builder.set_conditional_entry_point(
        _route_entry,
        {
            # Q&A destinations
            "awaiting_identification": "awaiting_identification",
            "awaiting_policy_choice": "awaiting_policy_choice",
            "answering_qa": "answering_qa",
            # Payment destinations
            NODE_RECEIVE_COMPROBANTE: NODE_RECEIVE_COMPROBANTE,
        },
    )

    # ── Q&A edges ────────────────────────────────────────────────────────────
    # ponytail: every non-terminal node emits a message that needs the user's
    # next reply, so all routes end the turn — no intra-invocation chaining.
    builder.add_conditional_edges(
        "awaiting_identification",
        route_from_identification,
        {"escalating": "escalating", END: END},
    )
    builder.add_conditional_edges(
        "awaiting_policy_choice",
        route_from_policy_choice,
        {END: END},
    )
    builder.add_conditional_edges(
        "answering_qa",
        route_from_answering,
        {
            "answering_qa": "answering_qa",  # judge retry self-loop
            "escalating": "escalating",
            "closed": "closed",
            END: END,  # judge approved → wait for next user turn
        },
    )

    # Terminal Q&A nodes → END
    builder.add_edge("escalating", END)
    builder.add_edge("closed", END)

    # ── Payment edges ────────────────────────────────────────────────────────
    # Linear: receive → forward → awaiting
    builder.add_edge(NODE_RECEIVE_COMPROBANTE, NODE_FORWARD_TO_CARTERA)
    builder.add_edge(NODE_FORWARD_TO_CARTERA, NODE_AWAITING_CARTERA)

    # Conditional post-interrupt: approved/escalated/info-loop
    builder.add_conditional_edges(
        NODE_AWAITING_CARTERA,
        _route_from_awaiting,
        {
            NODE_CONFIRMING: NODE_CONFIRMING,
            NODE_PAYMENT_ESCALATE: NODE_PAYMENT_ESCALATE,
            NODE_AWAITING_CARTERA: NODE_AWAITING_CARTERA,  # info loop
        },
    )

    # Terminal payment nodes → END
    builder.add_edge(NODE_CONFIRMING, END)
    builder.add_edge(NODE_PAYMENT_ESCALATE, END)

    return builder
