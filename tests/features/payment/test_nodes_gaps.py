"""RED tests for Phase 04 gap closure — GAP 1 (deferred forward) + GAP 3 (send_media error).

GAP 1: node_forward_to_cartera off-hours path must set case.status='awaiting_cartera'
       so check_pending_cases (which queries that status) picks up deferred cases.
       Also, check_pending_cases must call forward_case_to_cartera (media forward) for
       cases that have status='awaiting_cartera' AND cartera_message_wamid is None/empty.

GAP 3: The upload/send loop in node_forward_to_cartera (extracted to forward_case_to_cartera)
       must catch exceptions from meta.send_media, escalate via Chatwoot, set
       case.status='escalated', and return {"payment_status": "escalated"}.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ──────────────────────────────────────────────────────────────────────────────

CASE_ID = "aabbccdd-0000-0000-0000-000000000001"
CARTERA_PHONE = "+573999000001"
CLIENT_PHONE = "+15555550100"


def _make_case(
    *,
    case_id: str = CASE_ID,
    phone: str = CLIENT_PHONE,
    status: str = "forwarded",
    cartera_message_wamid: str | None = None,
    work_hours_due_at: datetime | None = None,
    reminder_sent_at: datetime | None = None,
    escalated_at: datetime | None = None,
    cliente_nombre: str = "Test Client",
    poliza_id: str = "POL-001",
    attachments: list[Any] | None = None,
) -> MagicMock:
    case = MagicMock()
    case.case_id = case_id
    case.phone = phone
    case.status = status
    case.cartera_message_wamid = cartera_message_wamid
    case.work_hours_due_at = work_hours_due_at
    case.reminder_sent_at = reminder_sent_at
    case.escalated_at = escalated_at
    case.cliente_nombre = cliente_nombre
    case.poliza_id = poliza_id
    case.cliente_doc = None
    case.created_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    case.attachments = attachments or []
    return case


def _make_attachment(mime_type: str = "image/jpeg", path: str = "/data/att.jpg") -> MagicMock:
    att = MagicMock()
    att.mime_type = mime_type
    att.path = path
    return att


def _make_session_ctx_factory(case: Any) -> Any:
    """Return a callable that produces an asynccontextmanager yielding a session stub."""

    class _FakeResult:
        def __init__(self, obj: Any) -> None:
            self._obj = obj

        def scalars(self) -> _FakeResult:
            return self

        def first(self) -> Any:
            return self._obj

    class _FakeSession:
        def __init__(self) -> None:
            self.flushed = False

        async def execute(self, _stmt: Any) -> _FakeResult:
            return _FakeResult(case)

        async def flush(self) -> None:
            self.flushed = True

    @asynccontextmanager
    async def _factory() -> Any:
        yield _FakeSession()

    return _factory


def _make_payment_settings_mock(cartera_phones: frozenset[str]) -> MagicMock:
    """Build a minimal mock of settings.payment with a controlled allowlist."""
    m = MagicMock()
    m.cartera_phone_allowlist = cartera_phones
    return m


# ──────────────────────────────────────────────────────────────────────────────
# GAP 1 — off-hours path sets status='awaiting_cartera'
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_off_hours_forward_sets_awaiting_cartera_status() -> None:
    """GAP 1a: node_forward_to_cartera off-hours must set case.status='awaiting_cartera'.

    Before the fix, the off-hours path only set work_hours_due_at; status remained
    'forwarded'. check_pending_cases queries status='awaiting_cartera', so deferred
    cases were never picked up.
    """
    from app.features.payment import nodes as nodes_mod

    case = _make_case(status="forwarded")
    factory = _make_session_ctx_factory(case)

    meta_mock = MagicMock()
    meta_mock.send_text = AsyncMock(return_value="wamid.ack")

    # Freeze time to an off-hours moment (Sunday).
    off_hours = datetime(2026, 6, 28, 3, 0, 0, tzinfo=UTC)  # Sunday 03:00 UTC

    state = {
        "wa_phone": CLIENT_PHONE,
        "case_id": CASE_ID,
        "poliza_id": "POL-001",
    }

    with (
        patch.object(nodes_mod, "_get_meta", return_value=meta_mock),
        patch.object(nodes_mod, "_session_factory_fn", factory),
        patch.object(nodes_mod, "is_business_time", return_value=False),
        patch.object(
            nodes_mod,
            "next_business_window_after",
            return_value=off_hours + timedelta(hours=8),
        ),
    ):
        # Patch settings.payment on the imported settings object inside nodes
        with patch("app.features.payment.nodes.datetime") as dt_mock:
            dt_mock.now.return_value = off_hours
            # We need to make settings.payment.cartera_phone_allowlist available but
            # for the off-hours path the cartera check is never reached; we only need
            # the case-status assertion. Still patch to be safe.
            from app.config import settings as settings_module

            orig_raw = settings_module.settings.payment.cartera_phone_allowlist_raw
            settings_module.settings.payment.cartera_phone_allowlist_raw = CARTERA_PHONE
            try:
                result = await nodes_mod.node_forward_to_cartera(state)
            finally:
                settings_module.settings.payment.cartera_phone_allowlist_raw = orig_raw

    assert result.get("payment_status") == "awaiting_cartera"
    # The critical assertion: status must be 'awaiting_cartera', not 'forwarded'.
    assert (
        case.status == "awaiting_cartera"
    ), f"Expected case.status='awaiting_cartera' after off-hours defer, got {case.status!r}"


# ──────────────────────────────────────────────────────────────────────────────
# GAP 1 — scheduler picks up deferred case (cartera_message_wamid is None)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_fires_media_forward_for_deferred_case() -> None:
    """GAP 1b: check_pending_cases must call forward_case_to_cartera (upload+send media)
    for a deferred case (status='awaiting_cartera', cartera_message_wamid is None).

    Before the fix, the scheduler only called meta.send_buttons() as a reminder,
    never performing the actual media forward. Cartera received buttons but no image.
    """
    from app.features.payment import scheduler as sched

    att = _make_attachment()
    case = _make_case(
        status="awaiting_cartera",
        cartera_message_wamid=None,  # not yet forwarded
        work_hours_due_at=datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC),
        attachments=[att],
    )

    meta_mock = MagicMock()
    meta_mock.upload_media = AsyncMock(return_value="UPLOADED_ID")
    meta_mock.send_media = AsyncMock(return_value="wamid.cartera_fwd")
    meta_mock.send_buttons = AsyncMock(return_value="wamid.btn")
    chatwoot_mock = MagicMock()

    class _FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> _FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class _FakeSession:
        async def execute(self, _stmt: Any) -> _FakeResult:
            return _FakeResult([case])

        async def flush(self) -> None:
            pass

        async def commit(self) -> None:
            pass

    @asynccontextmanager
    async def _db_factory() -> Any:
        yield _FakeSession()

    payment_settings = MagicMock()
    payment_settings.cartera_phone_allowlist = frozenset({CARTERA_PHONE})

    # during business hours
    business_now = datetime(2026, 1, 1, 10, 30, 0, tzinfo=UTC)

    ctx = {
        "meta": meta_mock,
        "chatwoot": chatwoot_mock,
        "db_session_factory": _db_factory,
    }

    with (
        patch.object(sched, "_now_utc", return_value=business_now),
        patch.object(sched, "_get_settings_payment", return_value=payment_settings),
        patch("app.features.payment.scheduler.is_business_time", return_value=True),
        patch("app.features.payment.scheduler.business_minutes_between", return_value=25),
    ):
        result = await sched.check_pending_cases(ctx)

    # Scheduler must have actually forwarded the media — not just buttons.
    assert (
        meta_mock.upload_media.called
    ), "upload_media must be called for deferred case with no cartera_message_wamid"
    assert (
        meta_mock.send_media.called
    ), "send_media must be called for deferred case with no cartera_message_wamid"
    assert result.get("processed", 0) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# GAP 3 — send_media exception triggers Chatwoot escalation
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_media_exception_escalates_to_chatwoot() -> None:
    """GAP 3: if meta.send_media raises an exception (e.g. Meta 4xx),
    node_forward_to_cartera must:
      - log the error
      - escalate via Chatwoot (get_or_create_conversation + post_message)
      - set case.status = 'escalated'
      - return {"payment_status": "escalated"}

    Before the fix, the exception propagated out of the node, leaving
    the case stranded in 'forwarded' status.
    """
    from app.features.payment import nodes as nodes_mod

    att = _make_attachment()
    case = _make_case(status="forwarded", attachments=[att])
    factory = _make_session_ctx_factory(case)

    meta_mock = MagicMock()
    meta_mock.upload_media = AsyncMock(return_value="UPLOADED_ID")
    meta_mock.send_media = AsyncMock(side_effect=RuntimeError("Meta 4xx: window expired"))

    chatwoot_mock = MagicMock()
    chatwoot_mock.get_or_create_conversation = AsyncMock(return_value=99)
    chatwoot_mock.post_message = AsyncMock()

    business_now = datetime(2026, 1, 2, 10, 0, 0, tzinfo=UTC)  # Monday business hours

    state = {
        "wa_phone": CLIENT_PHONE,
        "case_id": CASE_ID,
        "poliza_id": "POL-001",
    }

    with (
        patch.object(nodes_mod, "_get_meta", return_value=meta_mock),
        patch.object(nodes_mod, "_get_chatwoot", return_value=chatwoot_mock),
        patch.object(nodes_mod, "_session_factory_fn", factory),
        patch.object(nodes_mod, "is_business_time", return_value=True),
    ):
        from app.config import settings as settings_module

        orig_raw = settings_module.settings.payment.cartera_phone_allowlist_raw
        settings_module.settings.payment.cartera_phone_allowlist_raw = CARTERA_PHONE
        with patch("app.features.payment.nodes.datetime") as dt_mock:
            dt_mock.now.return_value = business_now
            try:
                result = await nodes_mod.node_forward_to_cartera(state)
            finally:
                settings_module.settings.payment.cartera_phone_allowlist_raw = orig_raw

    assert (
        result.get("payment_status") == "escalated"
    ), f"Expected 'escalated' when send_media fails, got: {result!r}"
    # Chatwoot must have been notified.
    chatwoot_mock.get_or_create_conversation.assert_called_once()
    assert (
        chatwoot_mock.post_message.called
    ), "Chatwoot must receive escalation note on send_media failure"
    note_text = " ".join(
        str(call.args[1] if len(call.args) > 1 else call.kwargs.get("content", ""))
        for call in chatwoot_mock.post_message.call_args_list
    )
    assert CASE_ID in note_text, f"Escalation note must mention case_id, got: {note_text!r}"
    # Case status must be escalated.
    assert (
        case.status == "escalated"
    ), f"Expected case.status='escalated' after send_media failure, got: {case.status!r}"
