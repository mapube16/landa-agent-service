"""Tests de la ventana de 24 h para mensajes del equipo (features/escalation/window.py).

Lo que se protege: pasadas 24 h sin mensaje del cliente, un mensaje del agente NO
se manda como texto (WhatsApp lo rechaza y en Chatwoot se ve enviado). Se guarda,
se avisa al cliente con UNA plantilla, y se entrega cuando el cliente responde.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.features.escalation.window import (
    REENGAGE_PAYLOAD,
    SAFETY_MARGIN_SECONDS,
    WINDOW_SECONDS,
    flush_pending,
    on_failed_status,
    record_inbound,
    relay_or_queue,
    window_open,
)

PHONE = "+573001112233"
H = 3600


class FakeRedis:
    """Redis minimo en memoria: set/get con nx, listas y expire."""

    def __init__(self) -> None:
        self.kv: dict[bytes, Any] = {}
        self.ttl: dict[bytes, int] = {}

    async def set(self, key: bytes, value: bytes, nx: bool = False, ex: int | None = None) -> Any:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        if ex:
            self.ttl[key] = ex
        return True

    async def get(self, key: bytes) -> Any:
        return self.kv.get(key)

    async def delete(self, key: bytes) -> int:
        return int(self.kv.pop(key, None) is not None)

    async def rpush(self, key: bytes, value: bytes) -> int:
        self.kv.setdefault(key, []).append(value)
        return len(self.kv[key])

    async def lrange(self, key: bytes, start: int, end: int) -> list[bytes]:
        return list(self.kv.get(key, []))

    async def llen(self, key: bytes) -> int:
        return len(self.kv.get(key, []))

    async def expire(self, key: bytes, seconds: int) -> None:
        self.ttl[key] = seconds


def _chatwoot(last_incoming: float | None = None) -> MagicMock:
    cw = MagicMock()
    msgs = [] if last_incoming is None else [{"message_type": 0, "created_at": last_incoming}]
    cw.list_messages = AsyncMock(return_value=msgs)
    cw.post_private_note = AsyncMock()
    return cw


def _meta() -> MagicMock:
    meta = MagicMock()
    meta.send_template = AsyncMock(return_value="wamid.tpl")
    return meta


async def _relay(redis: FakeRedis, cw: MagicMock, meta: MagicMock, send: AsyncMock) -> str:
    return await relay_or_queue(
        redis=redis,
        chatwoot=cw,
        meta=meta,
        phone=PHONE,
        conv_id=7,
        content="Hola, le escribo",
        attachments=[],
        msg_id="m1",
        send=send,
    )


# --- window_open ------------------------------------------------------------


def test_window_sin_dato_esta_cerrada() -> None:
    assert window_open(None, time.time()) is False


def test_window_abierta_dentro_de_24h() -> None:
    now = time.time()
    assert window_open(now - 2 * H, now) is True


def test_window_cerrada_en_el_margen_de_seguridad() -> None:
    now = time.time()
    casi = now - (WINDOW_SECONDS - SAFETY_MARGIN_SECONDS + 60)
    assert window_open(casi, now) is False


# --- relay_or_queue ---------------------------------------------------------


async def test_ventana_abierta_envia_normal_y_recuerda_el_wamid() -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    await record_inbound(redis, PHONE)
    send = AsyncMock(return_value="wamid.abc")

    assert await _relay(redis, cw, meta, send) == "sent"
    send.assert_awaited_once()
    meta.send_template.assert_not_awaited()
    assert json.loads(redis.kv[b"wa:out:wamid.abc"])["content"] == "Hola, le escribo"


async def test_ventana_cerrada_guarda_avisa_y_no_envia_texto() -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(time.time() - 3 * 24 * H), _meta()
    send = AsyncMock()

    assert await _relay(redis, cw, meta, send) == "queued"
    send.assert_not_awaited()
    meta.send_template.assert_awaited_once()
    assert meta.send_template.await_args.kwargs["quick_reply_payloads"] == [REENGAGE_PAYLOAD]
    assert len(redis.kv[b"wa:pending:+573001112233"]) == 1
    note = cw.post_private_note.await_args.args[1]
    assert "más de 24 horas" in note and "plantilla" in note


async def test_segundo_mensaje_no_repite_la_plantilla() -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    await _relay(redis, cw, meta, AsyncMock())
    await _relay(redis, cw, meta, AsyncMock())

    assert meta.send_template.await_count == 1
    assert len(redis.kv[b"wa:pending:+573001112233"]) == 2
    assert "Ya le habíamos enviado" in cw.post_private_note.await_args.args[1]


async def test_respaldo_en_chatwoot_si_no_hay_registro() -> None:
    """Clientes que escribieron antes del despliegue: se mira el hilo de Chatwoot."""
    redis, cw, meta = FakeRedis(), _chatwoot(time.time() - H), _meta()
    send = AsyncMock(return_value=None)

    assert await _relay(redis, cw, meta, send) == "sent"
    send.assert_awaited_once()


async def test_si_falla_la_plantilla_se_avisa_que_llame_y_se_reintenta() -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    meta.send_template = AsyncMock(side_effect=RuntimeError("plantilla no aprobada"))

    assert await _relay(redis, cw, meta, AsyncMock()) == "queued"
    assert "llama al cliente" in cw.post_private_note.await_args.args[1]
    assert b"wa:reengage:+573001112233" not in redis.kv  # liberado para reintentar


# --- flush_pending ----------------------------------------------------------


async def test_flush_entrega_en_orden_y_limpia() -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    for texto in ("uno", "dos"):
        await relay_or_queue(
            redis=redis,
            chatwoot=cw,
            meta=meta,
            phone=PHONE,
            conv_id=7,
            content=texto,
            attachments=[],
            msg_id=texto,
            send=AsyncMock(),
        )
    enviados: list[str] = []

    async def send_item(item: dict[str, Any]) -> None:
        enviados.append(item["content"])

    assert await flush_pending(redis, cw, "573001112233", send_item) == 2
    assert enviados == ["uno", "dos"]
    assert b"wa:pending:+573001112233" not in redis.kv
    assert b"wa:reengage:+573001112233" not in redis.kv
    assert "los 2 mensajes pendientes" in cw.post_private_note.await_args.args[1]


async def test_flush_sin_pendientes_no_hace_nada() -> None:
    redis, cw = FakeRedis(), _chatwoot()
    assert await flush_pending(redis, cw, PHONE, AsyncMock()) == 0
    cw.post_private_note.assert_not_awaited()


async def test_flush_con_fallo_deja_guardado_lo_que_falta() -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    for texto in ("uno", "dos", "tres"):
        await relay_or_queue(
            redis=redis,
            chatwoot=cw,
            meta=meta,
            phone=PHONE,
            conv_id=7,
            content=texto,
            attachments=[],
            msg_id=texto,
            send=AsyncMock(),
        )
    send_item = AsyncMock(side_effect=[None, RuntimeError("meta caido"), None])

    assert await flush_pending(redis, cw, PHONE, send_item) == 1
    restantes = [json.loads(x)["content"] for x in redis.kv[b"wa:pending:+573001112233"]]
    assert restantes == ["dos", "tres"]


# --- on_failed_status -------------------------------------------------------


async def test_rechazo_por_ventana_de_un_agente_se_reencola() -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    await record_inbound(redis, PHONE)
    await _relay(redis, cw, meta, AsyncMock(return_value="wamid.x"))

    await on_failed_status(
        redis=redis,
        chatwoot=cw,
        meta=meta,
        wamid="wamid.x",
        phone="573001112233",
        error_code=131047,
        error_title="Re-engagement",
    )

    assert len(redis.kv[b"wa:pending:+573001112233"]) == 1
    meta.send_template.assert_awaited_once()


async def test_rechazo_de_mensaje_del_bot_se_ignora() -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    await on_failed_status(
        redis=redis,
        chatwoot=cw,
        meta=meta,
        wamid="wamid.bot",
        phone=PHONE,
        error_code=131047,
        error_title=None,
    )
    meta.send_template.assert_not_awaited()
    cw.post_private_note.assert_not_awaited()


async def test_otro_rechazo_avisa_al_agente() -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    await record_inbound(redis, PHONE)
    await _relay(redis, cw, meta, AsyncMock(return_value="wamid.y"))

    await on_failed_status(
        redis=redis,
        chatwoot=cw,
        meta=meta,
        wamid="wamid.y",
        phone=PHONE,
        error_code=131026,
        error_title="Message undeliverable",
    )

    assert "Message undeliverable" in cw.post_private_note.await_args.args[1]
    meta.send_template.assert_not_awaited()


@pytest.mark.parametrize("raw", [b"", None])
async def test_last_inbound_vacio_cae_a_chatwoot(raw: Any) -> None:
    redis, cw, meta = FakeRedis(), _chatwoot(time.time() - 2 * H), _meta()
    if raw is not None:
        redis.kv[b"wa:last_in:+573001112233"] = raw
    assert await _relay(redis, cw, meta, AsyncMock(return_value=None)) == "sent"


# --- correcciones de la revision (27-sep) -----------------------------------


async def test_rechazo_duplicado_no_entrega_dos_veces() -> None:
    """Meta reenvia estados y no pasan por el dedup: el segundo rechazo del
    mismo wamid no debe reencolar el mensaje otra vez."""
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    await record_inbound(redis, PHONE)
    await _relay(redis, cw, meta, AsyncMock(return_value="wamid.dup"))

    for _ in range(2):
        await on_failed_status(
            redis=redis,
            chatwoot=cw,
            meta=meta,
            wamid="wamid.dup",
            phone=PHONE,
            error_code=131047,
            error_title="Re-engagement",
        )

    assert len(redis.kv[b"wa:pending:+573001112233"]) == 1
    assert meta.send_template.await_count == 1


async def test_plantilla_rechazada_libera_marca_y_pide_llamar() -> None:
    """Si Meta acepta la plantilla y luego la rechaza, se libera la marca de
    'ya avisado' y el agente recibe nota de llamar. Los pendientes no se tocan."""
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    meta.send_template = AsyncMock(return_value="wamid.tpl9")
    await _relay(redis, cw, meta, AsyncMock())  # ventana cerrada -> encola + plantilla
    assert b"wa:reengage:+573001112233" in redis.kv

    await on_failed_status(
        redis=redis,
        chatwoot=cw,
        meta=meta,
        wamid="wamid.tpl9",
        phone=PHONE,
        error_code=131026,
        error_title="Message undeliverable",
    )

    assert b"wa:reengage:+573001112233" not in redis.kv  # proximo mensaje reintenta
    assert len(redis.kv[b"wa:pending:+573001112233"]) == 1  # pendiente intacto
    note = cw.post_private_note.await_args.args[1]
    assert "plantilla de aviso no le llegó" in note and "llámalo" in note
    assert meta.send_template.await_count == 1  # no se reencola nada


async def test_flush_reactiva_el_silencio_del_bot() -> None:
    """Cliente responde dias despues (mute vencido): al entregar lo pendiente el
    bot debe quedar en silencio, no pedir la cedula sobre la misma respuesta."""
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    await relay_or_queue(
        redis=redis,
        chatwoot=cw,
        meta=meta,
        phone=PHONE,
        conv_id=7,
        content="hola",
        attachments=[],
        msg_id="m",
        send=AsyncMock(),
    )

    await flush_pending(redis, cw, PHONE, AsyncMock())

    assert redis.kv[b"bot:muted:+573001112233"].startswith(b"human:")


async def test_envio_con_adjuntos_recuerda_todos_los_wamids() -> None:
    """La red de seguridad debe cubrir texto Y adjuntos del agente."""
    redis, cw, meta = FakeRedis(), _chatwoot(), _meta()
    await record_inbound(redis, PHONE)
    send = AsyncMock(return_value=["wamid.t1", "wamid.a1", "wamid.a2"])

    assert await _relay(redis, cw, meta, send) == "sent"
    for w in ("wamid.t1", "wamid.a1", "wamid.a2"):
        assert json.loads(redis.kv[f"wa:out:{w}".encode()])["conv_id"] == 7
