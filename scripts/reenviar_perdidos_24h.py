"""One-off: reenviar los mensajes del equipo que WhatsApp rechazo por la ventana de 24 h.

Antes del despliegue del 27-sep, lo que un agente escribia en Chatwoot mas de
24 h despues del ultimo mensaje del cliente se veia enviado pero Meta lo
rechazaba (131047). Este script los encuentra y los pasa por el flujo nuevo
(``relay_or_queue``): quedan guardados, el cliente recibe la plantilla
"Ver respuesta" y le llegan al tocarla. Si el cliente ya escribio y la ventana
esta abierta, se envian directo.

  python scripts/reenviar_perdidos_24h.py buscar [--desde 2026-09-01]  # solo lectura
  python scripts/reenviar_perdidos_24h.py enviar --dry-run
  python scripts/reenviar_perdidos_24h.py enviar

Perdido = mensaje de un agente humano (no nota privada, no espejo del bot),
enviado 24 h o mas despues del ultimo mensaje del cliente, sin que el cliente
haya vuelto a escribir despues y sin que el flujo nuevo ya lo haya guardado.

Reanudable: los enviados quedan en perdidos_24h.json.sent.log. Necesita
REDIS_URL alcanzable desde afuera (REDIS_PUBLIC_URL del servicio Redis).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "perdidos_24h.json"  # datos personales: gitignored
SENT_LOG = OUT.with_suffix(".json.sent.log")
DAY = 24 * 3600
COT = timezone(timedelta(hours=-5))
QUEUED_NOTE = "WhatsApp no entrega este mensaje todavía"


def _get(c: httpx.Client, path: str, **params: object) -> dict[str, Any]:
    for attempt in range(6):
        try:
            r = c.get(path, params=params)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1 + attempt * 2)
                continue
            r.raise_for_status()
            return dict(r.json())
        except (httpx.HTTPError, ValueError):
            time.sleep(1 + attempt)
    return {}


def _conversations(c: httpx.Client, acc: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for page in range(1, 400):
        d = _get(
            c,
            f"/api/v1/accounts/{acc}/conversations",
            status="all",
            assignee_type="all",
            page=page,
        )
        fresh = [x for x in d.get("data", {}).get("payload", []) if x.get("id") not in seen]
        if not fresh:
            break
        seen.update(x["id"] for x in fresh)
        out.extend(fresh)
    return out


def _messages(c: httpx.Client, acc: str, conv_id: int) -> list[dict[str, Any]]:
    """Chatwoot entrega de a 20, los mas recientes primero: se pagina con before=."""
    msgs: dict[int, dict[str, Any]] = {}
    before: int | None = None
    for _ in range(100):
        params = {"before": before} if before else {}
        page = _get(c, f"/api/v1/accounts/{acc}/conversations/{conv_id}/messages", **params).get(
            "payload", []
        )
        new = [m for m in page if m.get("id") not in msgs]
        if not new:
            break
        msgs.update((m["id"], m) for m in new)
        if len(page) < 20:
            break
        before = min(m["id"] for m in page)
    return sorted(msgs.values(), key=lambda m: (m.get("created_at") or 0, m["id"]))


def _is_agent(m: dict[str, Any]) -> bool:
    return (
        m.get("message_type") == 1
        and not m.get("private")
        and (m.get("sender") or {}).get("type") == "user"
        and not (m.get("content_attributes") or {}).get("bot_mirror")
        and not (m.get("content_attributes") or {}).get("deleted")
        and bool(m.get("content") or m.get("attachments"))
    )


def lost_in(msgs: list[dict[str, Any]], since: float = 0) -> list[dict[str, Any]]:
    """Mensajes del agente perdidos por la ventana, en orden."""
    incoming = [m["created_at"] for m in msgs if m.get("message_type") == 0 and m.get("created_at")]
    last_in = max(incoming) if incoming else None
    queued_after = [
        m["created_at"]
        for m in msgs
        if m.get("private") and str(m.get("content") or "").startswith(QUEUED_NOTE)
    ]
    out = []
    for m in msgs:
        t = m.get("created_at") or 0
        if t < since or not _is_agent(m) or (last_in is not None and t < last_in):
            continue  # el cliente escribio despues: la conversacion siguio
        if last_in is not None and t - last_in < DAY:
            continue  # dentro de la ventana: llego
        if any(q >= t for q in queued_after):
            continue  # ya lo guardo el flujo nuevo
        out.append(m)
    return out


def buscar(desde: str) -> None:
    since = datetime.fromisoformat(desde).replace(tzinfo=COT).timestamp()
    acc = os.environ["CHATWOOT_ACCOUNT_ID"]
    c = httpx.Client(
        base_url=os.environ["CHATWOOT_URL"],
        headers={"api_access_token": os.environ["CHATWOOT_API_KEY"]},
        timeout=60,
    )
    convs = _conversations(c, acc)
    print(f"{len(convs)} conversaciones")
    found = []
    for i, conv in enumerate(convs, 1):
        phone = ((conv.get("meta") or {}).get("sender") or {}).get("phone_number")
        if not phone:
            continue
        lost = lost_in(_messages(c, acc, conv["id"]), since)
        if lost:
            found.append(
                {
                    "conv_id": conv["id"],
                    "phone": phone,
                    "messages": [
                        {
                            "id": m["id"],
                            "created_at": m["created_at"],
                            "content": m.get("content") or "",
                            "attachments": m.get("attachments") or [],
                            "agent": (m.get("sender") or {}).get("name"),
                        }
                        for m in lost
                    ],
                }
            )
        if i % 100 == 0:
            print(f"  {i}/{len(convs)}", flush=True)
    OUT.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(f["messages"]) for f in found)
    print(f"\n{n} mensajes perdidos en {len(found)} conversaciones -> {OUT.name}\n")
    for f in sorted(found, key=lambda f: f["messages"][-1]["created_at"], reverse=True):
        for m in f["messages"]:
            when = datetime.fromtimestamp(m["created_at"], COT).strftime("%d-%b %H:%M")
            adj = f" [+{len(m['attachments'])} adj]" if m["attachments"] else ""
            text = m["content"].replace("\n", " ")[:70]
            print(f"  #{f['conv_id']:<5} {when}  {m['agent'] or '?':<16.16} {text}{adj}")


async def enviar(dry: bool) -> None:
    import redis.asyncio as aioredis

    from app.features.escalation.window import relay_or_queue
    from app.integrations.chatwoot import get_chatwoot_client
    from app.integrations.meta_cloud import get_meta_client
    from app.webhooks.chatwoot import _relay_attachments

    found = json.loads(OUT.read_text(encoding="utf-8"))
    done = set(SENT_LOG.read_text().split()) if SENT_LOG.exists() else set()
    todo = [(f, m) for f in found for m in f["messages"] if str(m["id"]) not in done]
    print(f"{len(todo)} mensajes por reenviar ({len(done)} ya reenviados)")
    if dry:
        for f, m in todo:
            print(f"  #{f['conv_id']} {m['content'][:60]!r}")
        return

    redis = aioredis.from_url(os.environ["REDIS_URL"])
    chatwoot = get_chatwoot_client()
    chatwoot._redis = redis
    meta = get_meta_client()
    ok = fail = 0
    with SENT_LOG.open("a") as log:
        for f, m in todo:
            phone, content, atts = f["phone"], m["content"], m["attachments"]

            async def send(
                phone: str = phone, content: str = content, atts: list[Any] = atts
            ) -> list[str]:
                if atts:
                    return await _relay_attachments(
                        atts, phone=phone, content=content, chatwoot=chatwoot, meta=meta
                    )
                return [await meta.send_text(phone, content)]

            try:
                outcome = await relay_or_queue(
                    redis=redis,
                    chatwoot=chatwoot,
                    meta=meta,
                    phone=phone,
                    conv_id=int(f["conv_id"]),
                    content=content,
                    attachments=atts,
                    msg_id=f"cw-resend-{m['id']}",
                    send=send,
                )
                log.write(f"{m['id']}\n")
                log.flush()
                ok += 1
                print(f"  #{f['conv_id']} {outcome}")
            except Exception as exc:  # noqa: BLE001 — un fallo no frena el lote
                fail += 1
                print(f"  FALLO #{f['conv_id']}: {type(exc).__name__} {str(exc)[:100]}")
    await redis.aclose()
    print(f"listo: ok={ok} fail={fail}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("buscar")
    # Los masivos de "ARIA DPG" del 10 y 12-ago tambien cayeron fuera de
    # ventana (430), pero reenviarlos semanas despues no tiene sentido.
    b.add_argument("--desde", default="2026-09-01")
    e = sub.add_parser("enviar")
    e.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if a.cmd == "buscar":
        buscar(a.desde)
    else:
        asyncio.run(enviar(a.dry_run))


if __name__ == "__main__":
    main()
