"""Unit tests for app.security.judge.

Covers:
- JudgeRubric has exactly 8 bool fields (D-05 invariant)
- is_approved semantics for all 8 flags
- judge_response returns None on LLM parse failure
- judge_response returns JudgeRubric on success
- No raw rationale logged (Pitfall 5)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from app.security.judge import JudgeRubric, is_approved, judge_response


def _good_rubric(**overrides: object) -> JudgeRubric:
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


# ---------------------------------------------------------------------------
# Test 1: D-05 invariant -- exactly 8 bool fields
# ---------------------------------------------------------------------------


def test_judge_rubric_has_exactly_8_bool_flags() -> None:
    bool_fields = [
        name for name, info in JudgeRubric.model_fields.items() if info.annotation is bool
    ]
    assert len(bool_fields) == 8, (
        f"JudgeRubric must have exactly 8 bool fields (D-05 invariant). "
        f"Found {len(bool_fields)}: {bool_fields}. "
        "Changes to the rubric require updating is_approved() and LangSmith eval datasets."
    )


# ---------------------------------------------------------------------------
# Test 2: is_approved all-good returns True
# ---------------------------------------------------------------------------


def test_is_approved_all_good_returns_true() -> None:
    assert is_approved(_good_rubric()) is True


# ---------------------------------------------------------------------------
# Test 3: leaks_other_polizas=True -> reject
# ---------------------------------------------------------------------------


def test_is_approved_leaks_other_polizas_returns_false() -> None:
    assert is_approved(_good_rubric(leaks_other_polizas=True)) is False


# ---------------------------------------------------------------------------
# Test 4: no_pii_leak=False -> reject (flag=False means leak detected)
# ---------------------------------------------------------------------------


def test_is_approved_pii_leak_returns_false() -> None:
    assert is_approved(_good_rubric(no_pii_leak=False)) is False


# ---------------------------------------------------------------------------
# Test 5: judge_response returns None on LLM parse failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_response_returns_none_on_parse_failure() -> None:
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=None)

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain

    with patch("app.security.judge.get_llm", return_value=mock_llm):
        result = await judge_response([], "respuesta del bot")

    assert result is None


# ---------------------------------------------------------------------------
# Test 6: judge_response returns JudgeRubric on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_response_returns_rubric_on_success() -> None:
    expected = _good_rubric()
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=expected)

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain

    with patch("app.security.judge.get_llm", return_value=mock_llm):
        result = await judge_response([], "respuesta del bot")

    assert result is expected


# ---------------------------------------------------------------------------
# Test 7: No raw rationale in logs (Pitfall 5 -- log only rationale_len)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_response_does_not_log_rationale_raw() -> None:
    rubric = _good_rubric(rationale="SECRETO_QUE_NO_DEBE_SALIR_EN_LOGS")
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=rubric)

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain

    with structlog.testing.capture_logs() as captured:
        with patch("app.security.judge.get_llm", return_value=mock_llm):
            await judge_response([], "respuesta")

    for entry in captured:
        assert "rationale" not in entry or entry.get("rationale") != rubric.rationale, (
            "Raw rationale must NOT appear in logs (Pitfall 5 -- PII leak vector). "
            "Use rationale_len or rationale_hash instead."
        )
