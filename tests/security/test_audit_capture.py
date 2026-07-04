"""Tests for audit capture hooks in QA and payment graph nodes (Plan 05-04).

Verifies that:
- node_answer emits llm_turn, tool_call (when tools ran), and judge_decision.
- node_escalate emits escalation with the reason.
- node_confirming emits payment_approved.
- node_payment_escalate emits payment_rejected.
- No payload value is a float or nested dict.
- With emit_task NOT patched and no session_factory: node_answer still completes (fail-open).

Harness pattern: monkeypatch ``app.security.audit_log.emit_task`` with a recorder;
patch ``nodes.get_llm`` + ``nodes.judge_response`` with synchronous fakes (no real LLM).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

# ---------------------------------------------------------------------------
# Shared fake LLM / judge helpers
# ---------------------------------------------------------------------------


def _good_rubric(**overrides: object) -> Any:
    """Return an approving JudgeRubric-like object."""
    from app.security.judge import JudgeRubric

    defaults = {
        "is_in_scope": True,
        "leaks_other_polizas": False,
        "affirms_payment_without_cartera_approval": False,
        "factually_grounded": True,
        "no_jailbreak_echo": True,
        "no_pii_leak": True,
        "no_external_links": True,
        "sentiment_appropriate": True,
        "rationale": "ok",
    }
    return JudgeRubric(**{**defaults, **overrides})  # type: ignore[arg-type]


def _reject_rubric(**overrides: object) -> Any:
    """Return a rejecting JudgeRubric (leaks_other_polizas=True)."""
    return _good_rubric(leaks_other_polizas=True, **overrides)


class _FakeLLM:
    """Minimal async-callable fake LLM returning a plain AIMessage."""

    def __init__(self, content: str = "Respuesta del bot.", tool_calls: list | None = None) -> None:
        self._content = content
        self._tool_calls = tool_calls or []

    def bind_tools(self, tools: list) -> _FakeLLM:  # noqa: ARG002
        return self

    async def ainvoke(self, messages: Any) -> AIMessage:  # noqa: ARG002
        msg = AIMessage(content=self._content)
        msg.tool_calls = self._tool_calls  # type: ignore[attr-defined]
        return msg


class _FakeLLMWithTools(_FakeLLM):
    """Fake LLM that returns tool calls on first invocation, plain answer on second."""

    def __init__(self, tool_names: list[str]) -> None:
        super().__init__()
        self._calls = 0
        self._tool_names = tool_names

    async def ainvoke(self, messages: Any) -> AIMessage:  # noqa: ARG002
        self._calls += 1
        if self._calls == 1:
            # First call — return tool_calls
            tc = [
                {"name": name, "args": {}, "id": f"id_{i}"}
                for i, name in enumerate(self._tool_names)
            ]
            msg = AIMessage(content="")
            msg.tool_calls = tc  # type: ignore[attr-defined]
            return msg
        # Second call (after tool results) — return plain answer
        msg = AIMessage(content="Respuesta tras tools.")
        msg.tool_calls = []  # type: ignore[attr-defined]
        return msg


# ---------------------------------------------------------------------------
# Recorder fixture for emit_task
# ---------------------------------------------------------------------------


class _AuditRecorder:
    """Captures all emit_task calls as dicts for assertions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def emit_task(
        self,
        *,
        action: str,
        actor: str,
        conversation_id: str | None = None,
        poliza_id: str | None = None,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.calls.append(
            {
                "action": action,
                "actor": actor,
                "conversation_id": conversation_id,
                "poliza_id": poliza_id,
                "payload": payload,
                "metadata": metadata,
            }
        )

    def actions(self) -> list[str]:
        return [c["action"] for c in self.calls]


# ---------------------------------------------------------------------------
# QA state builder
# ---------------------------------------------------------------------------


def _qa_state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messages": [HumanMessage(content="Cual es mi saldo?")],
        "thread_id": "conv-abc123",
        "wa_phone": "+573001234567",
        "poliza_id": "POL-999",
        "judge_retries": 0,
        "last_rejection_rationale": None,
    }
    base.update(overrides)
    return base


# ===========================================================================
# Task 1 — QA graph capture tests
# ===========================================================================


