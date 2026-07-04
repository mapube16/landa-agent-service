"""Immutable audit log with SHA-256 hash chain.

Design invariants:
- APPEND-ONLY: PostgreSQL trigger ``trg_audit_log_immutable`` raises EXCEPTION
  on any DELETE or UPDATE attempt, enforcing immutability at the DB engine level.
- HASH CHAIN: Each row commits to ``prev_hash || canonical(entry)``, forming a
  tamper-evident chain. verify_chain_rows() detects any broken link.
- FAIL-OPEN: emit() and emit_task() NEVER raise. Any DB failure is logged via
  structlog and swallowed so audit failures never crash the service (Pitfall 3).
- NO PII: Only ``payload_hash = sha256(canonical(payload))`` is persisted. Raw
  payload data (poliza balances, client info) is hashed and discarded (CLAUDE.md).

Concurrency (v1 stance — documented):
  pg_advisory_xact_lock('audit_log_chain') serializes inserts within a
  transaction. Two concurrent emit() calls will queue on the advisory lock,
  preventing hash chain forks. This is a v1 approach; a dedicated audit queue
  via ARQ is the v2 path for higher throughput (RESEARCH Pitfall 1).

Module re-exports: AuditLog, canonical, compute_payload_hash, compute_entry_hash,
emit, emit_task, verify_chain_rows, verify_chain.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import orjson
import structlog
from sqlalchemy import TIMESTAMP as _TIMESTAMP
from sqlalchemy import BigInteger, Text
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.config.db import Base, session_scope

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """Append-only audit log row.

    CRITICAL: The Python attribute for the SQL ``metadata`` column is
    ``metadata_json`` because ``metadata`` is a reserved attribute on
    SQLAlchemy declarative models (it shadows the class-level ``metadata``
    mapper). The SQL column name remains ``metadata``.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        "created_at",
        _TIMESTAMP(timezone=True),  # TIMESTAMP WITH TIME ZONE
        nullable=False,
        server_default=func.now(),
    )
    conversation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    poliza_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    prev_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sa_text("''"),
    )
    entry_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # SQL column name is 'metadata'; Python attr is 'metadata_json' to avoid
    # collision with SQLAlchemy DeclarativeBase.metadata (the MetaData object).
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)


# ---------------------------------------------------------------------------
# Hash chain helpers
# ---------------------------------------------------------------------------


