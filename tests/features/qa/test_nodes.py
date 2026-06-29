"""Tests for app.features.qa.nodes — state transitions for all 5 nodes.

All LLM / SoftSeguros / judge calls are mocked. Tests are pure state-machine
checks — no live infrastructure required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pybreaker
import pytest
from langchain_core.messages import HumanMessage


def _make_state(**overrides: Any) -> dict[str, Any]:
    """Build a minimal QAState dict."""
    base: dict[str, Any] = {
        "messages": [],
        "poliza_id": None,
        "cliente_doc": None,
        "polizas_list": [],
        "doc_retries": 0,
        "judge_retries": 0,
        "node": "awaiting_identification",
        "escalation_reason": None,
        "last_rejection_rationale": None,
        "force_escalate": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# node_identify tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_identify_empty_message_returns_greeting() -> None:
    from app.features.qa.nodes import node_identify

    state = _make_state(messages=[])
    result = await node_identify(state)  # type: ignore[arg-type]
    assert result["node"] == "awaiting_identification"
    msgs = result["messages"]
    assert any("👋" in str(m.content) for m in msgs)


@pytest.mark.asyncio
async def test_node_identify_zero_polizas_first_attempt_retries() -> None:
    from app.features.qa.messages import T_02
    from app.features.qa.nodes import node_identify

    mock_client = MagicMock()
    mock_client.get_clientes_by_documento = AsyncMock(return_value={"id": 42})
    mock_client.get_polizas_by_cliente = AsyncMock(return_value=[])

    state = _make_state(messages=[HumanMessage(content="12345678")], doc_retries=0)
    with patch("app.features.qa.nodes.get_softseguros_client", return_value=mock_client):
        result = await node_identify(state)  # type: ignore[arg-type]

    assert result["node"] == "awaiting_identification"
    assert result["doc_retries"] == 1
    assert any(T_02 in str(m.content) for m in result["messages"])


@pytest.mark.asyncio
async def test_node_identify_zero_polizas_second_attempt_escalates() -> None:
    from app.features.qa.messages import T_03
    from app.features.qa.nodes import node_identify

    mock_client = MagicMock()
    mock_client.get_clientes_by_documento = AsyncMock(return_value={"id": 42})
    mock_client.get_polizas_by_cliente = AsyncMock(return_value=[])

    state = _make_state(messages=[HumanMessage(content="12345678")], doc_retries=1)
    with patch("app.features.qa.nodes.get_softseguros_client", return_value=mock_client):
        result = await node_identify(state)  # type: ignore[arg-type]

    assert result["node"] == "escalating"
    assert any(T_03 in str(m.content) for m in result["messages"])


@pytest.mark.asyncio
async def test_node_identify_one_poliza_locks_and_advances() -> None:
    from app.features.qa.nodes import node_identify

    poliza = {"id": 101, "numero_poliza": "67890", "ramo_nombre": "AUTOMOVILES"}
    mock_client = MagicMock()
    mock_client.get_clientes_by_documento = AsyncMock(return_value={"id": 1})
    mock_client.get_polizas_by_cliente = AsyncMock(return_value=[poliza])

    state = _make_state(messages=[HumanMessage(content="12345678")])
    with patch("app.features.qa.nodes.get_softseguros_client", return_value=mock_client):
        result = await node_identify(state)  # type: ignore[arg-type]

    assert result["node"] == "answering_qa"
    assert result["poliza_id"] is not None


@pytest.mark.asyncio
async def test_node_identify_multiple_polizas_emits_t04() -> None:
    from app.features.qa.nodes import node_identify

    polizas = [
        {
            "id": 1,
            "numero_poliza": "11111",
            "ramo_nombre": "AUTOMOVILES",
            "estado_poliza_nombre": "Vigente",
        },
        {
            "id": 2,
            "numero_poliza": "22222",
            "ramo_nombre": "VIDA",
            "estado_poliza_nombre": "Vigente",
        },
        {
            "id": 3,
            "numero_poliza": "33333",
            "ramo_nombre": "HOGAR",
            "estado_poliza_nombre": "Vencida",
        },
    ]
    mock_client = MagicMock()
    mock_client.get_clientes_by_documento = AsyncMock(return_value={"id": 1})
    mock_client.get_polizas_by_cliente = AsyncMock(return_value=polizas)

    state = _make_state(messages=[HumanMessage(content="12345678")])
    with patch("app.features.qa.nodes.get_softseguros_client", return_value=mock_client):
        result = await node_identify(state)  # type: ignore[arg-type]

    assert result["node"] == "awaiting_policy_choice"
    assert len(result["polizas_list"]) == 3
    assert any("1️⃣" in str(m.content) for m in result["messages"])


@pytest.mark.asyncio
async def test_node_identify_breaker_open_escalates() -> None:
    from app.features.qa.messages import T_06
    from app.features.qa.nodes import node_identify

    mock_client = MagicMock()
    mock_client.get_clientes_by_documento = AsyncMock(side_effect=pybreaker.CircuitBreakerError())

    state = _make_state(messages=[HumanMessage(content="12345678")])
    with patch("app.features.qa.nodes.get_softseguros_client", return_value=mock_client):
        result = await node_identify(state)  # type: ignore[arg-type]

    assert result["node"] == "escalating"
    assert result.get("escalation_reason") == "breaker"
    assert any(T_06 in str(m.content) for m in result["messages"])


# ---------------------------------------------------------------------------
# node_choose_policy tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_choose_policy_parses_numeric_index() -> None:
    from app.features.qa.nodes import node_choose_policy

    polizas = [
        {"id": "A", "numero_poliza": "11111"},
        {"id": "B", "numero_poliza": "22222"},
        {"id": "C", "numero_poliza": "33333"},
    ]
    state = _make_state(
        messages=[HumanMessage(content="2")],
        polizas_list=polizas,
        node="awaiting_policy_choice",
    )
    result = await node_choose_policy(state)  # type: ignore[arg-type]
    assert result["node"] == "answering_qa"
    assert result["poliza_id"] == "B"


@pytest.mark.asyncio
async def test_node_choose_policy_invalid_input_stays() -> None:
    from app.features.qa.nodes import node_choose_policy

    polizas = [
        {"id": "A", "numero_poliza": "11111"},
        {"id": "B", "numero_poliza": "22222"},
    ]
    state = _make_state(
        messages=[HumanMessage(content="xyzabc gibberish")],
        polizas_list=polizas,
        node="awaiting_policy_choice",
    )
    # Mock LLM fallback to return NONE
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="NONE"))

    with patch("app.features.qa.nodes.get_llm", return_value=mock_llm):
        result = await node_choose_policy(state)  # type: ignore[arg-type]

    assert result["node"] == "awaiting_policy_choice"


# ---------------------------------------------------------------------------
# node_answer tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_answer_judge_approves_returns_message() -> None:
    from app.features.qa.nodes import node_answer
    from app.security.judge import JudgeRubric

    approved_rubric = JudgeRubric(
        is_in_scope=True,
        leaks_other_polizas=False,
        affirms_payment_without_cartera_approval=False,
        factually_grounded=True,
        no_jailbreak_echo=True,
        no_pii_leak=True,
        no_external_links=True,
        sentiment_appropriate=True,
        rationale="ok",
    )
    mock_llm = MagicMock()
    mock_result = MagicMock()
    mock_result.content = "Tu saldo pendiente es 100.000 COP."
    mock_result.tool_calls = []
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(return_value=mock_result)

    state = _make_state(
        messages=[HumanMessage(content="¿cuánto debo?")],
        poliza_id="101",
        node="answering_qa",
    )

    with (
        patch("app.features.qa.nodes.get_llm", return_value=mock_llm),
        patch("app.features.qa.nodes.judge_response", AsyncMock(return_value=approved_rubric)),
        patch(
            "app.features.qa.nodes.load_kb",
            return_value="== REFERENCIA ==\nx\n== FIN REFERENCIA ==",
        ),
    ):
        result = await node_answer(state)  # type: ignore[arg-type]

    assert result["node"] == "answering_qa"
    assert result["judge_retries"] == 0
    msgs = result["messages"]
    assert any("send_to_client" in m.additional_kwargs for m in msgs)


@pytest.mark.asyncio
async def test_node_answer_judge_rejects_first_time_increments_counter() -> None:
    from app.features.qa.nodes import node_answer
    from app.security.judge import JudgeRubric

    rejected_rubric = JudgeRubric(
        is_in_scope=False,
        leaks_other_polizas=False,
        affirms_payment_without_cartera_approval=False,
        factually_grounded=False,
        no_jailbreak_echo=True,
        no_pii_leak=True,
        no_external_links=True,
        sentiment_appropriate=True,
        rationale="out of scope response",
    )
    mock_llm = MagicMock()
    mock_result = MagicMock()
    mock_result.content = "some bad response"
    mock_result.tool_calls = []
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(return_value=mock_result)

    state = _make_state(
        messages=[HumanMessage(content="test")],
        poliza_id="101",
        node="answering_qa",
        judge_retries=0,
    )

    with (
        patch("app.features.qa.nodes.get_llm", return_value=mock_llm),
        patch("app.features.qa.nodes.judge_response", AsyncMock(return_value=rejected_rubric)),
        patch(
            "app.features.qa.nodes.load_kb",
            return_value="== REFERENCIA ==\nx\n== FIN REFERENCIA ==",
        ),
    ):
        result = await node_answer(state)  # type: ignore[arg-type]

    assert result["node"] == "answering_qa"
    assert result["judge_retries"] == 1


@pytest.mark.asyncio
async def test_node_answer_judge_rejects_second_time_escalates() -> None:
    from app.features.qa.messages import T_07
    from app.features.qa.nodes import node_answer

    mock_llm = MagicMock()
    mock_result = MagicMock()
    mock_result.content = "bad response again"
    mock_result.tool_calls = []
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(return_value=mock_result)

    state = _make_state(
        messages=[HumanMessage(content="test")],
        poliza_id="101",
        node="answering_qa",
        judge_retries=1,
    )

    with (
        patch("app.features.qa.nodes.get_llm", return_value=mock_llm),
        patch("app.features.qa.nodes.judge_response", AsyncMock(return_value=None)),
        patch(
            "app.features.qa.nodes.load_kb",
            return_value="== REFERENCIA ==\nx\n== FIN REFERENCIA ==",
        ),
    ):
        result = await node_answer(state)  # type: ignore[arg-type]

    assert result["node"] == "escalating"
    assert any(T_07 in str(m.content) for m in result["messages"])


@pytest.mark.asyncio
async def test_node_answer_escalate_tool_called_escalates() -> None:
    from app.features.qa.messages import T_08
    from app.features.qa.nodes import node_answer

    mock_llm = MagicMock()
    mock_result = MagicMock()
    mock_result.content = ""
    mock_result.tool_calls = [
        {"name": "escalate_to_human", "args": {"reason": "cliente pidió agente"}, "id": "tc1"}
    ]
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(return_value=mock_result)

    state = _make_state(
        messages=[HumanMessage(content="quiero un humano")],
        poliza_id="101",
        node="answering_qa",
    )

    with (
        patch("app.features.qa.nodes.get_llm", return_value=mock_llm),
        patch(
            "app.features.qa.nodes.load_kb",
            return_value="== REFERENCIA ==\nx\n== FIN REFERENCIA ==",
        ),
    ):
        result = await node_answer(state)  # type: ignore[arg-type]

    assert result["node"] == "escalating"
    assert result.get("escalation_reason") == "escape_hatch"
    assert any(T_08 in str(m.content) for m in result["messages"])
