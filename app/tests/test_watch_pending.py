"""Tests del vigia de colas de la ventana de 24 h (worker.watch_pending_queues)."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker import watch_pending_queues


class _Redis:
    def __init__(self, queues: dict[bytes, list[bytes]]) -> None:
        self.queues = queues

    def scan_iter(self, match: bytes) -> Any:
        async def _gen() -> Any:
            for k in self.queues:
                yield k

        return _gen()

    async def lrange(self, key: bytes, start: int, end: int) -> list[bytes]:
        return self.queues.get(key, [])


def _item(days_old: float, conv_id: int = 7) -> bytes:
    return json.dumps(
        {"content": "x", "conv_id": conv_id, "t": time.time() - days_old * 86400}
    ).encode()


@pytest.mark.asyncio
async def test_cola_vieja_deja_nota(monkeypatch: pytest.MonkeyPatch) -> None:
    cw = MagicMock()
    cw.post_private_note = AsyncMock()
    monkeypatch.setattr("app.integrations.chatwoot.get_chatwoot_client", lambda: cw)
    redis = _Redis({b"wa:pending:+573001112233": [_item(4.2), _item(4.0)]})

    await watch_pending_queues({"redis": redis})

    conv, note = cw.post_private_note.await_args.args
    assert conv == 7
    assert "2 mensajes del equipo esperan" in note and "llámalo" in note


@pytest.mark.asyncio
async def test_cola_reciente_no_molesta(monkeypatch: pytest.MonkeyPatch) -> None:
    cw = MagicMock()
    cw.post_private_note = AsyncMock()
    monkeypatch.setattr("app.integrations.chatwoot.get_chatwoot_client", lambda: cw)
    redis = _Redis({b"wa:pending:+573001112233": [_item(0.5)]})

    await watch_pending_queues({"redis": redis})

    cw.post_private_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_redis_no_explota() -> None:
    await watch_pending_queues({})
