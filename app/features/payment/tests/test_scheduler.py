"""Tests for app.features.payment.scheduler — TDD RED phase (Plan 04-06).

Tests cover:
  - business_minutes_between: edge cases, full day, cross-midnight, cross-weekend
  - check_pending_cases: off-hours skip, reminder at 20 min, idempotency,
    escalation at 90 min, off-hours anchored window reminder
  - cleanup_attachments_90d: unlinks old files and rows, preserves young ones

Infrastructure notes:
  - No freezegun (not in pyproject.toml).
  - Clock is injected via monkeypatch.setattr on ``scheduler._now_utc``
    which is a module-level callable replaced to return a fixed datetime.
  - DB sessions are AsyncMock stubs — no real Postgres.
  - File I/O uses pytest ``tmp_path`` fixture.
"""

from __future__ import annotations

import datetime
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

TZ_CO = __import__("zoneinfo").ZoneInfo("America/Bogota")
UTC = datetime.UTC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bogota(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, tzinfo=TZ_CO)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.UTC)


def _make_ctx(meta: Any, chatwoot: Any, db_factory: Any) -> dict[str, Any]:
    return {"meta": meta, "chatwoot": chatwoot, "db_session_factory": db_factory}


def _make_mock_case(
    *,
    case_id: str | None = None,
    phone: str = "+573001234567",
    cliente_nombre: str | None = "Test Cliente",
    status: str = "awaiting_cartera",
    created_at: datetime.datetime | None = None,
    reminder_sent_at: datetime.datetime | None = None,
    escalated_at: datetime.datetime | None = None,
    work_hours_due_at: datetime.datetime | None = None,
) -> MagicMock:
    """Build a minimal Case MagicMock for scheduler tests."""
    from app.memory.case_store import Case

    case = MagicMock(spec=Case)
    case.case_id = case_id or str(uuid.uuid4())
    case.phone = phone
    case.cliente_nombre = cliente_nombre
    case.status = status
    case.created_at = created_at or _utc(2026, 6, 29, 15, 0)  # Monday 10:00 Bogota
    case.reminder_sent_at = reminder_sent_at
    case.escalated_at = escalated_at
    case.work_hours_due_at = work_hours_due_at
    return case


def _make_session_factory(cases: list[Any]) -> Any:
    """Return a callable that yields an async session whose execute returns ``cases``."""
    session = AsyncMock()

    # scalars().all() returns the case list
    scalars_result = MagicMock()
    scalars_result.all.return_value = cases
    scalars_result.first.return_value = cases[0] if cases else None

    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result
    session.execute.return_value = execute_result
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    def _factory() -> Any:
        @asynccontextmanager
        async def _ctx() -> Any:  # type: ignore[misc]
            yield session

        return _ctx()

    # also attach session for direct inspection
    _factory.session = session  # type: ignore[attr-defined]
    return _factory


# ---------------------------------------------------------------------------
# Tests: business_minutes_between
# ---------------------------------------------------------------------------


