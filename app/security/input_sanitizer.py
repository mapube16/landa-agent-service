"""Input sanitizer — thin re-export fulfilling the CLAUDE.md structure contract.

CLAUDE.md declares ``app/security/input_sanitizer.py`` as a module in the
security chain-of-responsibility pipeline (RESEARCH Open Question 4).
``prompt_firewall.py`` already implements the full 5-step sanitization pipeline;
this module re-exports ``sanitize`` under the name ``sanitize_input`` to satisfy
the structure contract without duplicating logic.

Usage::

    from app.security.input_sanitizer import sanitize_input

    result = sanitize_input(raw_text)
    if result.blocked:
        # ... send template T-05/T-06, do NOT forward to LLM
"""

from __future__ import annotations

from app.security.prompt_firewall import sanitize as sanitize_input

__all__ = ["sanitize_input"]
