"""Tests for app.features.payment.nodes (Task 2 — TDD RED phase).

Tests:
  - node_receive_comprobante: new case / reuse case / terminal→new / magic-byte mismatch
  - node_forward_to_cartera: buttons only on last / outside hours defers
  - node_confirming: sets payment_approved flag
  - node_payment_escalate: opens Chatwoot

I/O dependencies (meta client, chatwoot client, db session) are stubbed with
AsyncMock so tests run without real infra.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — minimal valid bytes
# ---------------------------------------------------------------------------

_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 50


def _make_state(**overrides: Any) -> dict[str, Any]:
    """Return a minimal QAState dict for testing nodes."""
    base: dict[str, Any] = {
        "messages": [],
        "thread_id": "+573001234567",
        "wa_phone": "+573001234567",
        "poliza_id": "POL123",
        "cliente_doc": "12345678",
        "cliente_nombre": "Juan Perez",
        "_inbound_media": {
            "media_id": "META_MEDIA_001",
            "mime_type": "image/jpeg",
            "wamid": "WA_MSG_001",
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_meta() -> AsyncMock:
    meta = AsyncMock()
    meta.download_media.return_value = (_JPEG_BYTES, "image/jpeg")
    meta.upload_media.return_value = "UPLOADED_MEDIA_ID"
    meta.send_media.return_value = "CARTERA_WAMID_001"
    meta.send_text.return_value = "BOT_WAMID_001"
    return meta


@pytest.fixture()
def mock_chatwoot() -> AsyncMock:
    chatwoot = AsyncMock()
    chatwoot.get_or_create_conversation.return_value = 42
    chatwoot.post_message.return_value = None
    return chatwoot


@pytest.fixture()
def mock_session() -> AsyncMock:
    """Async SQLAlchemy session mock.

    ``session.execute(...)`` returns a mock whose ``.scalars().first()``
    returns ``None`` by default (no existing case row). Individual tests
    override ``mock_session.execute.return_value.scalars.return_value.first.return_value``
    to inject an existing Case object.
    """
    session = AsyncMock()
    # Build the call chain mock: execute() -> scalars() -> first() -> None
    execute_result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = None  # no existing case by default
    execute_result.scalars.return_value = scalars_result
    session.execute.return_value = execute_result
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture()
def mock_session_factory(mock_session: AsyncMock) -> Any:
    """session_scope async context manager factory.

    Returns a callable that returns an async context manager (no args needed).
    """
    from contextlib import asynccontextmanager

    def _factory() -> Any:  # type: ignore[misc]
        @asynccontextmanager
        async def _ctx() -> Any:  # type: ignore[misc]
            yield mock_session

        return _ctx()

    return _factory


# ---------------------------------------------------------------------------
# node_receive_comprobante
# ---------------------------------------------------------------------------


class TestNodeReceiveComprobante:
    @pytest.mark.asyncio
    async def test_receive_creates_new_case_when_none_exists(
        self,
        mock_meta: AsyncMock,
        mock_session: AsyncMock,
        mock_session_factory: Any,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No existing case → INSERT new case row, set payment_status=forwarded."""
        from app.config.settings import settings

        monkeypatch.setattr(settings.payment, "volume_path", tmp_path)

        import app.features.payment.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "_get_meta", lambda: mock_meta)
        monkeypatch.setattr(nodes_mod, "_session_factory_fn", mock_session_factory)

        state = _make_state()
        result = await nodes_mod.node_receive_comprobante(state)

        assert result["payment_status"] == "forwarded"
        assert "case_id" in result
        assert result["attachment_count"] >= 1

    @pytest.mark.asyncio
    async def test_receive_reuses_open_case(
        self,
        mock_meta: AsyncMock,
        mock_session: AsyncMock,
        mock_session_factory: Any,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Existing open case → reuse same case_id (D-07)."""
        from app.config.settings import settings
        from app.memory.case_store import Case

        existing_case_id = str(uuid.uuid4())
        existing_case = MagicMock(spec=Case)
        existing_case.case_id = existing_case_id
        existing_case.status = "awaiting_receipt"
        existing_case.attachment_count = 1
        existing_case.poliza_id = "POL123"
        existing_case.cliente_doc = "12345678"
        existing_case.cliente_nombre = "Juan Perez"

        mock_session.execute.return_value.scalars.return_value.first.return_value = existing_case

        monkeypatch.setattr(settings.payment, "volume_path", tmp_path)

        import app.features.payment.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "_get_meta", lambda: mock_meta)
        monkeypatch.setattr(nodes_mod, "_session_factory_fn", mock_session_factory)

        state = _make_state(case_id=existing_case_id)
        result = await nodes_mod.node_receive_comprobante(state)

        assert result["case_id"] == existing_case_id
        assert result["payment_status"] == "forwarded"

    @pytest.mark.asyncio
    async def test_receive_opens_new_case_when_prior_is_terminal(
        self,
        mock_meta: AsyncMock,
        mock_session: AsyncMock,
        mock_session_factory: Any,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prior case is 'approved' (terminal) → open new case_id (D-09)."""
        from app.config.settings import settings
        from app.memory.case_store import Case

        old_case_id = str(uuid.uuid4())
        old_case = MagicMock(spec=Case)
        old_case.case_id = old_case_id
        old_case.status = "approved"  # terminal
        old_case.attachment_count = 1
        old_case.poliza_id = "POL123"
        old_case.cliente_doc = "12345678"
        old_case.cliente_nombre = "Juan Perez"

        mock_session.execute.return_value.scalars.return_value.first.return_value = old_case

        monkeypatch.setattr(settings.payment, "volume_path", tmp_path)

        import app.features.payment.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "_get_meta", lambda: mock_meta)
        monkeypatch.setattr(nodes_mod, "_session_factory_fn", mock_session_factory)

        state = _make_state(case_id=old_case_id)
        result = await nodes_mod.node_receive_comprobante(state)

        # Must open a NEW case, not reuse old_case_id
        assert result["case_id"] != old_case_id
        assert result["payment_status"] == "forwarded"

    @pytest.mark.asyncio
    async def test_receive_rejects_magic_byte_mismatch(
        self,
        mock_session: AsyncMock,
        mock_session_factory: Any,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Magic-byte mismatch → sends rejection text, returns awaiting_receipt."""
        from app.config.settings import settings

        monkeypatch.setattr(settings.payment, "volume_path", tmp_path)

        bad_meta = AsyncMock()
        bad_meta.download_media.return_value = (b"MZ\x90\x00" + b"\x00" * 50, "image/jpeg")
        bad_meta.send_text.return_value = "BOT_WAMID_ERR"

        import app.features.payment.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "_get_meta", lambda: bad_meta)
        monkeypatch.setattr(nodes_mod, "_session_factory_fn", mock_session_factory)

        state = _make_state()
        result = await nodes_mod.node_receive_comprobante(state)

        assert result["payment_status"] == "awaiting_receipt"
        bad_meta.send_text.assert_awaited_once()
        # No case row should be in the result
        assert result.get("case_id") is None or "case_id" not in result


# ---------------------------------------------------------------------------
# node_forward_to_cartera
# ---------------------------------------------------------------------------


class TestNodeForwardToCartera:
    @pytest.mark.asyncio
    async def test_forward_buttons_only_on_last(
        self,
        mock_meta: AsyncMock,
        mock_session: AsyncMock,
        mock_session_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """3 attachments → send_media called 3 times; only 3rd call has buttons (D-08)."""
        from app.config.settings import settings
        from app.memory.case_store import Attachment, Case

        case_id = str(uuid.uuid4())
        case_obj = MagicMock(spec=Case)
        case_obj.case_id = case_id
        case_obj.status = "forwarded"
        case_obj.attachment_count = 3
        case_obj.poliza_id = "POL123"
        case_obj.cliente_doc = "12345678"
        case_obj.cliente_nombre = "Juan Perez"
        case_obj.created_at = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)

        def _make_att(i: int) -> MagicMock:
            att = MagicMock(spec=Attachment)
            att.path = f"comprobantes/case/{i}.jpg"  # relative path; no S108
            att.mime_type = "image/jpeg"
            return att

        case_obj.attachments = [_make_att(i) for i in range(3)]

        mock_session.execute.return_value.scalars.return_value.first.return_value = case_obj

        # Patch settings.payment.cartera_phone_allowlist
        monkeypatch.setattr(settings.payment, "cartera_phone_allowlist_raw", "+573009999999")

        import app.features.payment.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "_get_meta", lambda: mock_meta)
        monkeypatch.setattr(nodes_mod, "_session_factory_fn", mock_session_factory)

        # Patch is_business_time to return True
        with patch("app.features.payment.nodes.is_business_time", return_value=True):
            state = _make_state(case_id=case_id, payment_status="forwarded")
            result = await nodes_mod.node_forward_to_cartera(state)

        assert mock_meta.send_media.call_count == 3
        calls = mock_meta.send_media.call_args_list

        # First two calls have no buttons
        for call in calls[:2]:
            kwargs = call.kwargs if call.kwargs else {}
            args = call.args if call.args else ()
            buttons_arg = kwargs.get("buttons") or (args[4] if len(args) > 4 else None)
            assert buttons_arg is None

        # Last call has buttons
        last_kwargs = calls[2].kwargs if calls[2].kwargs else {}
        last_args = calls[2].args if calls[2].args else ()
        last_buttons = last_kwargs.get("buttons") or (last_args[4] if len(last_args) > 4 else None)
        assert last_buttons is not None
        button_ids = [b[0] for b in last_buttons]
        assert any(b.startswith("aprobar|") for b in button_ids)
        assert any(b.startswith("rechazar|") for b in button_ids)

        assert result["payment_status"] == "awaiting_cartera"

    @pytest.mark.asyncio
    async def test_forward_outside_hours_sends_ack_and_defers(
        self,
        mock_meta: AsyncMock,
        mock_session: AsyncMock,
        mock_session_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Outside business hours → client ack, no forward to cartera (D-13)."""
        from app.config.settings import settings
        from app.memory.case_store import Attachment, Case

        case_id = str(uuid.uuid4())
        case_obj = MagicMock(spec=Case)
        case_obj.case_id = case_id
        case_obj.status = "forwarded"
        case_obj.attachment_count = 1
        case_obj.poliza_id = "POL123"
        case_obj.cliente_doc = "12345678"
        case_obj.cliente_nombre = "Juan Perez"
        case_obj.created_at = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)

        att = MagicMock(spec=Attachment)
        att.path = "comprobantes/case/file.jpg"
        att.mime_type = "image/jpeg"
        case_obj.attachments = [att]

        mock_session.execute.return_value.scalars.return_value.first.return_value = case_obj

        monkeypatch.setattr(settings.payment, "cartera_phone_allowlist_raw", "+573009999999")

        import app.features.payment.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "_get_meta", lambda: mock_meta)
        monkeypatch.setattr(nodes_mod, "_session_factory_fn", mock_session_factory)

        with patch("app.features.payment.nodes.is_business_time", return_value=False):
            state = _make_state(case_id=case_id, payment_status="forwarded")
            result = await nodes_mod.node_forward_to_cartera(state)

        # Must NOT have called upload_media or send_media (no forward)
        mock_meta.upload_media.assert_not_called()
        mock_meta.send_media.assert_not_called()

        # Must have sent ack to client
        mock_meta.send_text.assert_awaited_once()

        assert result["payment_status"] == "awaiting_cartera"


