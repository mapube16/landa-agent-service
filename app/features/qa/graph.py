"""Q&A LangGraph builder — implemented in Plan 03-05.

Builds the 5-node StateGraph (D-04) that drives the Q&A feature:
``awaiting_identification`` -> ``awaiting_policy_choice`` (conditional, skip
if N=1) -> ``answering_qa`` -> ``escalating`` (terminal) -> ``closed``
(terminal).

Call ``build_qa_graph().compile(checkpointer=...)`` in ``app/main.py``
lifespan to get the runnable graph.
"""

from __future__ import annotations

from typing import Any

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


def _route_entry(state: dict[str, Any]) -> str:
    """Pick entry node based on persisted state — avoid re-running node_identify
    after the user is already identified."""
    if state.get("poliza_id"):
        return "answering_qa"
    if state.get("polizas_list") and state.get("node") == "awaiting_policy_choice":
        return "awaiting_policy_choice"
    return "awaiting_identification"


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

    builder.set_conditional_entry_point(
        _route_entry,
        {
            "awaiting_identification": "awaiting_identification",
            "awaiting_policy_choice": "awaiting_policy_choice",
            "answering_qa": "answering_qa",
        },
    )

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
            "answering_qa": "answering_qa",
            "escalating": "escalating",
            "closed": "closed",
        },
    )

    # Terminal nodes -> END
    builder.add_edge("escalating", END)
    builder.add_edge("closed", END)

    return builder
