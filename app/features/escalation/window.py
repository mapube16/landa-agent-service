"""Ventana de 24 horas de WhatsApp para los mensajes que el equipo escribe en Chatwoot.

WhatsApp solo entrega mensajes libres dentro de las 24 h siguientes al ultimo
mensaje del cliente. Fuera de ese plazo Meta acepta el envio, lo rechaza despues
(error 131047) y en Chatwoot el mensaje del agente se ve enviado aunque nunca
llego: el 25-sep se perdieron asi 9 respuestas del equipo.

Flujo:
  1. ``relay_or_queue``: antes de reenviar un mensaje del agente mira cuando
     escribio el cliente por ultima vez. Ventana abierta: se envia normal.
     Cerrada: se guarda, se manda UNA plantilla aprobada con boton
     "Ver respuesta" y se deja nota privada al agente.
  2. ``flush_pending``: cuando el cliente escribe o toca el boton, la ventana se
     abre y los mensajes guardados se entregan en orden.
  3. ``on_failed_status``: red de seguridad. Si Meta igual rechaza un mensaje
     del agente por la ventana, se vuelve a guardar y se dispara la plantilla.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from app.config.settings import settings
from app.features.escalation.mute import _normalize_e164, set_human

log = structlog.get_logger("features.escalation.window")

WINDOW_SECONDS = 24 * 3600
# Margen: un mensaje enviado a las 23:59 puede llegar a Meta despues de las 24 h.
SAFETY_MARGIN_SECONDS = 30 * 60
PENDING_TTL_SECONDS = 7 * 24 * 3600
REENGAGE_PAYLOAD = "ver_respuesta"
# Codigo de Meta para "pasaron mas de 24 h desde el ultimo mensaje del cliente".
REENGAGEMENT_ERROR = 131047

SendItem = Callable[[dict[str, Any]], Awaitable[None]]


def _k(prefix: str, value: str) -> bytes:
    return f"wa:{prefix}:{value}".encode()


def window_open(last_inbound: float | None, now: float) -> bool:
    """True si todavia se puede escribir libre. Sin dato, se asume cerrada."""
    return last_inbound is not None and now - last_inbound < WINDOW_SECONDS - SAFETY_MARGIN_SECONDS


async def record_inbound(redis: Any, phone: str, now: float | None = None) -> None:
    """Marca el ultimo mensaje del cliente. Fail-open: nunca rompe el webhook."""
    try:
        ts = time.time() if now is None else now
        await redis.set(
            _k("last_in", _normalize_e164(phone)), str(ts).encode(), ex=2 * WINDOW_SECONDS
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("window.record_inbound_failed", error_type=type(exc).__name__)


async def last_inbound(redis: Any, chatwoot: Any, phone: str, conv_id: int) -> float | None:
    """Hora del ultimo mensaje del cliente: Redis primero, Chatwoot de respaldo.

    El respaldo cubre a los clientes que escribieron antes de que existiera el
    registro en Redis: el hilo de Chatwoot espeja todos sus mensajes.
    """
    try:
        raw = await redis.get(_k("last_in", _normalize_e164(phone)))
        if raw:
            return float(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("window.last_inbound.redis_failed", error_type=type(exc).__name__)
    try:
        msgs = await chatwoot.list_messages(conv_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("window.last_inbound.chatwoot_failed", error_type=type(exc).__name__)
        return None
    stamps = [
        float(m["created_at"])
        for m in msgs
        if m.get("message_type") in (0, "incoming") and not m.get("private") and m.get("created_at")
    ]
    return max(stamps) if stamps else None


async def _note(chatwoot: Any, conv_id: int | None, text: str) -> None:
    if conv_id is None:
        return
    try:
        await chatwoot.post_private_note(conv_id, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("window.note_failed", error_type=type(exc).__name__)


async def _reengage(redis: Any, meta: Any, phone: str, conv_id: int | None) -> str:
    """Envia la plantilla una sola vez cada 24 h. Devuelve sent | already | failed."""
    key = _k("reengage", phone)
    if not await redis.set(key, b"1", nx=True, ex=WINDOW_SECONDS):
        return "already"
    try:
        tpl_wamid = await meta.send_template(
            phone,
            settings.whatsapp.reengage_template,
            "es",
            body_params=[],
            quick_reply_payloads=[REENGAGE_PAYLOAD],
        )
    except Exception as exc:  # noqa: BLE001
        # Sin plantilla no hay aviso: se libera la marca para reintentar con el
        # siguiente mensaje del agente (p. ej. si Meta aun no la aprueba).
        await redis.delete(key)
        log.error("window.reengage_failed", error_type=type(exc).__name__)
        return "failed"
    # Red de seguridad tambien para la plantilla: si Meta la acepta y luego la
    # rechaza (numero sin WhatsApp, tope de envios, opt-out), on_failed_status
    # libera la marca y avisa al agente en vez de dejar al cliente esperando.
    if tpl_wamid:
        try:
            await redis.set(
                _k("out", tpl_wamid),
                json.dumps({"reengage": True, "conv_id": conv_id}).encode(),
                ex=2 * WINDOW_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("window.remember_reengage_failed", error_type=type(exc).__name__)
    log.info("window.reengage_sent")
    return "sent"


async def _queue(redis: Any, phone: str, item: dict[str, Any]) -> int:
    key = _k("pending", phone)
    await redis.rpush(key, json.dumps(item, ensure_ascii=False).encode())
    await redis.expire(key, PENDING_TTL_SECONDS)
    return int(await redis.llen(key))


def _queued_note(status: str, pending: int) -> str:
    base = (
        "WhatsApp no entrega este mensaje todavía: el cliente lleva más de 24 horas sin "
        "escribir. Quedó guardado"
        + (f" junto con otros {pending - 1}" if pending > 1 else "")
        + " y le llegará apenas el cliente responda."
    )
    if status == "sent":
        return base + " Le enviamos la plantilla «Ver respuesta» para avisarle."
    if status == "already":
        return base + " Ya le habíamos enviado la plantilla de aviso."
    return base + " No se pudo enviar la plantilla de aviso: si es urgente, llama al cliente."


async def relay_or_queue(
    *,
    redis: Any,
    chatwoot: Any,
    meta: Any,
    phone: str,
    conv_id: int,
    content: str,
    attachments: list[dict[str, Any]],
    msg_id: str,
    send: Callable[[], Awaitable[list[str] | str | None]],
) -> str:
    """Envia el mensaje del agente o lo guarda si la ventana esta cerrada.

    ``send`` hace el envio real (texto y adjuntos) y devuelve los wamid
    enviados, que se recuerdan 48 h para la red de seguridad de
    ``on_failed_status``. Devuelve "sent" o "queued".
    """
    phone = _normalize_e164(phone)
    if window_open(await last_inbound(redis, chatwoot, phone, conv_id), time.time()):
        result = await send()
        wamids = [result] if isinstance(result, str) else list(result or [])
        item = {
            "content": content,
            "attachments": attachments,
            "conv_id": conv_id,
            "msg_id": msg_id,
        }
        for wamid in wamids:
            if not wamid:
                continue
            try:
                await redis.set(
                    _k("out", wamid),
                    json.dumps(item, ensure_ascii=False).encode(),
                    ex=2 * WINDOW_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("window.remember_out_failed", error_type=type(exc).__name__)
        return "sent"

    item = {
        "content": content,
        "attachments": attachments,
        "conv_id": conv_id,
        "msg_id": msg_id,
        "t": time.time(),
    }
    pending = await _queue(redis, phone, item)
    status = await _reengage(redis, meta, phone, conv_id)
    await _note(chatwoot, conv_id, _queued_note(status, pending))
    log.info("window.queued", pending=pending, reengage=status)
    return "queued"


async def flush_pending(redis: Any, chatwoot: Any, phone: str, send_item: SendItem) -> int:
    """Entrega en orden los mensajes guardados. Llamar cuando el cliente escribe.

    Nunca lanza: un fallo aqui no puede tumbar la atencion del mensaje entrante.
    Los que fallen al enviarse quedan guardados para el proximo intento.
    """
    phone = _normalize_e164(phone)
    key = _k("pending", phone)
    try:
        raw_items = await redis.lrange(key, 0, -1)
        if not raw_items:
            return 0
        await redis.delete(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("window.flush.redis_failed", error_type=type(exc).__name__)
        return 0

    delivered = 0
    conv_id: int | None = None
    for i, raw in enumerate(raw_items):
        item = json.loads(raw)
        conv_id = item.get("conv_id") or conv_id
        try:
            await send_item(item)
            delivered += 1
        except Exception as exc:  # noqa: BLE001
            log.error("window.flush.send_failed", error_type=type(exc).__name__)
            for rest in raw_items[i:]:
                await redis.rpush(key, rest)
            await redis.expire(key, PENDING_TTL_SECONDS)
            break
    await redis.delete(_k("reengage", phone))
    if delivered:
        # El agente retoma la conversacion al entregarse su respuesta: si el mute
        # de 24 h ya expiro, sin esto el bot contestaria el mismo mensaje del
        # cliente pidiendo la cedula.
        try:
            await set_human(redis, phone)
        except Exception as exc:  # noqa: BLE001
            log.warning("window.flush.mute_failed", error_type=type(exc).__name__)
        text = (
            "El cliente respondió y se le entregó el mensaje que estaba pendiente."
            if delivered == 1
            else f"El cliente respondió y se le entregaron los {delivered} mensajes pendientes."
        )
        await _note(chatwoot, conv_id, text)
    log.info("window.flushed", delivered=delivered, total=len(raw_items))
    return delivered


async def on_failed_status(
    *,
    redis: Any,
    chatwoot: Any,
    meta: Any,
    wamid: str,
    phone: str,
    error_code: int | None,
    error_title: str | None,
) -> None:
    """Meta rechazo un mensaje: si era del agente, avisarle y, si fue por la
    ventana, guardarlo y mandar la plantilla. Mensajes del bot se ignoran."""
    try:
        raw = await redis.get(_k("out", wamid))
    except Exception as exc:  # noqa: BLE001
        log.warning("window.failed.redis_failed", error_type=type(exc).__name__)
        return
    if not raw:
        return
    # Meta reenvia eventos de estado y los estados no pasan por el dedup de
    # mensajes: sin borrar el registro, un rechazo duplicado entregaria el
    # mismo texto dos veces al cliente.
    try:
        await redis.delete(_k("out", wamid))
    except Exception as exc:  # noqa: BLE001
        log.warning("window.failed.delete_failed", error_type=type(exc).__name__)
    item = json.loads(raw)
    conv_id = item.get("conv_id")
    phone = _normalize_e164(phone)
    if item.get("reengage"):
        # La plantilla de aviso no llego: liberar la marca para que el proximo
        # mensaje del agente la reintente, y avisar que hoy toca llamar.
        await redis.delete(_k("reengage", phone))
        await _note(
            chatwoot,
            conv_id,
            f"La plantilla de aviso no le llegó al cliente "
            f"({error_title or 'sin detalle'}). Sus mensajes siguen guardados: "
            "si es urgente, llámalo.",
        )
        log.warning("window.reengage_undelivered", error_code=error_code)
        return
    if error_code == REENGAGEMENT_ERROR:
        pending = await _queue(redis, phone, {**item, "t": time.time()})
        status = await _reengage(redis, meta, phone, conv_id)
        await _note(chatwoot, conv_id, _queued_note(status, pending))
        return
    await _note(
        chatwoot,
        conv_id,
        f"WhatsApp no entregó tu último mensaje ({error_title or 'sin detalle'}). "
        "El cliente no lo recibió: intenta de nuevo o llámalo.",
    )
