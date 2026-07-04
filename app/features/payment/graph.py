"""Payment graph builder — Plan 04-04.

Exposes node-name constants (needed by other plans that import before the
full graph is wired) and ``build_payment_subgraph`` which is called by
``app/features/qa/graph.py::build_qa_graph`` to embed the 5 payment nodes
in the same compiled QA graph (RESEARCH Open Question #1: vertical extension
over a separate subgraph to avoid cross-graph state serialisation complexity).

Node name constants are kept here so that both graph.py and qa/graph.py can
import them without pulling in the full node implementations.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.features.qa.state import QAState

# ---------------------------------------------------------------------------
# Node name constants
# ---------------------------------------------------------------------------

NODE_RECEIVE_COMPROBANTE: str = "node_receive_comprobante"
NODE_FORWARD_TO_CARTERA: str = "node_forward_to_cartera"
NODE_AWAITING_CARTERA: str = "node_awaiting_cartera"
NODE_CONFIRMING: str = "node_confirming"
NODE_PAYMENT_ESCALATE: str = "node_payment_escalate"


# ---------------------------------------------------------------------------
# Routing helper
# ---------------------------------------------------------------------------


def _route_from_awaiting(state: dict) -> str:  # type: ignore[type-arg]
    """Conditional edge after node_awaiting_cartera.

    Routes based on ``payment_status`` that node_awaiting_cartera sets after
    the interrupt() resume:
      - "approved"       → node_confirming
      - "escalated"      → node_payment_escalate
      - "awaiting_cartera" → loop back (Plan 04-05 "info" path re-prompts)
    """
    ps = state.get("payment_status")
    if ps == "approved":
        return NODE_CONFIRMING
    if ps == "escalated":
        return NODE_PAYMENT_ESCALATE
    # "awaiting_cartera" or anything else → loop (Plan 04-05 drives next turn)
    return NODE_AWAITING_CARTERA


def build_payment_subgraph(checkpointer: object | None = None) -> StateGraph:  # type: ignore[type-arg]
    """Return a compiled-ready StateGraph for the payment flow.

    Uses the same ``QAState`` schema as the QA graph (vertical extension
    recommendation from 04-RESEARCH.md Open Question #1).

    The returned object must be compiled::

        graph = build_payment_subgraph(checkpointer=cp).compile(checkpointer=cp)

    In practice, Plan 04-04 integrates the payment nodes *directly into
    the QA graph* via ``build_qa_graph()`` rather than compiling a standalone
    subgraph, so callers that need the full pipeline should use
    ``app.features.qa.graph.build_qa_graph()``.

    This function exists for standalone testing and for Plan 04-05+.

    Args:
        checkpointer: Optional checkpointer passed to ``g.compile()``. When
            not provided the caller must compile with a checkpointer before
            invoking graphs that contain ``interrupt()``.

    Returns:
        A ``StateGraph`` (not yet compiled) or a compiled graph when
        ``checkpointer`` is given — callers chain ``.compile(checkpointer=...)``
        themselves for consistency with the lifespan pattern in ``app/main.py``.
    """
    from app.features.payment.nodes import (
        node_awaiting_cartera,
        node_confirming,
        node_forward_to_cartera,
        node_payment_escalate,
        node_receive_comprobante,
    )

    g: StateGraph = StateGraph(QAState)  # type: ignore[type-arg]

    # Add all 5 payment nodes.
    g.add_node(NODE_RECEIVE_COMPROBANTE, node_receive_comprobante)
    g.add_node(NODE_FORWARD_TO_CARTERA, node_forward_to_cartera)
    g.add_node(NODE_AWAITING_CARTERA, node_awaiting_cartera)
    g.add_node(NODE_CONFIRMING, node_confirming)
    g.add_node(NODE_PAYMENT_ESCALATE, node_payment_escalate)

    # Entry point: always start with receive.
    g.set_entry_point(NODE_RECEIVE_COMPROBANTE)

    # Linear edges: receive → forward → awaiting
    g.add_edge(NODE_RECEIVE_COMPROBANTE, NODE_FORWARD_TO_CARTERA)
    g.add_edge(NODE_FORWARD_TO_CARTERA, NODE_AWAITING_CARTERA)

    # Conditional edge from awaiting (post-interrupt resume).
    g.add_conditional_edges(
        NODE_AWAITING_CARTERA,
        _route_from_awaiting,
        {
            NODE_CONFIRMING: NODE_CONFIRMING,
            NODE_PAYMENT_ESCALATE: NODE_PAYMENT_ESCALATE,
            NODE_AWAITING_CARTERA: NODE_AWAITING_CARTERA,  # info loop
        },
    )

    # Terminal edges.
    g.add_edge(NODE_CONFIRMING, END)
    g.add_edge(NODE_PAYMENT_ESCALATE, END)

    return g


__all__ = [
    "NODE_AWAITING_CARTERA",
    "NODE_CONFIRMING",
    "NODE_FORWARD_TO_CARTERA",
    "NODE_PAYMENT_ESCALATE",
    "NODE_RECEIVE_COMPROBANTE",
    "build_payment_subgraph",
]
