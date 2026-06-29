"""QAState TypedDict — the shared state shape for the Q&A LangGraph (Phase 3).

All nodes and tools in ``app/features/qa/`` read and write this TypedDict.
``messages`` uses LangGraph's ``add_messages`` reducer so appends are safe
across concurrent node executions. All other fields are last-write-wins.

``node`` is a Literal enum of the 5 canonical graph nodes (D-04 from
03-CONTEXT.md). It is NEVER mutated by the LLM — only node functions and
conditional edges set it.

Implemented by: Plan 03-05 (graph.py + nodes.py compile and run QAState).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class QAState(TypedDict):
    """Shared state for the 5-node Q&A LangGraph (D-04, 03-CONTEXT.md).

    Fields:
        messages: conversation turn history with ``add_messages`` reducer.
        poliza_id: locked poliza PK once client has chosen; ``None`` until then.
            The LLM NEVER mutates this field — only ``node_identify`` and
            ``node_choose_policy`` set it (CLAUDE.md "Lock poliza_id in state").
        cliente_doc: raw document string provided by the client (echo only,
            never passed to LLM after identification succeeds).
        polizas_list: transient list of poliza dicts fetched by ``node_identify``
            for the ``awaiting_policy_choice`` disambiguation display. Cleared
            after poliza is chosen.
        doc_retries: count of failed document lookups (max=1 per D-03). When
            this reaches 1, ``node_identify`` transitions to ``escalating``.
        judge_retries: count of LLM-as-judge rejections (max=1 per D-06). When
            this reaches 1, ``node_answer`` transitions to ``escalating``.
        node: current node name used by conditional edges and for Chatwoot
            mirror metadata. Value is always one of the 5 literal strings.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    poliza_id: str | None
    cliente_doc: str | None
    polizas_list: list[dict[str, Any]]
    doc_retries: int
    judge_retries: int
    node: Literal[
        "awaiting_identification",
        "awaiting_policy_choice",
        "answering_qa",
        "escalating",
        "closed",
    ]


__all__ = ["QAState"]
