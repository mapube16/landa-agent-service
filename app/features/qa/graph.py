"""Q&A LangGraph builder — implemented in Plan 03-05.

Builds the 5-node StateGraph (D-04) that drives the Q&A feature:
``awaiting_identification`` -> ``awaiting_policy_choice`` (conditional, skip
if N=1) -> ``answering_qa`` -> ``escalating`` (terminal) -> ``closed``
(terminal).

Call ``build_qa_graph().compile(checkpointer=...)`` in ``app/main.py``
lifespan to get the runnable graph.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

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

__all__ = ["build_qa_graph"]


def build_qa_graph() -> StateGraph:  # type: ignore[type-arg]
    """Return a compiled-ready ``StateGraph`` for the Q&A feature.

    Call ``build_qa_graph().compile(checkpointer=...)`` in ``app/main.py``
    lifespan to get the runnable graph.
    """
    builder: StateGraph = StateGraph(QAState)  # type: ignore[type-arg]

    # 5 nodes (D-04)
    builder.add_node("awaiting_identification", node_identify)
    builder.add_node("awaiting_policy_choice", node_choose_policy)
    builder.add_node("answering_qa", node_answer)
    builder.add_node("escalating", node_escalate)
    builder.add_node("closed", node_close)

    builder.set_entry_point("awaiting_identification")

    # Conditional edges — routes driven by state.node set by each node fn
    builder.add_conditional_edges(
        "awaiting_identification",
        route_from_identification,
        {
            "awaiting_policy_choice": "awaiting_policy_choice",
            "answering_qa": "answering_qa",
            "escalating": "escalating",
            "awaiting_identification": "awaiting_identification",
        },
    )
    builder.add_conditional_edges(
        "awaiting_policy_choice",
        route_from_policy_choice,
        {
            "answering_qa": "answering_qa",
            "awaiting_policy_choice": "awaiting_policy_choice",
        },
    )
    builder.add_conditional_edges(
        "answering_qa",
        route_from_answering,
        {
            "answering_qa": "answering_qa",
            "escalating": "escalating",
            "closed": "closed",
        },
    )

    # Terminal nodes -> END
    builder.add_edge("escalating", END)
    builder.add_edge("closed", END)

    return builder