class TestQAAuditCapture:
    """Verify audit hooks in node_answer and node_escalate."""

    @pytest.mark.asyncio
    async def test_plain_answer_emits_llm_turn_and_judge_decision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Approving turn without tools: llm_turn then judge_decision emitted."""
        import app.features.qa.nodes as nodes_mod
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        fake_llm = _FakeLLM()
        monkeypatch.setattr(nodes_mod, "get_llm", lambda role: fake_llm)  # noqa: ARG005

        rubric = _good_rubric()
        monkeypatch.setattr(nodes_mod, "judge_response", AsyncMock(return_value=rubric))
        monkeypatch.setattr(nodes_mod, "is_approved", lambda r: True)  # noqa: ARG005

        state = _qa_state()
        await nodes_mod.node_answer(state)

        acts = recorder.actions()
        assert "llm_turn" in acts, f"Expected llm_turn, got {acts}"
        assert "judge_decision" in acts, f"Expected judge_decision, got {acts}"
        # No tool_call
        assert "tool_call" not in acts

    @pytest.mark.asyncio
    async def test_plain_answer_uses_correct_actors_and_ids(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """llm_turn actor=bot, judge_decision actor=judge; ids from state."""
        import app.features.qa.nodes as nodes_mod
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        fake_llm = _FakeLLM()
        monkeypatch.setattr(nodes_mod, "get_llm", lambda role: fake_llm)  # noqa: ARG005

        rubric = _good_rubric()
        monkeypatch.setattr(nodes_mod, "judge_response", AsyncMock(return_value=rubric))
        monkeypatch.setattr(nodes_mod, "is_approved", lambda r: True)  # noqa: ARG005

        state = _qa_state(thread_id="conv-abc123", poliza_id="POL-999")
        await nodes_mod.node_answer(state)

        llm_ev = next(c for c in recorder.calls if c["action"] == "llm_turn")
        judge_ev = next(c for c in recorder.calls if c["action"] == "judge_decision")

        assert llm_ev["actor"] == "bot"
        assert judge_ev["actor"] == "judge"
        assert llm_ev["conversation_id"] == "conv-abc123"
        assert llm_ev["poliza_id"] == "POL-999"
        assert judge_ev["conversation_id"] == "conv-abc123"
        assert judge_ev["poliza_id"] == "POL-999"

    @pytest.mark.asyncio
    async def test_turn_with_tools_emits_tool_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tool execution path emits tool_call event with tool names in payload."""
        import langgraph.prebuilt as _lgp

        import app.features.qa.nodes as nodes_mod
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        fake_llm = _FakeLLMWithTools(["get_saldo"])
        monkeypatch.setattr(nodes_mod, "get_llm", lambda role: fake_llm)  # noqa: ARG005

        # ToolNode is imported inline inside node_answer; patch it at its source module
        mock_tool_node = AsyncMock()
        mock_tool_node.ainvoke.return_value = {"messages": []}
        monkeypatch.setattr(_lgp, "ToolNode", lambda tools: mock_tool_node)  # noqa: ARG005

        rubric = _good_rubric()
        monkeypatch.setattr(nodes_mod, "judge_response", AsyncMock(return_value=rubric))
        monkeypatch.setattr(nodes_mod, "is_approved", lambda r: True)  # noqa: ARG005

        state = _qa_state()
        await nodes_mod.node_answer(state)

        acts = recorder.actions()
        assert "tool_call" in acts, f"Expected tool_call, got {acts}"
        tc_ev = next(c for c in recorder.calls if c["action"] == "tool_call")
        assert "get_saldo" in tc_ev["payload"]["tools"]

    @pytest.mark.asyncio
    async def test_rejecting_judge_emits_judge_decision_with_approved_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rejecting judge: judge_decision payload has approved=False."""
        import app.features.qa.nodes as nodes_mod
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        fake_llm = _FakeLLM()
        monkeypatch.setattr(nodes_mod, "get_llm", lambda role: fake_llm)  # noqa: ARG005

        rubric = _reject_rubric()
        monkeypatch.setattr(nodes_mod, "judge_response", AsyncMock(return_value=rubric))
        monkeypatch.setattr(nodes_mod, "is_approved", lambda r: not r.leaks_other_polizas)

        state = _qa_state(judge_retries=1)  # already 1 retry → will escalate
        await nodes_mod.node_answer(state)

        judge_ev = next((c for c in recorder.calls if c["action"] == "judge_decision"), None)
        assert judge_ev is not None, "judge_decision not emitted on rejection"
        assert judge_ev["payload"]["approved"] is False

    @pytest.mark.asyncio
    async def test_node_escalate_emits_escalation_with_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """node_escalate emits escalation event with payload[reason] == state reason."""
        import app.features.qa.nodes as nodes_mod
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        state = _qa_state(escalation_reason="judge_rejected")
        await nodes_mod.node_escalate(state)

        acts = recorder.actions()
        assert "escalation" in acts, f"Expected escalation, got {acts}"
        esc_ev = next(c for c in recorder.calls if c["action"] == "escalation")
        assert esc_ev["payload"]["reason"] == "judge_rejected"

    @pytest.mark.asyncio
    async def test_no_float_or_nested_in_payloads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No payload value is a float or nested dict (AuditPayload constraint)."""
        import app.features.qa.nodes as nodes_mod
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        fake_llm = _FakeLLM()
        monkeypatch.setattr(nodes_mod, "get_llm", lambda role: fake_llm)  # noqa: ARG005

        rubric = _good_rubric()
        monkeypatch.setattr(nodes_mod, "judge_response", AsyncMock(return_value=rubric))
        monkeypatch.setattr(nodes_mod, "is_approved", lambda r: True)  # noqa: ARG005

        state = _qa_state()
        await nodes_mod.node_answer(state)
        await nodes_mod.node_escalate(state)

        for call in recorder.calls:
            for k, v in call["payload"].items():
                assert not isinstance(
                    v, float
                ), f"Float found in {call['action']} payload key={k!r} value={v!r}"
                assert not isinstance(
                    v, dict
                ), f"Nested dict found in {call['action']} payload key={k!r}"

    @pytest.mark.asyncio
    async def test_fail_open_no_session_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """node_answer completes even when emit_task is unpatched and session_factory absent."""
        import app.features.qa.nodes as nodes_mod

        # Ensure app.main.app.state has no session_factory
        try:
            from app.main import app as _app

            if hasattr(_app.state, "session_factory"):
                monkeypatch.delattr(_app.state, "session_factory", raising=False)
        except Exception:  # noqa: BLE001
            pass

        fake_llm = _FakeLLM()
        monkeypatch.setattr(nodes_mod, "get_llm", lambda role: fake_llm)  # noqa: ARG005

        rubric = _good_rubric()
        monkeypatch.setattr(nodes_mod, "judge_response", AsyncMock(return_value=rubric))
        monkeypatch.setattr(nodes_mod, "is_approved", lambda r: True)  # noqa: ARG005

        state = _qa_state()
        # Must not raise
        result = await nodes_mod.node_answer(state)
        assert isinstance(result, dict)


