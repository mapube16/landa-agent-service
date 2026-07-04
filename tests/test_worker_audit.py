"""Tests for worker audit machinery (Plan 05-05).

Covers:
- process_attachment emits attachment_received audit event before graph work
- verify_audit_chain cron: ok path logs info, tamper path logs error
- verify_audit_chain fail-open: no session_factory -> logs and returns
- export_audit_ndjson: writes 2 date-partitioned files, 3 lines total, cursor
- export_audit_ndjson: second run with same rows returns 0 (idempotent)
- export_audit_ndjson: unwritable dir -> returns 0 without raising
- sink_audit_log: sink_enabled=False -> export never called
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    id: int,
    created_at: datetime,
    action: str = "test_action",
    actor: str = "test_actor",
    conversation_id: str | None = "conv-1",
    poliza_id: str | None = None,
    payload_hash: str = "ph",
    prev_hash: str = "",
    entry_hash: str = "eh",
) -> MagicMock:
    """Return a MagicMock shaped like an AuditLog ORM row."""
    row = MagicMock()
    row.id = id
    row.created_at = created_at
    row.action = action
    row.actor = actor
    row.conversation_id = conversation_id
    row.poliza_id = poliza_id
    row.payload_hash = payload_hash
    row.prev_hash = prev_hash
    row.entry_hash = entry_hash
    return row


# ---------------------------------------------------------------------------
# Task 1 — process_attachment emits attachment_received
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_attachment_emits_audit_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_attachment calls emit_task with action=attachment_received before graph."""
    from app.worker import process_attachment

    emitted: list[dict[str, Any]] = []

    def _fake_emit_task(**kwargs: Any) -> None:
        emitted.append(kwargs)

    monkeypatch.setattr("app.security.audit_log.emit_task", _fake_emit_task)

    # Stub graph with async no-ops (fast path via ctx["qa_graph"])
    stub_graph = MagicMock()
    stub_graph.aupdate_state = AsyncMock(return_value=None)
    stub_graph.ainvoke = AsyncMock(return_value=None)

    ctx: dict[str, Any] = {"qa_graph": stub_graph}

    await process_attachment(
        ctx,
        phone="+15555550100",
        media_id="mid-abc",
        mime_type="image/jpeg",
        wamid="wamid.test001",
    )

    assert len(emitted) == 1, "emit_task should have been called exactly once"
    call = emitted[0]
    assert call["action"] == "attachment_received"
    assert call["actor"] == "worker"
    assert call["conversation_id"] == "+15555550100"
    payload = call["payload"]
    assert payload["media_id"] == "mid-abc"
    assert payload["mime_type"] == "image/jpeg"
    assert payload["wamid"] == "wamid.test001"
    # All payload values must be strings (no floats, no nested objects)
    for v in payload.values():
        assert isinstance(v, str), f"payload value {v!r} must be str"


