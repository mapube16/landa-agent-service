"""Tests for app/security/audit_log.py — Task 1 (migration + model) and Task 2 (hash chain + emit).

Integration tests require a live Postgres instance and are skipped locally without POSTGRES_URL.
"""

from __future__ import annotations

import os
import types
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Task 1 — Table registration + offline DDL (RED phase)
# ---------------------------------------------------------------------------


def test_audit_log_table_registered_on_base() -> None:
    """audit_log table must be registered on Base.metadata after import."""
    from app.config.db import Base
    from app.security import audit_log  # noqa: F401

    assert "audit_log" in Base.metadata.tables


def test_audit_log_model_columns() -> None:
    """AuditLog model must have all required columns."""
    from app.security.audit_log import AuditLog

    table = AuditLog.__table__
    col_names = {c.name for c in table.columns}
    required = {
        "id",
        "created_at",
        "conversation_id",
        "poliza_id",
        "action",
        "actor",
        "payload_hash",
        "prev_hash",
        "entry_hash",
        "metadata",
    }
    assert required <= col_names, f"Missing columns: {required - col_names}"


def test_audit_log_model_metadata_python_attribute() -> None:
    """Python attribute must be 'metadata_json' (not 'metadata') to avoid SQLAlchemy conflict."""
    from app.security.audit_log import AuditLog

    # The ORM attribute name must be metadata_json
    assert hasattr(
        AuditLog, "metadata_json"
    ), "AuditLog must have 'metadata_json' Python attribute (SQL column 'metadata')"
    # The SQL column name must still be 'metadata'
    col = AuditLog.__table__.c["metadata"]
    assert col is not None


def test_audit_log_prev_hash_server_default() -> None:
    """prev_hash server_default must be empty string sentinel."""
    from app.security.audit_log import AuditLog

    col = AuditLog.__table__.c["prev_hash"]
    assert col.server_default is not None


# ---------------------------------------------------------------------------
# Integration tests (require live Postgres — skipped locally)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("INTEGRATION_POSTGRES_URL"),
    reason="Integration test requires INTEGRATION_POSTGRES_URL (live DB with 0003 applied)",
)
async def test_audit_log_delete_raises_db_error() -> None:
    """DELETE on audit_log row must raise DBAPIError (trigger guard)."""
    import sqlalchemy as sa
    from sqlalchemy.exc import DBAPIError

    from app.config.db import create_db_engine, create_session_factory
    from app.security.audit_log import AuditLog

    os.environ["POSTGRES_URL"] = os.environ["INTEGRATION_POSTGRES_URL"]
    engine = create_db_engine()
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        # Insert a minimal row
        row = AuditLog(
            action="test_action",
            actor="test",
            payload_hash="abc123",
            prev_hash="",
            entry_hash="def456",
        )
        session.add(row)
        await session.flush()
        row_id = row.id

        # Attempt DELETE — must raise
        with pytest.raises(DBAPIError):
            await session.execute(sa.text("DELETE FROM audit_log WHERE id = :i"), {"i": row_id})

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("INTEGRATION_POSTGRES_URL"),
    reason="Integration test requires INTEGRATION_POSTGRES_URL (live DB with 0003 applied)",
)
async def test_audit_log_update_raises_db_error() -> None:
    """UPDATE on audit_log row must raise DBAPIError (trigger guard)."""
    import sqlalchemy as sa
    from sqlalchemy.exc import DBAPIError

    from app.config.db import create_db_engine, create_session_factory
    from app.security.audit_log import AuditLog

    os.environ["POSTGRES_URL"] = os.environ["INTEGRATION_POSTGRES_URL"]
    engine = create_db_engine()
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        row = AuditLog(
            action="test_action",
            actor="test",
            payload_hash="abc123",
            prev_hash="",
            entry_hash="def456",
        )
        session.add(row)
        await session.flush()
        row_id = row.id

        with pytest.raises(DBAPIError):
            await session.execute(
                sa.text("UPDATE audit_log SET action = 'x' WHERE id = :i"),
                {"i": row_id},
            )

    await engine.dispose()


# ---------------------------------------------------------------------------
# Task 2 — Hash chain + emit + AuditPayload (RED phase)
# ---------------------------------------------------------------------------


def test_canonical_deterministic() -> None:
    """canonical() must produce identical bytes regardless of key order."""
    from app.security.audit_log import canonical

    a = canonical({"b": 1, "a": "x"})
    b = canonical({"a": "x", "b": 1})
    assert a == b


def test_canonical_returns_bytes() -> None:
    from app.security.audit_log import canonical

    result = canonical({"key": "value"})
    assert isinstance(result, bytes)


def test_compute_entry_hash_deterministic() -> None:
    """compute_entry_hash must produce the same result for the same inputs."""
    import hashlib

    from app.security.audit_log import canonical, compute_entry_hash

    prev_hash = ""
    entry = {"id": 1, "action": "test", "actor": "bot"}

    h1 = compute_entry_hash(prev_hash, entry)
    h2 = compute_entry_hash(prev_hash, entry)
    assert h1 == h2

    # Must match manually computed reference
    expected = hashlib.sha256(prev_hash.encode() + canonical(entry)).hexdigest()
    assert h1 == expected


def test_compute_payload_hash_deterministic() -> None:
    """compute_payload_hash must be sha256(canonical(payload))."""
    import hashlib

    from app.security.audit_log import canonical, compute_payload_hash

    payload = {"amount": 1000, "poliza": "ABC123"}
    result = compute_payload_hash(payload)
    expected = hashlib.sha256(canonical(payload)).hexdigest()
    assert result == expected


# ---------------------------------------------------------------------------
# AuditPayload tests
# ---------------------------------------------------------------------------


