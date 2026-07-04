"""Tests for app.features.payment.cartera (Plan 04-05, TDD RED/GREEN).

Covers:
  - parse_button_id: valid actions, invalid inputs
  - resume_payment_interrupt: idempotent when already approved, calls graph when active
  - handle_cartera_message: text triggers button re-send, unknown button triggers re-send

I/O dependencies (qa_graph, meta_client, db_session) are stubbed with AsyncMock.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CASE_ID = str(uuid.uuid4())
CARTERA_PHONE = "+573001234567"


def _make_case(status: str = "awaiting_cartera", case_id: str = CASE_ID) -> MagicMock:
    """Return a mock Case ORM object."""
    case = MagicMock()
    case.case_id = case_id
    case.phone = "+573200000001"
    case.status = status
    return case


class _FakeResult:
    """Mimic SQLAlchemy async result with scalars().first()."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalars(self) -> _FakeResult:
        return self

    def first(self) -> Any:
        return self._value


class _FakeSession:
    """Minimal async session stub."""

    def __init__(self, case: Any = None) -> None:
        self._case = case

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self._case)

    async def flush(self) -> None:
        pass


def _session_factory(case: Any = None) -> Any:
    """Return an asynccontextmanager factory yielding a _FakeSession."""

    @asynccontextmanager
    async def _ctx() -> Any:  # type: ignore[misc]
        yield _FakeSession(case)

    return _ctx


# ---------------------------------------------------------------------------
# parse_button_id tests
# ---------------------------------------------------------------------------


def test_parse_button_id_valid_aprobar() -> None:
    from app.features.payment.cartera import parse_button_id

    assert parse_button_id(f"aprobar|{CASE_ID}") == ("aprobar", CASE_ID)


def test_parse_button_id_valid_rechazar() -> None:
    from app.features.payment.cartera import parse_button_id

    assert parse_button_id(f"rechazar|{CASE_ID}") == ("rechazar", CASE_ID)


def test_parse_button_id_valid_info() -> None:
    from app.features.payment.cartera import parse_button_id

    assert parse_button_id(f"info|{CASE_ID}") == ("info", CASE_ID)


def test_parse_button_id_no_pipe() -> None:
    from app.features.payment.cartera import parse_button_id

    assert parse_button_id("nonsense") is None


def test_parse_button_id_unknown_action() -> None:
    from app.features.payment.cartera import parse_button_id

    assert parse_button_id(f"eliminar|{CASE_ID}") is None


def test_parse_button_id_empty_case_id() -> None:
    from app.features.payment.cartera import parse_button_id

    assert parse_button_id("aprobar|") is None


def test_parse_button_id_empty_string() -> None:
    from app.features.payment.cartera import parse_button_id

    assert parse_button_id("") is None


# ---------------------------------------------------------------------------
# resume_payment_interrupt tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_idempotent_when_already_approved() -> None:
    """Case already in terminal state — no-op, graph never touched."""
    from app.features.payment.cartera import resume_payment_interrupt

    case = _make_case(status="approved")
    qa_graph = AsyncMock()

    result = await resume_payment_interrupt(
        action="aprobar",
        case_id=CASE_ID,
        extra=None,
        qa_graph=qa_graph,
        db_session_factory=_session_factory(case),
    )

    assert result is True
    qa_graph.aupdate_state.assert_not_called()
    qa_graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_resume_idempotent_when_rejected() -> None:
    """Case already rejected — same no-op."""
    from app.features.payment.cartera import resume_payment_interrupt

    case = _make_case(status="rejected")
    qa_graph = AsyncMock()

    result = await resume_payment_interrupt(
        action="rechazar",
        case_id=CASE_ID,
        extra=None,
        qa_graph=qa_graph,
        db_session_factory=_session_factory(case),
    )

    assert result is True
    qa_graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_resume_calls_graph_when_active() -> None:
    """Active case (awaiting_cartera) — graph aupdate_state + ainvoke called."""
    from app.features.payment.cartera import resume_payment_interrupt

    case = _make_case(status="awaiting_cartera")
    qa_graph = AsyncMock()

    result = await resume_payment_interrupt(
        action="aprobar",
        case_id=CASE_ID,
        extra=None,
        qa_graph=qa_graph,
        db_session_factory=_session_factory(case),
    )

    assert result is True
    qa_graph.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_resume_returns_false_when_case_not_found() -> None:
    """Case not found in DB — returns False, graph not touched."""
    from app.features.payment.cartera import resume_payment_interrupt

    qa_graph = AsyncMock()

    result = await resume_payment_interrupt(
        action="aprobar",
        case_id="nonexistent-id",
        extra=None,
        qa_graph=qa_graph,
        db_session_factory=_session_factory(None),
    )

    assert result is False
    qa_graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_resume_dispatches_confirmation_to_client() -> None:
    """After resume, the send_to_client AIMessage from graph state reaches the client.

    Regression: node_confirming only puts the confirmation into graph state;
    without dispatch on the resume path the client never receives it (found
    in live smoke — cartera tapped Aprobar, client got nothing).
    """
    from langchain_core.messages import AIMessage

    from app.features.payment.cartera import resume_payment_interrupt

    case = _make_case(status="awaiting_cartera")
    qa_graph = AsyncMock()
    qa_graph.ainvoke.return_value = {
        "messages": [
            AIMessage(
                content="Tu pago fue confirmado para la poliza POL-123. Gracias.",
                additional_kwargs={"payment_approved": True, "send_to_client": True},
            )
        ],
        "payment_status": "approved",
    }
    meta_client = AsyncMock()

    result = await resume_payment_interrupt(
        action="aprobar",
        case_id=CASE_ID,
        extra=None,
        qa_graph=qa_graph,
        db_session_factory=_session_factory(case),
        meta_client=meta_client,
    )

    assert result is True
    meta_client.send_text.assert_awaited_once()
    kwargs = meta_client.send_text.await_args.kwargs
    assert kwargs["to"] == case.phone
    assert "confirmado" in kwargs["body"]


