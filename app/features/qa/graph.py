"""Q&A LangGraph builder — implemented in Plan 03-05.

Builds the 5-node StateGraph (D-04) that drives the Q&A feature:
``awaiting_identification`` → ``awaiting_policy_choice`` (conditional, skip
if N=1) → ``answering_qa`` → ``escalating`` (terminal) → ``closed``
(terminal).

Conditional edges from ANY node → ``escalating`` fire when:
- SoftSeguros circuit breaker opens (``pybreaker.CircuitBreakerError``)
- LLM-as-judge rejects twice (``judge_retries > 1``)
- Prompt firewall blocks the input
- Document lookup exhausted (``doc_retries > 1``)
- Escape hatch fired (regex Layer 1 or LLM tool Layer 2)
- Client attempts poliza change mid-conversation after lock

Implemented in: Plan 03-05.
"""

from __future__ import annotations

from langgraph.graph import StateGraph

__all__ = ["build_qa_graph"]


def build_qa_graph() -> StateGraph:  # type: ignore[type-arg]
    """Return a compiled-ready ``StateGraph`` for the Q&A feature.

    Call ``build_qa_graph().compile(checkpointer=...)`` in ``app/main.py``
    lifespan to get the runnable graph.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")