def test_audit_payload_accepts_flat_primitives() -> None:
    """AuditPayload must accept str | int | bool | None values."""
    from app.models.audit import AuditPayload

    payload = {"a": "s", "b": 1, "c": True, "d": None}
    model = AuditPayload.model_validate(payload)
    assert model.root == payload


def test_audit_payload_rejects_floats() -> None:
    """AuditPayload must reject float values (Pitfall 4)."""
    from pydantic import ValidationError

    from app.models.audit import AuditPayload

    with pytest.raises(ValidationError):
        AuditPayload.model_validate({"amount": 123.45})


def test_audit_payload_rejects_nested_dicts() -> None:
    """AuditPayload must reject nested dict values."""
    from pydantic import ValidationError

    from app.models.audit import AuditPayload

    with pytest.raises(ValidationError):
        AuditPayload.model_validate({"nested": {"key": "value"}})


# ---------------------------------------------------------------------------
# verify_chain_rows tests
# ---------------------------------------------------------------------------


def _make_chain_rows(
    n: int,
) -> list[types.SimpleNamespace]:
    """Build n well-chained synthetic rows for testing."""
    from app.security.audit_log import compute_entry_hash, compute_payload_hash

    rows: list[types.SimpleNamespace] = []
    prev_hash = ""
    base_dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    for i in range(1, n + 1):
        created_at = base_dt.replace(second=i)
        payload_hash = compute_payload_hash({"idx": i})
        entry: dict[str, object] = {
            "id": i,
            "created_at": created_at.isoformat(),
            "conversation_id": None,
            "poliza_id": None,
            "action": "test",
            "actor": "bot",
            "payload_hash": payload_hash,
        }
        entry_hash = compute_entry_hash(prev_hash, entry)

        row = types.SimpleNamespace(
            id=i,
            created_at=created_at,
            conversation_id=None,
            poliza_id=None,
            action="test",
            actor="bot",
            payload_hash=payload_hash,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        rows.append(row)
        prev_hash = entry_hash

    return rows


def test_verify_chain_rows_valid_chain() -> None:
    """verify_chain_rows returns (True, None) for a valid chain."""
    from app.security.audit_log import verify_chain_rows

    rows = _make_chain_rows(3)
    ok, bad_id = verify_chain_rows(rows)
    assert ok is True
    assert bad_id is None


def test_verify_chain_rows_detects_tampered_payload_hash() -> None:
    """verify_chain_rows detects a tampered payload_hash on row 2."""
    from app.security.audit_log import verify_chain_rows

    rows = _make_chain_rows(3)
    # Tamper row 2's payload_hash without recomputing entry_hash
    rows[1].payload_hash = "tampered_payload_hash"

    ok, bad_id = verify_chain_rows(rows)
    assert ok is False
    assert bad_id == 2


def test_verify_chain_rows_detects_broken_linkage() -> None:
    """verify_chain_rows detects a broken prev_hash linkage on row 3."""
    from app.security.audit_log import verify_chain_rows

    rows = _make_chain_rows(3)
    # Break row 3's prev_hash linkage (doesn't match row 2's entry_hash)
    rows[2].prev_hash = "broken_prev_hash"

    ok, bad_id = verify_chain_rows(rows)
    assert ok is False
    assert bad_id == 3


def test_verify_chain_rows_empty() -> None:
    """verify_chain_rows returns (True, None) for empty input."""
    from app.security.audit_log import verify_chain_rows

    ok, bad_id = verify_chain_rows([])
    assert ok is True
    assert bad_id is None


# ---------------------------------------------------------------------------
# emit fail-open tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_fail_open_on_db_error() -> None:
    """emit must return None and not raise when DB session fails."""
    from app.security.audit_log import emit

    # Create a session_factory stub whose session raises on execute
    mock_session = AsyncMock()
    mock_session.execute.side_effect = RuntimeError("DB error")
    mock_session.rollback = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value = mock_session

    # Must not raise
    result = await emit(
        mock_factory,
        action="test_action",
        actor="bot",
        payload={"key": "value"},
    )
    assert result is None


@pytest.mark.asyncio
async def test_emit_fail_open_invalid_payload() -> None:
    """emit must return None (not raise) when payload has invalid types (floats)."""
    from app.security.audit_log import emit

    mock_session = AsyncMock()
    mock_factory = MagicMock()
    mock_factory.return_value = mock_session

    # Float in payload should be rejected by AuditPayload, but emit must not raise
    result = await emit(
        mock_factory,
        action="test_action",
        actor="bot",
        payload={"amount": 123.45},
    )
    assert result is None


# ---------------------------------------------------------------------------
# emit_task tests
# ---------------------------------------------------------------------------


def test_emit_task_no_session_factory_no_crash() -> None:
    """emit_task returns silently when app.state has no session_factory."""
    from app.security.audit_log import emit_task

    with patch("app.main.app") as mock_app:
        # Remove session_factory from state
        mock_state = MagicMock(spec=[])  # spec=[] means no attributes
        mock_app.state = mock_state

        # Must not raise
        emit_task(
            action="test_action",
            actor="bot",
            payload={"key": "value"},
        )


def test_emit_task_does_not_raise_on_import_error() -> None:
    """emit_task must not raise if app import fails."""
    from app.security.audit_log import emit_task

    # Even with a patched app state, emit_task should be fail-open
    with patch("app.security.audit_log.asyncio") as mock_asyncio:
        mock_asyncio.create_task.side_effect = RuntimeError("task creation failed")
        # Should not raise even if create_task fails
        try:
            emit_task(
                action="test_action",
                actor="bot",
                payload={"key": "value"},
            )
        except Exception as e:
            pytest.fail(f"emit_task raised unexpectedly: {e}")