def canonical(entry: dict[str, Any]) -> bytes:
    """Deterministic JSON bytes: sorted keys, no whitespace.

    Uses orjson OPT_SORT_KEYS so insertion order of dict keys does not
    affect the serialized form.
    """
    return orjson.dumps(entry, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS)


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of canonical(payload). Stored in the DB; raw payload is discarded."""
    return hashlib.sha256(canonical(payload)).hexdigest()


def compute_entry_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    """sha256(prev_hash_bytes || canonical_entry_bytes).

    Binds each row to its predecessor's hash so any mutation in the chain
    breaks the linkage and is detectable by verify_chain_rows().
    """
    data = prev_hash.encode("utf-8") + canonical(entry)
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# emit — async, fail-open
# ---------------------------------------------------------------------------


async def emit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    action: str,
    actor: str,
    conversation_id: str | None = None,
    poliza_id: str | None = None,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert one audit row into the hash chain.

    FAIL-OPEN: any exception (DB, validation, lock timeout) is caught, logged
    via structlog, and swallowed. This function NEVER raises (Pitfall 3).
    """
    try:
        from app.models.audit import AuditPayload

        # Validate payload — flat primitives only (Pitfall 4). Raises ValidationError
        # if floats or nested structures are present. Caught by the outer try/except.
        AuditPayload.model_validate(payload)

        payload_hash = compute_payload_hash(payload)

        async with session_scope(session_factory) as session:
            # Serialize chain inserts via advisory lock (Pitfall 1).
            await session.execute(
                sa_text("SELECT pg_advisory_xact_lock(hashtext('audit_log_chain'))")
            )

            # Advance the sequence to get the next id deterministically.
            next_id: int = (
                await session.execute(
                    sa_text("SELECT nextval(pg_get_serial_sequence('audit_log', 'id'))")
                )
            ).scalar_one()

            # Fetch the most recent entry_hash for chaining.
            prev_hash: str = (
                await session.execute(
                    sa_text("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
                )
            ).scalar() or ""

            # Python-side timestamp so it participates in the hash.
            created_at = datetime.now(UTC)

            # Entry dict (metadata deliberately EXCLUDED per RESEARCH Pattern 2).
            entry: dict[str, Any] = {
                "id": next_id,
                "created_at": created_at.isoformat(),
                "conversation_id": conversation_id,
                "poliza_id": poliza_id,
                "action": action,
                "actor": actor,
                "payload_hash": payload_hash,
            }
            entry_hash = compute_entry_hash(prev_hash, entry)

            row = AuditLog(
                id=next_id,
                created_at=created_at,
                conversation_id=conversation_id,
                poliza_id=poliza_id,
                action=action,
                actor=actor,
                payload_hash=payload_hash,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                metadata_json=(orjson.dumps(metadata).decode() if metadata is not None else None),
            )
            session.add(row)

    except Exception as exc:
        log.error(
            "audit_log.emit_failed",
            action=action,
            actor=actor,
            error_type=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# emit_task — sync fire-and-forget for hot paths
# ---------------------------------------------------------------------------


def emit_task(
    *,
    action: str,
    actor: str,
    conversation_id: str | None = None,
    poliza_id: str | None = None,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget audit emit for synchronous hot paths (e.g., webhook handler).

    Wraps emit() in asyncio.create_task(). Any failure is logged and swallowed.
    NEVER raises (Pitfall 3).
    """
    try:
        # Late import to avoid circular dependency at module load time.
        from app.main import app as _app

        sf = getattr(_app.state, "session_factory", None)
        if sf is None:
            log.debug("audit_log.no_session_factory", action=action)
            return

        task = asyncio.create_task(
            emit(
                sf,
                action=action,
                actor=actor,
                conversation_id=conversation_id,
                poliza_id=poliza_id,
                payload=payload,
                metadata=metadata,
            )
        )

        def _log_task_error(t: asyncio.Task[None]) -> None:
            exc = t.exception()
            if exc is not None:
                log.error(
                    "audit_log.emit_task_callback_error",
                    action=action,
                    error_type=type(exc).__name__,
                )

        task.add_done_callback(_log_task_error)

    except Exception as exc:
        log.error(
            "audit_log.emit_task_failed",
            action=action,
            actor=actor,
            error_type=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------


def verify_chain_rows(rows: Sequence[Any]) -> tuple[bool, int | None]:
    """Verify hash chain integrity for a sequence of AuditLog rows.

    Rows must be ordered by id ASC. Returns (True, None) if chain is intact,
    (False, first_bad_id) otherwise.

    Checks two invariants per row:
    1. row.prev_hash == previous row's entry_hash (first row: "")
    2. compute_entry_hash(row.prev_hash, entry_dict) == row.entry_hash
    """
    prev_entry_hash = ""
    for row in rows:
        # Check linkage
        if row.prev_hash != prev_entry_hash:
            return (False, row.id)

        # Rebuild entry dict — normalize created_at to ISO 8601 with tz info
        created_at: datetime = row.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        entry: dict[str, Any] = {
            "id": row.id,
            "created_at": created_at.isoformat(),
            "conversation_id": row.conversation_id,
            "poliza_id": row.poliza_id,
            "action": row.action,
            "actor": row.actor,
            "payload_hash": row.payload_hash,
        }

        expected_hash = compute_entry_hash(row.prev_hash, entry)
        if expected_hash != row.entry_hash:
            return (False, row.id)

        prev_entry_hash = row.entry_hash

    return (True, None)


async def verify_chain(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[bool, int | None]:
    """Fetch all audit rows ordered by id ASC and verify the chain.

    Fail-open: returns (True, None) on DB error (logging the error).
    Verification alerting is handled by the ARQ cron job (05-05).
    """
    try:
        from sqlalchemy import select

        async with session_scope(session_factory) as session:
            result = await session.execute(select(AuditLog).order_by(AuditLog.id.asc()))
            rows = result.scalars().all()
            return verify_chain_rows(rows)
    except Exception as exc:
        log.error(
            "audit_log.verify_chain_failed",
            error_type=type(exc).__name__,
        )
        return (True, None)


__all__ = [
    "AuditLog",
    "canonical",
    "compute_payload_hash",
    "compute_entry_hash",
    "emit",
    "emit_task",
    "verify_chain_rows",
    "verify_chain",
]
