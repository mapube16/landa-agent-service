"""KB loader for DPG knowledge base — implemented in Plan 03-05.

Loads ``knowledge/dpg_cartera.md`` into memory once per process
(``@lru_cache(maxsize=1)``). The KB auditor (``app/security/kb_auditor.py``)
validates the file at startup before this is ever called — if the auditor
fails, the service does not start (D-11, fail-closed).

The raw string is injected into the system prompt via ``system_prompt()``
wrapped in ``== REFERENCIA ==`` / ``== /REFERENCIA ==`` delimiters so the
LLM can distinguish KB content from conversation instructions (CLAUDE.md §L5).

Implemented in: Plan 03-05.
"""

from __future__ import annotations

from functools import lru_cache

import structlog

log = structlog.get_logger("features.qa.knowledge_base")

__all__ = ["load_kb"]


@lru_cache(maxsize=1)
def load_kb() -> str:
    """Return the full text of ``knowledge/dpg_cartera.md``.

    Cached after first call — file is read at startup after kb_auditor gate
    passes. Returns the raw markdown string.

    Implemented in Plan 03-05.
    """
    raise NotImplementedError("Implemented in Plan 03-05")
