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

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class QAState(TypedDict, total=False):
    """Shared state for the 5-node Q&A LangGraph (D-04, 03-CONTEXT.md).

    Extended in Phase 4 (04-01) with payment-flow fields. Both Q&A and
    payment flows share this single TypedDict and the same Postgres
    checkpointer (one thread per phone, RESEARCH §LangGraph interrupt).

    Fields:
        messages: conversation turn history with ``add_messages`` reducer.
        poliza_id: locked poliza PK once client has chosen; ``None`` until then.
            The LLM NEVER mutates this field — only ``node_identify`` and
            ``node_choose_policy`` set it (CLAUDE.md "Lock poliza_id in state").
        cliente_doc: raw document string provided by the client (echo only,
            never passed to LLM after identification succeeds).
        polizas_list: transient list of poliza dicts fetched by ``node_identify``
            for the ``awaiting_policy_choice`` disambiguation display.
        doc_retries: count of failed document lookups (max=1 per D-03). When
            this reaches 1, ``node_identify`` transitions to ``escalating``.
        judge_retries: count of LLM-as-judge rejections (max=1 per D-06). When
            this reaches 1, ``node_answer`` transitions to ``escalating``.
        node: current node name used by conditional edges.
        escalation_reason: why escalation was triggered (for logging/template
            selection). One of 'doc_exhausted', 'breaker', 'judge_rejected',
            'escape_hatch'.
        last_rejection_rationale: judge rationale from last rejection (injected
            into system prompt on retry per D-06).
        force_escalate: set by webhook handler when Layer 1 regex escape hatch
            fires (D-15) — node_answer checks this flag first.
        wa_phone: WhatsApp phone number (E.164) for outbound dispatch. Set by
            webhook handler when dispatching the graph.

        -- Phase 4 payment fields (04-01-PLAN.md) --
        case_id: UUID v4 string referencing db.cases.case_id. Set when the
            first comprobante is received; ``None`` in Q&A-only conversations.
        attachment_count: total number of comprobante files received in this
            submission batch (set once batch is fully received).
        attachment_idx: 1-based index of the current comprobante being forwarded
            to cartera (used to build the "[idx/total]" caption per D-08).
        payment_status: current status of the payment flow, mirroring
            db.cases.status. ``None`` when no payment flow is active.
        cartera_message_wamid: wamid of the last message sent to cartera
            (used to correlate cartera button-tap replies).
        payment_approved: True iff cartera tapped "aprobar"; controls output
            firewall (D-28). The "pago confirmado" text may ONLY appear in
            outbound messages when this flag is True.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    poliza_id: str | None
    cliente_doc: str | None
    polizas_list: list[dict[str, Any]]
    doc_retries: int
    judge_retries: int
    node: Literal[
        # Q&A graph nodes (Phase 3)
        "awaiting_identification",
        "awaiting_policy_choice",
        "answering_qa",
        "escalating",
        "closed",
        # Payment graph nodes (Phase 4, Plan 04-04 populates the implementations)
        "node_receive_comprobante",
        "node_forward_to_cartera",
        "node_awaiting_cartera",
        "node_confirming",
        "node_payment_escalate",
    ]
    escalation_reason: str | None
    last_rejection_rationale: str | None
    force_escalate: bool
    wa_phone: str
    asked_for_doc: bool  # True after T-01 emitted; prevents SoftSeguros call on greeting
    polizas_page: int  # current page (0-based) when N>10, advanced by "__more" button
    # --- Phase 4 payment fields ---
    case_id: NotRequired[str | None]
    attachment_count: NotRequired[int]
    attachment_idx: NotRequired[int]
    payment_status: NotRequired[
        Literal[
            "none",
            "awaiting_receipt",
            "forwarded",
            "awaiting_cartera",
            "approved",
            "rejected",
            "escalated",
        ]
        | None
    ]
    cartera_message_wamid: NotRequired[str | None]
    payment_approved: NotRequired[bool]


__all__ = ["QAState"]
