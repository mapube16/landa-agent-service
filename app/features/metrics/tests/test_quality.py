"""Tests for the LLM quality auditor (features/metrics/quality.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.features.metrics.quality import (
    QualityRubric,
    aggregate_quality,
    audit_conversation,
    render_transcript,
)


def test_render_transcript_roles_and_skips_private() -> None:
    msgs = [
        {"message_type": 1, "content": "Hola, soy ARIA"},
        {"message_type": 0, "content": "saldo"},
        {"message_type": 1, "content": "nota interna", "private": True},
        {"message_type": 0, "content": ""},  # empty skipped
    ]
    t = render_transcript(msgs)
    assert "BOT: Hola, soy ARIA" in t
    assert "CLIENTE: saldo" in t
    assert "nota interna" not in t  # private excluded


async def test_audit_empty_transcript_is_clean() -> None:
    r = await audit_conversation([{"message_type": 1, "content": "", "private": True}])
    assert r.intento_certificar_pago is False
    assert r.rationale == "sin problemas"


async def test_audit_uses_llm_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.features.metrics.quality as q

    rubric = QualityRubric(
        intento_certificar_pago=True,
        lista_rota=False,
        loop_sin_salida=False,
        cliente_frustrado=False,
        rationale="el bot dijo que está al día",
    )
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=rubric)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    monkeypatch.setattr(q, "get_llm", lambda _role: llm)

    r = await audit_conversation(
        [
            {"message_type": 0, "content": "ya pagué"},
            {"message_type": 1, "content": "tu póliza está al día"},
        ]
    )
    assert r.intento_certificar_pago is True


async def test_audit_fail_open_on_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.features.metrics.quality as q

    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=RuntimeError("gateway down"))
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    monkeypatch.setattr(q, "get_llm", lambda _role: llm)

    r = await audit_conversation([{"message_type": 0, "content": "hola"}])
    assert r.intento_certificar_pago is False  # fail-open = all clear


def test_aggregate_quality_counts_flags() -> None:
    rubrics = [
        QualityRubric(
            intento_certificar_pago=True,
            lista_rota=False,
            loop_sin_salida=False,
            cliente_frustrado=True,
            rationale="",
        ),
        QualityRubric(
            intento_certificar_pago=True,
            lista_rota=True,
            loop_sin_salida=False,
            cliente_frustrado=False,
            rationale="",
        ),
    ]
    out = aggregate_quality(rubrics)
    assert out["anom_intento_certificar_pago"] == 2
    assert out["anom_lista_rota"] == 1
    assert out["anom_cliente_frustrado"] == 1
    assert out["conversaciones_auditadas"] == 2
