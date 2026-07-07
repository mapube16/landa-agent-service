"""Tests for app.memory.debtor_flags (L4 flags, Fase 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.memory.debtor_flags import get_debtor_flags


class _FakeResult:
    def __init__(self, rows: list[tuple[str, datetime]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, datetime]]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[tuple[str, datetime]]) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)


async def test_no_history_returns_empty_dict() -> None:
    session = _FakeSession([])
    flags = await get_debtor_flags(session, "+573001234567")
    assert flags == {}


async def test_derives_flags_from_case_history() -> None:
    t1 = datetime(2026, 7, 1, tzinfo=UTC)
    t2 = datetime(2026, 7, 4, tzinfo=UTC)
    session = _FakeSession([("approved", t1), ("escalated", t2)])

    flags = await get_debtor_flags(session, "+573001234567")

    assert flags["intentos"] == 2
    assert flags["escalado_previo"] is True
    assert flags["ultima_interaccion_wa"] == t2.isoformat()


async def test_escalado_previo_false_when_never_escalated() -> None:
    t1 = datetime(2026, 7, 1, tzinfo=UTC)
    session = _FakeSession([("approved", t1)])

    flags = await get_debtor_flags(session, "+573001234567")

    assert flags["escalado_previo"] is False


async def test_no_poliza_id_skips_softseguros_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default (no poliza_id) never touches SoftSeguros — pure case-history flags."""
    mock_client = AsyncMock()
    monkeypatch.setattr("app.integrations.softseguros.get_softseguros_client", lambda: mock_client)
    session = _FakeSession([])

    await get_debtor_flags(session, "+573001234567")

    mock_client.get_cartera_status.assert_not_called()


async def test_poliza_id_enriches_flags_with_cartera_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.softseguros import CarteraStatus

    mock_client = AsyncMock()
    mock_client.get_cartera_status.return_value = CarteraStatus(
        edad_cartera=45,
        fecha_realizara_pago="2026-07-15",
        saldo_pendiente="200000.00",
        riesgo="LMT78B",
    )
    monkeypatch.setattr("app.integrations.softseguros.get_softseguros_client", lambda: mock_client)
    session = _FakeSession([])

    flags = await get_debtor_flags(session, "+573001234567", poliza_id="POL123")

    mock_client.get_cartera_status.assert_awaited_once_with("POL123")
    assert flags["dias_mora"] == 45
    assert flags["fecha_compromiso"] == "2026-07-15"
    assert flags["saldo_pendiente"] == "200000.00"
    assert flags["riesgo"] == "LMT78B"


async def test_poliza_id_no_cartera_pendiente_adds_no_extra_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_client = AsyncMock()
    mock_client.get_cartera_status.return_value = None
    monkeypatch.setattr("app.integrations.softseguros.get_softseguros_client", lambda: mock_client)
    t1 = datetime(2026, 7, 1, tzinfo=UTC)
    session = _FakeSession([("approved", t1)])

    flags = await get_debtor_flags(session, "+573001234567", poliza_id="POL123")

    assert flags["intentos"] == 1
    assert "dias_mora" not in flags


async def test_softseguros_failure_keeps_case_history_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SoftSeguros outage fails open — case-history flags survive intact."""
    mock_client = AsyncMock()
    mock_client.get_cartera_status.side_effect = RuntimeError("boom")
    monkeypatch.setattr("app.integrations.softseguros.get_softseguros_client", lambda: mock_client)
    t1 = datetime(2026, 7, 1, tzinfo=UTC)
    session = _FakeSession([("approved", t1)])

    flags = await get_debtor_flags(session, "+573001234567", poliza_id="POL123")

    assert flags["intentos"] == 1
    assert "dias_mora" not in flags