# ---------------------------------------------------------------------------
# node_confirming
# ---------------------------------------------------------------------------


class TestNodeConfirming:
    @pytest.mark.asyncio
    async def test_confirming_sets_payment_approved_flag(
        self,
        mock_session: AsyncMock,
        mock_session_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """node_confirming must set payment_approved=True in state (D-28)."""
        from langchain_core.messages import AIMessage

        import app.features.payment.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "_session_factory_fn", mock_session_factory)

        state = _make_state(
            case_id=str(uuid.uuid4()),
            poliza_id="POL456",
            payment_status="approved",
        )
        result = await nodes_mod.node_confirming(state)

        assert result["payment_approved"] is True
        assert result["payment_status"] == "approved"

        # An AIMessage with payment_approved=True must be in messages
        ai_msgs = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
        assert ai_msgs, "node_confirming must emit an AIMessage"
        assert ai_msgs[0].additional_kwargs.get("payment_approved") is True


# ---------------------------------------------------------------------------
# node_payment_escalate
# ---------------------------------------------------------------------------


class TestNodePaymentEscalate:
    @pytest.mark.asyncio
    async def test_escalate_opens_chatwoot(
        self,
        mock_chatwoot: AsyncMock,
        mock_meta: AsyncMock,
        mock_session: AsyncMock,
        mock_session_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """node_payment_escalate must call get_or_create_conversation on Chatwoot."""
        import app.features.payment.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "_get_meta", lambda: mock_meta)
        monkeypatch.setattr(nodes_mod, "_get_chatwoot", lambda: mock_chatwoot)
        monkeypatch.setattr(nodes_mod, "_session_factory_fn", mock_session_factory)

        state = _make_state(case_id=str(uuid.uuid4()), payment_status="awaiting_cartera")
        result = await nodes_mod.node_payment_escalate(state)

        mock_chatwoot.get_or_create_conversation.assert_awaited_once()
        assert result["payment_status"] == "escalated"