# ===========================================================================
# Task 2 — Payment nodes capture tests
# ===========================================================================


def _payment_state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messages": [],
        "thread_id": "+573009876543",
        "wa_phone": "+573009876543",
        "poliza_id": "POL-777",
        "case_id": "case-uuid-001",
        "cliente_doc": "98765432",
        "cliente_nombre": "Ana Gomez",
    }
    base.update(overrides)
    return base


def _make_mock_case(case_id: str, status: str = "awaiting_cartera") -> Any:
    from app.memory.case_store import Case

    case = MagicMock(spec=Case)
    case.case_id = case_id
    case.status = status
    case.attachments = []
    return case


def _make_payment_session_factory(mock_session: Any) -> Any:
    from contextlib import asynccontextmanager

    def _factory() -> Any:  # type: ignore[misc]
        @asynccontextmanager
        async def _ctx() -> Any:  # type: ignore[misc]
            yield mock_session

        return _ctx()

    return _factory


def _make_mock_session(case: Any = None) -> AsyncMock:
    session = AsyncMock()
    execute_result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = case
    execute_result.scalars.return_value = scalars_result
    session.execute.return_value = execute_result
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


class TestPaymentAuditCapture:
    """Verify audit hooks in node_confirming and node_payment_escalate."""

    @pytest.mark.asyncio
    async def test_node_confirming_emits_payment_approved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """node_confirming emits payment_approved with actor=cartera, correct ids."""
        import app.features.payment.nodes as payment_nodes
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        case_id = "case-uuid-001"
        mock_case = _make_mock_case(case_id, status="awaiting_cartera")
        mock_session = _make_mock_session(case=mock_case)
        sf = _make_payment_session_factory(mock_session)
        monkeypatch.setattr(payment_nodes, "_session_factory_fn", sf)

        state = _payment_state(case_id=case_id, thread_id="+573009876543", poliza_id="POL-777")
        await payment_nodes.node_confirming(state)

        assert (
            "payment_approved" in recorder.actions()
        ), f"Expected payment_approved, got {recorder.actions()}"
        ev = next(c for c in recorder.calls if c["action"] == "payment_approved")
        assert ev["actor"] == "cartera"
        assert ev["poliza_id"] == "POL-777"
        assert ev["payload"]["case_id"] == case_id
        assert ev["payload"]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_node_confirming_conversation_id_from_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """conversation_id in payment_approved event comes from state phone/thread."""
        import app.features.payment.nodes as payment_nodes
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        case_id = "case-xyz-999"
        mock_case = _make_mock_case(case_id)
        mock_session = _make_mock_session(case=mock_case)
        sf = _make_payment_session_factory(mock_session)
        monkeypatch.setattr(payment_nodes, "_session_factory_fn", sf)

        phone = "+573001111111"
        state = _payment_state(case_id=case_id, wa_phone=phone, thread_id=phone)
        await payment_nodes.node_confirming(state)

        ev = next(c for c in recorder.calls if c["action"] == "payment_approved")
        assert ev["conversation_id"] == phone

    @pytest.mark.asyncio
    async def test_node_payment_escalate_emits_payment_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """node_payment_escalate emits payment_rejected with actor=cartera."""
        import app.features.payment.nodes as payment_nodes
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        case_id = "case-rej-002"
        mock_case = _make_mock_case(case_id, status="awaiting_cartera")
        mock_session = _make_mock_session(case=mock_case)
        sf = _make_payment_session_factory(mock_session)
        monkeypatch.setattr(payment_nodes, "_session_factory_fn", sf)

        mock_chatwoot = AsyncMock()
        mock_chatwoot.get_or_create_conversation.return_value = 77
        mock_chatwoot.post_message.return_value = None
        monkeypatch.setattr(payment_nodes, "_get_chatwoot", lambda: mock_chatwoot)

        state = _payment_state(case_id=case_id, poliza_id="POL-555")
        await payment_nodes.node_payment_escalate(state)

        assert (
            "payment_rejected" in recorder.actions()
        ), f"Expected payment_rejected, got {recorder.actions()}"
        ev = next(c for c in recorder.calls if c["action"] == "payment_rejected")
        assert ev["actor"] == "cartera"
        assert ev["payload"]["case_id"] == case_id
        assert ev["payload"]["status"] == "escalated"

    @pytest.mark.asyncio
    async def test_payment_payloads_no_float_or_nested(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No float or nested dict in payment audit payloads."""
        import app.features.payment.nodes as payment_nodes
        import app.security.audit_log as audit_mod

        recorder = _AuditRecorder()
        monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)

        case_id = "case-flat-003"
        mock_case = _make_mock_case(case_id)
        mock_session = _make_mock_session(case=mock_case)
        sf = _make_payment_session_factory(mock_session)
        monkeypatch.setattr(payment_nodes, "_session_factory_fn", sf)

        mock_chatwoot = AsyncMock()
        mock_chatwoot.get_or_create_conversation.return_value = 10
        mock_chatwoot.post_message.return_value = None
        monkeypatch.setattr(payment_nodes, "_get_chatwoot", lambda: mock_chatwoot)

        state = _payment_state(case_id=case_id)
        # Run both nodes to collect all payment audit events
        await payment_nodes.node_confirming(state)
        await payment_nodes.node_payment_escalate(state)

        for call in recorder.calls:
            for k, v in call["payload"].items():
                assert not isinstance(v, float), f"Float in {call['action']} key={k!r} value={v!r}"
                assert not isinstance(v, dict), f"Nested dict in {call['action']} key={k!r}"
