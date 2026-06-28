"""ARQ worker entrypoint.

Phase 1 ships a stub with a single no-op job so ``Dockerfile.worker`` has
a valid ``CMD`` target (``arq app.worker.WorkerSettings``) that actually
boots — ARQ refuses to start with an empty ``functions`` list
(``RuntimeError: at least one function or cron_job must be registered``).
Real jobs (WhatsApp send, SoftSeguros refresh, escalation cleanup) wire
in from Phase 2 onward and the ``_noop`` placeholder gets dropped then.
"""

from __future__ import annotations

from typing import Any


async def _noop(ctx: dict[str, Any]) -> None:
    """Placeholder so ARQ has at least one registered function.

    Removed in Phase 2 when real WhatsApp / SoftSeguros jobs land.
    """
    return None


class WorkerSettings:
    """ARQ worker configuration.

    Populated incrementally per ROADMAP:
      - F2: WhatsApp send jobs, SoftSeguros cache warmers
      - F3: KB auditor schedules
      - F5: Audit log fan-out
    """

    functions: list[Any] = [_noop]
    # ``redis_settings`` is read lazily by ARQ from REDIS_URL when not set
    # here; settings module already injects REDIS_URL into the process env.
