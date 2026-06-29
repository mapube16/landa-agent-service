"""KB auditor — 5-layer pipeline to validate ``knowledge/dpg_cartera.md``.

Runs at 3 points (D-11):
1. **CI on PR** — GitHub Action when ``knowledge/dpg_cartera.md`` changes.
   Fails the PR if ``risk_score > 50``.
2. **Pre-deploy gate** — before Railway redeploy trigger (documented in
   runbook).
3. **Startup FAIL-CLOSED** — ``app/main.py`` lifespan calls ``audit_kb``
   before loading KB into the system prompt. If ``risk_score > 50``,
   raises ``RuntimeError`` and the service does NOT start.

The 5 layers (D-10):
1. Hash check — skip if KB hash unchanged since last audit (Redis or file).
2. Static patterns — regex against injection/exfiltration catalog (hidden
   chars, ignore-previous, role-override, data exfiltration patterns).
   Note: hidden-char code points are listed by hex (U+200B, U+202E, U+202D)
   — the actual chars are never stored in source code, only in adversarial
   test fixtures under ``tests/fixtures/kb_adversarial/``.
3. Diff extraction — only audits the delta vs previous version.
4. LLM judge — ``get_llm("judge").with_structured_output(KBAuditRubric)``.
5. Risk scoring — combines signals → 0-100 score. Thresholds: >50 blocks,
   20-50 flags (Sentry warning), <20 passes silently.

Implemented in: Plan 03-04.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

log = structlog.get_logger("security.kb_auditor")

__all__ = ["KBAuditRubric", "audit_kb"]


class KBAuditRubric(BaseModel):
    """LLM judge rubric for KB content audit (Layer 4 of 5, D-10).

    6 boolean flags + rationale + numeric risk contribution.
    Used by the LLM judge call in ``audit_kb``; combined with static
    signal scores in Layer 5.
    """

    contains_injection_patterns: bool
    contains_role_override: bool
    contains_exfiltration_patterns: bool
    contains_pii: bool
    contains_external_links: bool
    content_on_topic: bool
    rationale: str
    risk_score: int  # 0-100; final combined score after Layer 5


async def audit_kb(kb_path: str, redis: Any) -> int:
    """Run the 5-layer KB audit pipeline against ``kb_path``.

    Args:
        kb_path: Absolute path to the KB markdown file (typically
            ``knowledge/dpg_cartera.md``).
        redis: Redis client instance for hash-check cache (Layer 1).
            Pass ``None`` in CI where Redis is unavailable — Layer 1
            is skipped and all subsequent layers run unconditionally.

    Returns:
        Final ``risk_score`` (0-100). Caller must raise if ``> 50``.

    Raises:
        ``FileNotFoundError`` if ``kb_path`` does not exist.

    Implemented in Plan 03-04.
    """
    raise NotImplementedError("Implemented in Plan 03-04")
