"""Tests for app.memory.debtor_flags (L4 flags, Fase 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
