"""Memory layer — L3 case storage + L4 debtor flags (Phase 4).

Import ``case_store`` as a module or import ``Case``/``Attachment`` directly:

    from app.memory import case_store
    from app.memory import Case, Attachment
    from app.memory.case_store import Case, Attachment
"""

from __future__ import annotations

from app.memory import case_store
from app.memory.case_store import Attachment, Case

__all__ = [
    "Attachment",
    "Case",
    "case_store",
]
