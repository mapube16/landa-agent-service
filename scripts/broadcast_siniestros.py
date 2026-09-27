"""One-off: las dos campanas del terremoto del 10-ago-2026 (textos DPG, 18-sep).

  seguimiento  -> lista_terremoto.csv (154 con reclamacion abierta)
  deteccion    -> lista_sin_info.csv  (309 con poliza vigente, sin informacion)

Al enviar, la conversacion queda marcada en Chatwoot con la etiqueta de la
campana (``siniestro-sismo`` / ``deteccion-sismo``): es lo que le dice al
enrutador (``app/features/siniestros/routing.py``) que clasificador aplicar
cuando el cliente responda.

  python scripts/broadcast_siniestros.py status
  python scripts/broadcast_siniestros.py send seguimiento lista_terremoto.csv --dry-run
  python scripts/broadcast_siniestros.py send seguimiento lista_terremoto.csv --limit 10
  python scripts/broadcast_siniestros.py send deteccion lista_sin_info.csv

Reanudable: los enviados quedan en <csv>.<campana>.sent.log. Una persona con
varias polizas recibe UN mensaje.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LANG = "es"
HERE = Path(__file__).parent


@dataclass(frozen=True)
class Campana:
    template: str
    body_file: str
    marker: str

    @property
    def body(self) -> str:
        return (HERE / self.body_file).read_text(encoding="utf-8").strip()

    @property
    def mirror_text(self) -> str:
        # La plantilla no pasa por el dispatcher que espeja los envios: aca se
        # registra su contenido para que DPG vea que se le mando.
        return f"[Campana {self.marker}] Plantilla enviada:\n\n{self.body}"


CAMPANAS: dict[str, Campana] = {
    "seguimiento": Campana(
        "seguimiento_siniestro_sismo_v2", "broadcast_siniestros_body.txt", "siniestro-sismo"
    ),
    "deteccion": Campana(
        "deteccion_siniestro_sismo", "broadcast_deteccion_body.txt", "deteccion-sismo"
    ),
}


def status() -> None:
    import httpx

    waba = os.environ["WA_BUSINESS_ACCOUNT_ID"]
    r = httpx.get(
        f"https://graph.facebook.com/v21.0/{waba}/message_templates",
        params={"limit": 50},
        headers={"Authorization": f"Bearer {os.environ['WA_TOKEN']}"},
        timeout=60,
    )
    wanted = {c.template for c in CAMPANAS.values()}
    for t in r.json().get("data", []):
        if t["name"] in wanted:
            print(f"{t['name']} [{t.get('language')}] -> {t.get('status')}")
            if t.get("rejected_reason") and t["rejected_reason"] != "NONE":
                print("  motivo:", t["rejected_reason"])


def _people(csv_path: Path) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for r in csv.DictReader(csv_path.open(encoding="utf-8-sig")):
        if r.get("phone"):
            seen.setdefault(r["phone"], r)
    return list(seen.values())


async def send(camp: Campana, csv_path: Path, limit: int | None, dry: bool) -> None:
    people = _people(csv_path)
    sent_log = csv_path.with_suffix(f"{csv_path.suffix}.{camp.marker}.sent.log")
    done = set(sent_log.read_text().split()) if sent_log.exists() else set()
    todo = [p for p in people if p["phone"] not in done]
    if limit:
        todo = todo[:limit]
    print(f"{len(people)} personas | {len(done)} ya enviados | {len(todo)} en este lote")

    if dry:
        for p in todo[:10]:
            print(f"  {p['phone']}  {p.get('asegurado', '')[:44]}")
        print(f"\nplantilla: {camp.template} [{LANG}] — {len(camp.body)} chars, sin variables")
        print(f"marca en Chatwoot: {camp.marker}")
        return

    from app.integrations.meta_cloud import get_meta_client  # tarda: solo al enviar

    meta = get_meta_client()
    ok = fail = 0
    with sent_log.open("a") as log:
        for i, p in enumerate(todo, 1):
            phone = p["phone"]
            try:
                await meta.send_template(phone, camp.template, LANG, body_params=[])
                log.write(phone + "\n")
                log.flush()
                ok += 1
                await _mirror_and_mark(phone, camp)
            except Exception as exc:  # noqa: BLE001 — un fallo no frena el lote
                fail += 1
                print(f"  FALLO {phone}: {type(exc).__name__} {str(exc)[:110]}")
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}  ok={ok} fail={fail}", flush=True)
    print(f"listo: ok={ok} fail={fail}")


async def _mirror_and_mark(phone: str, camp: Campana) -> None:
    """Espeja el envio y marca la conversacion con la campana. Fail-open."""
    try:
        from app.features.payment.nodes import mirror_outgoing_to_chatwoot
        from app.integrations.chatwoot import get_chatwoot_client

        await mirror_outgoing_to_chatwoot(phone, camp.mirror_text)
        client = get_chatwoot_client()
        conv_id = await client.get_or_create_conversation(phone)
        await client.add_labels(conv_id, [camp.marker])
    except Exception as exc:  # noqa: BLE001
        print(f"  (sin espejo/marca) {phone}: {type(exc).__name__}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("send")
    s.add_argument("campana", choices=sorted(CAMPANAS))
    s.add_argument("csv", type=Path)
    s.add_argument("--limit", type=int)
    s.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.cmd == "status":
        status()
    else:
        asyncio.run(send(CAMPANAS[a.campana], a.csv, a.limit, a.dry_run))


if __name__ == "__main__":
    main()
