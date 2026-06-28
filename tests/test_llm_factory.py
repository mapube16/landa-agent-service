"""Tests for the get_llm() factory (plan 01-04, GOAL-1.10)."""

from __future__ import annotations

import pytest


def test_get_llm_returns_chatopenai_for_conversation() -> None:
    from app.integrations.openrouter import get_llm

    llm = get_llm("conversation")
    assert llm.model_name == "google/gemini-2.0-pro"
    assert "openrouter.ai" in str(llm.openai_api_base)


def test_get_llm_cached_per_role() -> None:
    from app.integrations.openrouter import get_llm

    a = get_llm("conversation")
    b = get_llm("conversation")
    assert a is b


def test_get_llm_judge_temperature_zero() -> None:
    from app.integrations.openrouter import get_llm

    judge = get_llm("judge")
    assert judge.temperature == 0.0


def test_get_llm_intent_alias_resolves_to_same_instance() -> None:
    from app.integrations.openrouter import get_llm

    intent_alias = get_llm("intent")
    intent_canonical = get_llm("intent_classifier")
    assert intent_alias is intent_canonical


def test_get_llm_unknown_role_raises_key_error() -> None:
    from app.integrations.openrouter import get_llm

    with pytest.raises(KeyError):
        get_llm("totally_unknown_role")


def test_get_llm_re_export_path_matches_integration_path() -> None:
    from app.config.llm import get_llm as via_config
    from app.integrations.openrouter import get_llm as via_integration

    assert via_config is via_integration
