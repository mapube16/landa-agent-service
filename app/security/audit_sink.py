"""Incremental NDJSON secondary sink for the audit log (SEC-03, Plan 05-05).

Exports new audit rows to append-only NDJSON files partitioned by date on the
Railway volume (default mount: /data/comprobantes/audit). The sink is secondary
to the Postgres hash chain; its purpose is offline re-verification and backup.

Design:
- Incremental: only rows with id > last exported id (tracked in .cursor file).
- Append-only: files opened with mode "ab" so no existing line is overwritten.
- Date-partitioned: one file per UTC calendar date (YYYY-MM-DD.ndjson).
- Sync file I/O is intentional here: this runs in an ARQ cron job (batch,
  not hot path), the export volume is small (daily delta), and the Railway
  volume I/O is local-disk (not network), so asyncio overhead would exceed
  the actual I/O cost. aiofiles is not a project dependency.
- Fail-open: any exception is logged and the function returns 0 -- the worker
  must never crash because the volume is absent or full.

Exports: export_audit_ndjson
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.security.audit_log import AuditLog

log = structlog.get_logger(__name__)

_CURSOR_FILE = ".cursor"


def _read_cursor(sink_dir: Path) -> int:
    """Read the last exported id from .cursor; return 0 if absent or invalid."""
    cursor_path = sink_dir / _CURSOR_FILE
    try:
        return int(cursor_path.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return 0


def _write_cursor(sink_dir: Path, last_id: int) -> None:
    """Write last exported id to .cursor (overwrites previous value)."""
    (sink_dir / _CURSOR_FILE).write_text(str(last_id))


async def export_audit_ndjson(
    session_factory: async_sessionmaker[AsyncSession],
    sink_dir: Path,
) -> int:
    """Export new audit rows as NDJSON lines to date-partitioned files.

    Args:
        session_factory: SQLAlchemy async sessionmaker (from app.state).
        sink_dir: Root directory for NDJSON files (Railway volume mount point).

    Returns:
        Number of rows exported. Returns 0 on any error (fail-open).
    """
    try:
        # Ensure sink_dir exists; will fail below if it cannot be created.
        sink_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

        cursor = _read_cursor(sink_dir)

        async with session_factory() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.id > cursor).order_by(AuditLog.id.asc())
            )
            rows = result.scalars().all()

        if not rows:
            return 0

        # Group rows by UTC calendar date for file partitioning.
        from collections import defaultdict
        from datetime import UTC, datetime

        by_date: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            dt: datetime = row.created_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            date_key = dt.strftime("%Y-%m-%d")
            line: dict[str, Any] = {
                "id": row.id,
                "created_at": dt.isoformat(),
                "conversation_id": row.conversation_id,
                "poliza_id": row.poliza_id,
                "action": row.action,
                "actor": row.actor,
                "payload_hash": row.payload_hash,
                "prev_hash": row.prev_hash,
                "entry_hash": row.entry_hash,
            }
            by_date[date_key].append(line)

        # Append lines to date-partitioned files (binary append, never truncate).
        for date_key, lines in by_date.items():
            file_path = sink_dir / f"{date_key}.ndjson"
            with open(file_path, "ab") as fh:  # noqa: ASYNC230
                for line in lines:
                    fh.write(orjson.dumps(line, option=orjson.OPT_SORT_KEYS) + b"\n")

        max_id: int = rows[-1].id
        _write_cursor(sink_dir, max_id)
        return len(rows)

    except Exception as exc:
        log.error(
            "audit_sink.write_failed",
            error_type=type(exc).__name__,
            sink_dir=str(sink_dir),
        )
        return 0


__all__ = ["export_audit_ndjson"]
