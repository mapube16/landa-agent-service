"""LLM factory re-exports — the canonical import path for the ROADMAP deliverable.

ROADMAP F1 deliverable 10 specifies ``app/config/llm.py`` as the home of the
``get_llm(role)`` factory. The actual implementation lives in
``app/integrations/openrouter.py`` to honour the vertical-slice convention
(CLAUDE.md: "Cuando aparezca una integración nueva, va en
``integrations/<nombre>.py``"). This module re-exports the public surface so
both import paths resolve to the same callable:

    from app.config.llm import get_llm          # ROADMAP-promised path
    from app.integrations.openrouter import get_llm  # vertical-slice path

Tests assert that both bindings are the *same* function object, so the
``@lru_cache`` warm-reuse semantics survive regardless of which import path
feature code chooses.
"""

from __future__ import annotations

from app.integrations.openrouter import ROLE_MODEL_MAP, LLMRole, get_llm

__all__ = ["LLMRole", "ROLE_MODEL_MAP", "get_llm"]