# ---------------------------------------------------------------------------
# Task 1 — verify_audit_chain cron
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_audit_chain_ok_logs_info(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """verify_audit_chain with chain ok -> log.info('audit_log.chain_verified')."""
    from app.worker import verify_audit_chain

    # Wire a fake session_factory via app.state
    fake_sf = MagicMock()
    fake_app_state = MagicMock()
    fake_app_state.session_factory = fake_sf

    monkeypatch.setattr("app.main.app.state", fake_app_state)

    # Patch verify_chain to return ok
    monkeypatch.setattr(
        "app.security.audit_log.verify_chain",
        AsyncMock(return_value=(True, None)),
    )

    log_events: list[dict[str, Any]] = []

    import structlog

    def _capture(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        if event_dict.get("event") == "audit_log.chain_verified":
            log_events.append({"level": method, **event_dict})
        return event_dict

    with structlog.testing.capture_logs() as cap:
        await verify_audit_chain({})

    verified = [e for e in cap if e.get("event") == "audit_log.chain_verified"]
    assert verified, "Expected audit_log.chain_verified log event"
    assert verified[0]["log_level"] == "info"


@pytest.mark.asyncio
async def test_verify_audit_chain_tampered_logs_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_audit_chain with chain tampered -> log.error('audit_log.chain_tampered', first_bad_id=42)."""
    from app.worker import verify_audit_chain

    fake_sf = MagicMock()
    fake_app_state = MagicMock()
    fake_app_state.session_factory = fake_sf
    monkeypatch.setattr("app.main.app.state", fake_app_state)

    monkeypatch.setattr(
        "app.security.audit_log.verify_chain",
        AsyncMock(return_value=(False, 42)),
    )

    import structlog

    with structlog.testing.capture_logs() as cap:
        await verify_audit_chain({})

    tampered = [e for e in cap if e.get("event") == "audit_log.chain_tampered"]
    assert tampered, "Expected audit_log.chain_tampered log event"
    assert tampered[0]["log_level"] == "error"
    assert tampered[0]["first_bad_id"] == 42


@pytest.mark.asyncio
async def test_verify_audit_chain_no_session_factory_logs_and_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_audit_chain when session_factory is None -> log warning + return, no exception."""
    from app.worker import verify_audit_chain

    fake_app_state = MagicMock()
    fake_app_state.session_factory = None
    monkeypatch.setattr("app.main.app.state", fake_app_state)

    import structlog

    with structlog.testing.capture_logs() as cap:
        # Must not raise
        await verify_audit_chain({})

    # Should log a warning about missing session_factory
    warns = [e for e in cap if "session_factory" in str(e.get("event", ""))]
    assert warns, "Expected a warning log about missing session_factory"


# ---------------------------------------------------------------------------
# Task 2 — export_audit_ndjson
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_audit_ndjson_writes_partitioned_files(
    tmp_path: Path,
) -> None:
    """3 rows across 2 dates -> 2 NDJSON files, 3 lines total, cursor = max id."""
    from app.security.audit_sink import export_audit_ndjson

    d1 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    d2 = datetime(2026, 7, 2, 15, 0, 0, tzinfo=UTC)

    rows = [
        _make_row(1, d1, payload_hash="ph1", entry_hash="eh1"),
        _make_row(2, d1, payload_hash="ph2", prev_hash="eh1", entry_hash="eh2"),
        _make_row(3, d2, payload_hash="ph3", prev_hash="eh2", entry_hash="eh3"),
    ]

    # Stub session factory: returns an async context manager whose session
    # returns rows when execute().scalars().all() is called.
    session_mock = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = rows
    session_mock.execute = AsyncMock(return_value=result_mock)

    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=None)

    session_factory = MagicMock(return_value=session_ctx)

    count = await export_audit_ndjson(session_factory, tmp_path)

    assert count == 3

    # Two date-partitioned files
    file_d1 = tmp_path / "2026-07-01.ndjson"
    file_d2 = tmp_path / "2026-07-02.ndjson"
    assert file_d1.exists(), "Expected 2026-07-01.ndjson"
    assert file_d2.exists(), "Expected 2026-07-02.ndjson"

    lines_d1 = [json.loads(l) for l in file_d1.read_text().splitlines() if l]
    lines_d2 = [json.loads(l) for l in file_d2.read_text().splitlines() if l]
    assert len(lines_d1) == 2
    assert len(lines_d2) == 1

    # Cursor file contains max id
    cursor_file = tmp_path / ".cursor"
    assert cursor_file.exists()
    assert cursor_file.read_text().strip() == "3"


@pytest.mark.asyncio
async def test_export_audit_ndjson_idempotent_second_run(
    tmp_path: Path,
) -> None:
    """Second run with same rows (all id <= cursor) -> returns 0, files unchanged."""
    from app.security.audit_sink import export_audit_ndjson

    d1 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    rows = [_make_row(1, d1), _make_row(2, d1)]

    def _make_session_factory(return_rows: list[Any]) -> MagicMock:
        session_mock = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = return_rows
        session_mock.execute = AsyncMock(return_value=result_mock)
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
        session_ctx.__aexit__ = AsyncMock(return_value=None)
        sf = MagicMock(return_value=session_ctx)
        return sf

    # First run
    count1 = await export_audit_ndjson(_make_session_factory(rows), tmp_path)
    assert count1 == 2

    file_d1 = tmp_path / "2026-07-01.ndjson"
    original_content = file_d1.read_bytes()

    # Second run — no new rows (empty result from DB query with cursor filter)
    count2 = await export_audit_ndjson(_make_session_factory([]), tmp_path)
    assert count2 == 0, "Second run should export 0 rows"
    assert file_d1.read_bytes() == original_content, "File must not be modified on second run"


@pytest.mark.asyncio
async def test_export_audit_ndjson_unwritable_dir_returns_zero() -> None:
    """Unwritable sink_dir -> logs audit_sink.write_failed, returns 0, no exception."""
    from app.security.audit_sink import export_audit_ndjson

    # Use a path that is a FILE (not a dir) to trigger a write error
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False) as f:
        bad_path = Path(f.name)

    # bad_path exists as a file; export_audit_ndjson tries to mkdir or write inside it
    session_mock = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=None)
    sf = MagicMock(return_value=session_ctx)

    import structlog

    with structlog.testing.capture_logs() as cap:
        result = await export_audit_ndjson(sf, bad_path)

    assert result == 0
    failed = [e for e in cap if e.get("event") == "audit_sink.write_failed"]
    assert failed, "Expected audit_sink.write_failed log"

    bad_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Task 2 — sink_audit_log cron
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_audit_log_disabled_skips_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sink_enabled=False -> export_audit_ndjson is never called."""
    from app.worker import sink_audit_log

    exported: list[int] = []

    async def _fake_export(sf: Any, sink_dir: Any) -> int:
        exported.append(1)
        return 0

    monkeypatch.setattr("app.security.audit_sink.export_audit_ndjson", _fake_export)

    # Patch settings.audit.sink_enabled = False
    from app.config.settings import settings

    original = settings.audit.sink_enabled
    try:
        object.__setattr__(settings.audit, "sink_enabled", False)
        await sink_audit_log({})
    finally:
        object.__setattr__(settings.audit, "sink_enabled", original)

    assert exported == [], "export_audit_ndjson must not be called when sink_enabled=False"