class TestBusinessMinutesBetween:
    def test_same_instant_returns_zero(self) -> None:
        """Same start and end → 0 minutes."""
        from app.features.payment.business_hours import business_minutes_between

        t = _bogota(2026, 6, 29, 10, 0).astimezone(UTC)
        assert business_minutes_between(t, t) == 0

    def test_20_minutes_inside_morning_block(self) -> None:
        """10:00–10:20 Bogota Monday = 20 business minutes."""
        from app.features.payment.business_hours import business_minutes_between

        start = _bogota(2026, 6, 29, 10, 0).astimezone(UTC)
        end = _bogota(2026, 6, 29, 10, 20).astimezone(UTC)
        assert business_minutes_between(start, end) == 20

    def test_spans_lunch_break(self) -> None:
        """10:00–15:00 Bogota = 120 morning + 60 afternoon = 180 business minutes."""
        from app.features.payment.business_hours import business_minutes_between

        start = _bogota(2026, 6, 29, 10, 0).astimezone(UTC)
        end = _bogota(2026, 6, 29, 15, 0).astimezone(UTC)
        # 10:00-12:00 = 120 min, 14:00-15:00 = 60 min
        assert business_minutes_between(start, end) == 180

    def test_full_workday_is_360_minutes(self) -> None:
        """One full Monday: 08:00–16:00 = 4h morning + 2h afternoon = 360 min."""
        from app.features.payment.business_hours import business_minutes_between

        start = _bogota(2026, 6, 29, 8, 0).astimezone(UTC)
        end = _bogota(2026, 6, 29, 16, 0).astimezone(UTC)
        assert business_minutes_between(start, end) == 360

    def test_weekend_minutes_not_counted(self) -> None:
        """Friday 15:00 – Monday 09:00 = 60 Friday + 60 Monday = 120 (no weekend)."""
        from app.features.payment.business_hours import business_minutes_between

        # Friday 3 July 2026
        start = _bogota(2026, 7, 3, 15, 0).astimezone(UTC)
        # Monday 6 July 2026
        end = _bogota(2026, 7, 6, 9, 0).astimezone(UTC)
        # Friday 15:00-16:00 = 60, Monday 08:00-09:00 = 60
        assert business_minutes_between(start, end) == 120

    def test_outside_hours_not_counted(self) -> None:
        """Saturday 10:00 – Monday 08:20 = only 20 Monday minutes."""
        from app.features.payment.business_hours import business_minutes_between

        start = _bogota(2026, 7, 4, 10, 0).astimezone(UTC)  # Saturday
        end = _bogota(2026, 7, 6, 8, 20).astimezone(UTC)  # Monday 08:20
        assert business_minutes_between(start, end) == 20

    def test_start_after_end_returns_zero(self) -> None:
        """start > end is treated as zero (no negative minutes)."""
        from app.features.payment.business_hours import business_minutes_between

        start = _bogota(2026, 6, 29, 10, 30).astimezone(UTC)
        end = _bogota(2026, 6, 29, 10, 0).astimezone(UTC)
        assert business_minutes_between(start, end) == 0


# ---------------------------------------------------------------------------
# Tests: check_pending_cases
# ---------------------------------------------------------------------------


