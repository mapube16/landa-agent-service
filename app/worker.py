"""ARQ worker entrypoint.

Phase 1 ships a stub so ``Dockerfile.worker`` has a valid ``CMD`` target
(``arq app.worker.WorkerSettings``). Real jobs (WhatsApp send, SoftSeguros
refresh, escalation cleanup) wire in from Phase 2 onward.
"""

from __future__ import annotations

from typing import Any


class WorkerSettings:
    """ARQ worker configuration.

    Populated incrementally per ROADMAP:
      - F2: WhatsApp send jobs, SoftSeguros cache warmers
      - F3: KB auditor schedules
      - F5: Audit log fan-out
    """

    functions: list[Any] = []
    # `redis_settings` is read lazily by ARQ from REDIS_URL when not set
    # here; we'll wire it through app.config.settings in plan 01-03.
