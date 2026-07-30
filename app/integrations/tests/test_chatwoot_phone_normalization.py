"""El mismo cliente debe caer SIEMPRE en la misma conversación de Chatwoot.

Bug real (30-jul): la cache key salía del teléfono crudo. El webhook de Meta
entrega el número SIN '+' y el handoff CON '+', así que un mismo cliente tenía
dos entradas de caché y terminaba con hilos separados — las plantillas en uno
y los mensajes del bot en otro (JULIAN: #92, #93, #94 con un mensaje cada uno).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest  # type: ignore[import-not-found]


class FakeRedis:
    """Redis de dict — lo que importa es qué CLAVES se tocan."""

    def __init__(self) -> None:
        self.store: dict[bytes, bytes] = {}

    async def get(self, key: bytes) -> bytes | None:
        return self.store.get(key)

    async def set(
        self, key: bytes, value: bytes, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: bytes) -> int:
        return int(self.store.pop(key, None) is not None)


def _client(redis: FakeRedis) -> Any:
    from app.integrations.chatwoot import ChatwootClient

    c = ChatwootClient.__new__(ChatwootClient)  # sin __init__: no red, no settings
    c._account_id = 1  # type: ignore[attr-defined]
    c._redis = redis  # type: ignore[attr-defined]
    c._http = MagicMock()  # type: ignore[attr-defined]
    # El contacto existe y ya tiene un hilo reusable: nunca debe crear otro.
    c._create_or_get_contact = AsyncMock(return_value=74)  # type: ignore[attr-defined]
    c._find_reusable_conversation = AsyncMock(return_value=92)  # type: ignore[attr-defined]
    c._create_conversation = AsyncMock(return_value=999)  # type: ignore[attr-defined]
    return c


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_con_y_sin_mas_resuelven_la_misma_conversacion() -> None:
    redis = FakeRedis()
    c = _client(redis)

    con_mas = await c.get_or_create_conversation("+573124449163")
    sin_mas = await c.get_or_create_conversation("573124449163")

    assert con_mas == sin_mas == 92
    # Y la segunda llamada salió del caché: sin segunda búsqueda de contacto.
    assert c._create_or_get_contact.await_count == 1
    c._create_conversation.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_una_sola_entrada_de_cache_por_cliente() -> None:
    """Dos formatos del mismo número no pueden generar dos claves."""
    redis = FakeRedis()
    c = _client(redis)

    await c.get_or_create_conversation("573124449163")
    await c.get_or_create_conversation("+573124449163")

    conv_keys = [k for k in redis.store if k.startswith(b"chatwoot:conv:")]
    assert len(conv_keys) == 1
