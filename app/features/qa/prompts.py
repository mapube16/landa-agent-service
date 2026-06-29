"""Q&A system prompt builder — implemented in Plan 03-05.

Composes the LLM system prompt from:
1. KB content (``load_kb()`` output wrapped in ``== REFERENCIA ==`` delimiters
   per CLAUDE.md L5 memory spec)
2. L4 debtor flags (``l4_flags`` dict — concise summary, NOT raw transcripts)
3. Current poliza_id if locked (tells LLM which policy the conversation is
   scoped to, reinforcing the state-level lock)

The system prompt is NOT the only line of defense against poliza drift —
``poliza_id`` is locked in graph state and tools receive it via
``InjectedState``. The prompt is complementary guidance only.

Implemented in: Plan 03-05.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger("features.qa.prompts")

__all__ = ["system_prompt"]


def system_prompt(
    kb_content: str,
    poliza_id: str | None = None,
    l4_flags: dict[str, Any] | None = None,
) -> str:
    """Build the system prompt for the conversation LLM.

    Args:
        kb_content: Full text from ``load_kb()`` — will be wrapped in
            ``== REFERENCIA ==`` delimiters.
        poliza_id: Locked poliza PK if identification is complete, else
            ``None`` (prompt omits poliza section).
        l4_flags: Summarised debtor history flags (``promesa_de_pago``,
            ``escalado_previo``, ``intentos``, etc.). If ``None`` or empty,
            prompt omits L4 section.

    Returns:
        Full system prompt string ready for the conversation LLM.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")