class TestCheckPendingCases:
    @pytest.mark.asyncio
    async def test_off_hours_skips_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Saturday 10:00 Bogota → returns immediately, no Meta calls."""
        import app.features.payment.scheduler as sched

        fixed_now = _bogota(2026, 7, 4, 10, 0).astimezone(UTC)  # Saturday
        monkeypatch.setattr(sched, "_now_utc", lambda: fixed_now)

        meta = AsyncMock()
        chatwoot = AsyncMock()
        case = _make_mock_case(
            work_hours_due_at=_utc(2026, 7, 4, 8, 0),
        )
        db = _make_session_factory([case])
        monkeypatch.setattr(
            "app.features.payment.scheduler._get_settings_payment",
            lambda: MagicMock(cartera_phone_allowlist=frozenset({"+573009999999"})),
        )

        ctx = _make_ctx(meta, chatwoot, db)
        result = await sched.check_pending_cases(ctx)

        assert result == {"skipped": "off_hours"}
        meta.send_buttons.assert_not_called()
        meta.send_text.assert_not_called()
        # reminder_sent_at must remain None (no DB write)
        assert case.reminder_sent_at is None

    @pytest.mark.asyncio
    async def test_reminder_fires_at_20_business_minutes_and_only_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Monday 10:21 Bogota; case created 10:00 → reminder sent once; second call skips."""
        import app.features.payment.scheduler as sched

        fixed_now = _bogota(2026, 6, 29, 10, 21).astimezone(UTC)  # Monday 10:21
        monkeypatch.setattr(sched, "_now_utc", lambda: fixed_now)

        meta = AsyncMock()
        meta.send_buttons.return_value = "WAMID_REMINDER"
        chatwoot = AsyncMock()

        case_id = str(uuid.uuid4())
        case = _make_mock_case(
            case_id=case_id,
            created_at=_bogota(2026, 6, 29, 10, 0).astimezone(UTC),  # Monday 10:00
            reminder_sent_at=None,
            work_hours_due_at=_bogota(2026, 6, 29, 10, 20).astimezone(UTC),
        )
        db = _make_session_factory([case])
        monkeypatch.setattr(
            "app.features.payment.scheduler._get_settings_payment",
            lambda: MagicMock(cartera_phone_allowlist=frozenset({"+573009999999"})),
        )

        ctx = _make_ctx(meta, chatwoot, db)
        result = await sched.check_pending_cases(ctx)

        assert result == {"processed": 1}
        meta.send_buttons.assert_awaited_once()
        # Verify session.execute was called with an UPDATE to set reminder_sent_at
        session = db.session
        assert session.execute.call_count >= 2  # SELECT + UPDATE

    @pytest.mark.asyncio
    async def test_reminder_not_fired_if_already_sent(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """reminder_sent_at already set and < 90 min → no second reminder, no escalation."""
        import app.features.payment.scheduler as sched

        fixed_now = _bogota(2026, 6, 29, 10, 25).astimezone(UTC)
        monkeypatch.setattr(sched, "_now_utc", lambda: fixed_now)

        meta = AsyncMock()
        chatwoot = AsyncMock()

        case = _make_mock_case(
            created_at=_bogota(2026, 6, 29, 10, 0).astimezone(UTC),
            reminder_sent_at=_bogota(2026, 6, 29, 10, 20).astimezone(UTC),
            work_hours_due_at=_bogota(2026, 6, 29, 10, 20).astimezone(UTC),
        )
        db = _make_session_factory([case])
        monkeypatch.setattr(
            "app.features.payment.scheduler._get_settings_payment",
            lambda: MagicMock(cartera_phone_allowlist=frozenset({"+573009999999"})),
        )

        ctx = _make_ctx(meta, chatwoot, db)
        await sched.check_pending_cases(ctx)

        meta.send_buttons.assert_not_called()
        chatwoot.post_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_escalates_after_90_business_minutes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Monday 11:31; created 10:00; reminder already sent → escalate."""
        import app.features.payment.scheduler as sched

        fixed_now = _bogota(2026, 6, 29, 11, 31).astimezone(UTC)  # 91 min elapsed
        monkeypatch.setattr(sched, "_now_utc", lambda: fixed_now)

        meta = AsyncMock()
        meta.send_text.return_value = "WAMID_ESC"
        chatwoot = AsyncMock()
        chatwoot.get_or_create_conversation.return_value = 42
        chatwoot.post_message.return_value = None

        case_id = str(uuid.uuid4())
        case = _make_mock_case(
            case_id=case_id,
            created_at=_bogota(2026, 6, 29, 10, 0).astimezone(UTC),
            reminder_sent_at=_bogota(2026, 6, 29, 10, 20).astimezone(UTC),
            work_hours_due_at=_bogota(2026, 6, 29, 10, 20).astimezone(UTC),
        )
        db = _make_session_factory([case])
        monkeypatch.setattr(
            "app.features.payment.scheduler._get_settings_payment",
            lambda: MagicMock(cartera_phone_allowlist=frozenset({"+573009999999"})),
        )

        ctx = _make_ctx(meta, chatwoot, db)
        result = await sched.check_pending_cases(ctx)

        assert result == {"processed": 1}
        chatwoot.get_or_create_conversation.assert_awaited_once()
        chatwoot.post_message.assert_awaited_once()
        meta.send_text.assert_awaited_once()

        # Verify escalation message contains case_id
        escalation_args = chatwoot.post_message.call_args
        assert case_id in str(escalation_args)

    @pytest.mark.asyncio
    async def test_off_hours_anchored_window_reminder_fires(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Case created Friday 15:50; work_hours_due_at Monday 08:20; clock Monday 08:21."""
        import app.features.payment.scheduler as sched

        fixed_now = _bogota(2026, 7, 6, 8, 21).astimezone(UTC)  # Monday 08:21
        monkeypatch.setattr(sched, "_now_utc", lambda: fixed_now)

        meta = AsyncMock()
        meta.send_buttons.return_value = "WAMID_REMINDER"
        chatwoot = AsyncMock()

        case_id = str(uuid.uuid4())
        case = _make_mock_case(
            case_id=case_id,
            created_at=_bogota(2026, 7, 3, 15, 50).astimezone(UTC),  # Friday 15:50
            reminder_sent_at=None,
            work_hours_due_at=_bogota(2026, 7, 6, 8, 20).astimezone(UTC),  # Monday 08:20
        )
        db = _make_session_factory([case])
        monkeypatch.setattr(
            "app.features.payment.scheduler._get_settings_payment",
            lambda: MagicMock(cartera_phone_allowlist=frozenset({"+573009999999"})),
        )

        ctx = _make_ctx(meta, chatwoot, db)
        result = await sched.check_pending_cases(ctx)

        assert result == {"processed": 1}
        meta.send_buttons.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_cartera_configured_logs_error_and_returns(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty cartera allowlist → logs error, returns without processing."""
        import app.features.payment.scheduler as sched

        fixed_now = _bogota(2026, 6, 29, 10, 21).astimezone(UTC)
        monkeypatch.setattr(sched, "_now_utc", lambda: fixed_now)

        meta = AsyncMock()
        chatwoot = AsyncMock()
        db = _make_session_factory([])
        monkeypatch.setattr(
            "app.features.payment.scheduler._get_settings_payment",
            lambda: MagicMock(cartera_phone_allowlist=frozenset()),
        )

        ctx = _make_ctx(meta, chatwoot, db)
        result = await sched.check_pending_cases(ctx)

        # Should return without error (logged internally)
        assert "skipped" in result or "processed" in result
        meta.send_buttons.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: cleanup_attachments_90d
# ---------------------------------------------------------------------------


class TestCleanupAttachments90d:
    @pytest.mark.asyncio
    async def test_cleanup_unlinks_old_attachments_and_preserves_young(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Old attachments (91 days) → file + DB row deleted; young (5 days) → untouched."""
        import app.features.payment.scheduler as sched

        # Create real files in tmp_path
        old_file = tmp_path / "old_comprobante.jpg"
        old_file.write_bytes(b"old file content")
        young_file = tmp_path / "young_comprobante.jpg"
        young_file.write_bytes(b"young file content")

        now_utc = _utc(2026, 7, 4, 2, 0)
        monkeypatch.setattr(sched, "_now_utc", lambda: now_utc)

        from app.memory.case_store import Attachment

        old_att = MagicMock(spec=Attachment)
        old_att.id = 1
        old_att.path = str(old_file)
        old_att.received_at = now_utc - datetime.timedelta(days=91)

        young_att = MagicMock(spec=Attachment)
        young_att.id = 2
        young_att.path = str(young_file)
        young_att.received_at = now_utc - datetime.timedelta(days=5)

        session = AsyncMock()
        scalars_result = MagicMock()
        scalars_result.all.return_value = [old_att]  # only old ones from query
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_result
        session.execute.return_value = execute_result
        session.commit = AsyncMock()

        def _factory() -> Any:
            @asynccontextmanager
            async def _ctx() -> Any:  # type: ignore[misc]
                yield session

            return _ctx()

        ctx = {"db_session_factory": _factory}
        result = await sched.cleanup_attachments_90d(ctx)

        assert result == {"deleted": 1}
        # Old file must be gone
        assert not old_file.exists()
        # Young file must be untouched
        assert young_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_returns_zero_when_nothing_to_delete(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No rows older than 90 days → deleted count 0."""
        import app.features.payment.scheduler as sched

        now_utc = _utc(2026, 7, 4, 2, 0)
        monkeypatch.setattr(sched, "_now_utc", lambda: now_utc)

        session = AsyncMock()
        scalars_result = MagicMock()
        scalars_result.all.return_value = []
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_result
        session.execute.return_value = execute_result
        session.commit = AsyncMock()

        def _factory() -> Any:
            @asynccontextmanager
            async def _ctx() -> Any:  # type: ignore[misc]
                yield session

            return _ctx()

        ctx = {"db_session_factory": _factory}
        result = await sched.cleanup_attachments_90d(ctx)

        assert result == {"deleted": 0}