@pytest.mark.asyncio
async def test_resume_dispatch_blocked_by_firewall_without_approval() -> None:
    """D-28: a confirmation-looking message without payment_approved never sends."""
    from langchain_core.messages import AIMessage

    from app.features.payment.cartera import resume_payment_interrupt

    case = _make_case(status="awaiting_cartera")
    qa_graph = AsyncMock()
    qa_graph.ainvoke.return_value = {
        "messages": [
            AIMessage(
                content="Tu pago fue confirmado para la poliza POL-123. Gracias.",
                additional_kwargs={"send_to_client": True},
            )
        ],
    }
    meta_client = AsyncMock()

    result = await resume_payment_interrupt(
        action="aprobar",
        case_id=CASE_ID,
        extra=None,
        qa_graph=qa_graph,
        db_session_factory=_session_factory(case),
        meta_client=meta_client,
    )

    assert result is True
    meta_client.send_text.assert_not_called()


# ---------------------------------------------------------------------------
# handle_cartera_message tests
# ---------------------------------------------------------------------------


def _make_text_msg(from_: str = CARTERA_PHONE) -> MagicMock:
    """Build a mock InboundMessage with type='text'."""
    msg = MagicMock()
    msg.type = "text"
    msg.from_ = from_
    msg.interactive = None
    return msg


def _make_interactive_msg(button_id: str, from_: str = CARTERA_PHONE) -> MagicMock:
    """Build a mock InboundMessage with type='interactive'."""
    msg = MagicMock()
    msg.type = "interactive"
    msg.from_ = from_

    interactive = MagicMock()
    interactive.selected_id.return_value = button_id
    msg.interactive = interactive
    return msg


@pytest.mark.asyncio
async def test_text_message_resends_buttons() -> None:
    """Text from cartera (no button) must re-send 3 buttons."""
    from app.features.payment.cartera import handle_cartera_message

    case = _make_case(status="awaiting_cartera")
    meta_client = AsyncMock()
    meta_client.send_buttons.return_value = "wamid.btn1"

    qa_graph = AsyncMock()
    msg = _make_text_msg()

    await handle_cartera_message(
        msg=msg,
        qa_graph=qa_graph,
        meta_client=meta_client,
        db_session_factory=_session_factory(case),
    )

    meta_client.send_buttons.assert_called_once()
    # Must not touch the graph on free text
    qa_graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_button_resends_buttons() -> None:
    """Unrecognized button_id (e.g. 'badbutton|x') triggers re-send of buttons."""
    from app.features.payment.cartera import handle_cartera_message

    case = _make_case(status="awaiting_cartera")
    meta_client = AsyncMock()
    meta_client.send_buttons.return_value = "wamid.btn2"

    qa_graph = AsyncMock()
    msg = _make_interactive_msg(button_id="badbutton|xyz")

    await handle_cartera_message(
        msg=msg,
        qa_graph=qa_graph,
        meta_client=meta_client,
        db_session_factory=_session_factory(case),
    )

    meta_client.send_buttons.assert_called_once()
    qa_graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_valid_button_calls_resume() -> None:
    """Valid aprobar button_id triggers resume path, not button re-send."""
    from app.features.payment.cartera import handle_cartera_message

    case = _make_case(status="awaiting_cartera")
    meta_client = AsyncMock()
    qa_graph = AsyncMock()
    msg = _make_interactive_msg(button_id=f"aprobar|{CASE_ID}")

    await handle_cartera_message(
        msg=msg,
        qa_graph=qa_graph,
        meta_client=meta_client,
        db_session_factory=_session_factory(case),
    )

    # Graph was invoked (resume path), buttons not re-sent
    qa_graph.ainvoke.assert_called_once()
    meta_client.send_buttons.assert_not_called()


@pytest.mark.asyncio
async def test_no_open_case_no_send() -> None:
    """If no open case found, silently drop — no outbound."""
    from app.features.payment.cartera import handle_cartera_message

    meta_client = AsyncMock()
    qa_graph = AsyncMock()
    msg = _make_text_msg()

    await handle_cartera_message(
        msg=msg,
        qa_graph=qa_graph,
        meta_client=meta_client,
        db_session_factory=_session_factory(None),
    )

    meta_client.send_buttons.assert_not_called()
    meta_client.send_text.assert_not_called()
