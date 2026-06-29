"""LLM-as-judge for outbound message validation (Phase 3, D-05/D-06/D-07).

Every outbound message from the conversation LLM passes through
``judge_response`` before being sent to the client. ``JudgeRubric`` defines
the 8-flag schema (D-05) — ALL flags must be ``True`` for the message to be
approved. One ``False`` flag → rejection → 1 retry with guidance
(``rationale`` injected) → second rejection → escalate (D-06).

Model: ``get_llm("judge")`` = Gemini 2.5 Flash, temp=0 (D-07).
Uses ``with_structured_output(JudgeRubric)`` — verified feasible in 03-00
probe Task 3 (strict JSON schema, OpenRouter → Gemini Flash, all 8 booleans
must be non-optional for ``strict=true`` mode).

``affirms_payment_without_cartera_approval`` is always ``False`` in F3
(no payment flow yet). Schema includes it now so F4 can reuse without
schema migration (D-05 rationale in 03-CONTEXT.md).

Implemented in: Plan 03-04.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

log = structlog.get_logger("security.judge")

__all__ = ["JudgeRubric", "is_approved", "judge_response"]


class JudgeRubric(BaseModel):
    """8-flag approval rubric for outbound LLM messages (D-05, locked schema).

    ALL boolean flags must be ``True`` for ``is_approved`` to return ``True``.
    ``rationale`` is a Spanish debug string explaining the evaluation
    (logged to LangSmith + structlog; never sent to the client).
    """

    is_in_scope: bool
    leaks_other_polizas: bool
    affirms_payment_without_cartera_approval: bool
    factually_grounded: bool
    no_jailbreak_echo: bool
    no_pii_leak: bool
    no_external_links: bool
    sentiment_appropriate: bool
    rationale: str


def is_approved(rubric: JudgeRubric) -> bool:
    """Return ``True`` iff all 8 boolean flags in ``rubric`` are ``True``.

    Convention (D-05): every flag being ``True`` means "the message is safe
    / correct along this dimension". A single ``False`` → rejection.

    Implemented in Plan 03-04.
    """
    raise NotImplementedError("Implemented in Plan 03-04")


async def judge_response(
    messages: list[Any],
    response: str,
) -> JudgeRubric | None:
    """Evaluate ``response`` against the 8-flag rubric via LLM judge.

    Args:
        messages: The conversation history (``list[BaseMessage]``) providing
            context for the judge.
        response: The outbound message candidate to evaluate.

    Returns:
        ``JudgeRubric`` with evaluation results, or ``None`` if the judge
        call fails (caller must treat ``None`` as rejection and escalate).

    Implemented in Plan 03-04.
    """
    raise NotImplementedError("Implemented in Plan 03-04")
