"""One-off: broadcast de plantilla Meta a pólizas por ramo (conversatorio 11-sep).

Dos pasos, ambos con el entorno del servicio (railway run -s landa-agent-service):

  1. Armar segmento (solo lectura SoftSeguros, escribe CSV, no envía nada):
     python scripts/broadcast_conversatorio.py segment segmento.csv
     Imprime conteo por ramo (incluye los ramos NO matcheados para verificar nombres).

  1b. Crear la plantilla en Meta (header IMAGE + cuerpo + boton URL). Requiere
     WA_BUSINESS_ACCOUNT_ID y que el token tenga whatsapp_business_management:
     python scripts/broadcast_conversatorio.py template image.png --name conversatorio_reclamaciones
     Luego consultar estado hasta APPROVED:
     python scripts/broadcast_conversatorio.py status --name conversatorio_reclamaciones

  2. Enviar (requiere plantilla APROBADA en Meta con header IMAGE, sin variables):
     python scripts/broadcast_conversatorio.py send segmento.csv image.png \
         --template conversatorio_reclamaciones --lang es [--limit 5]
     Reanudable: los enviados quedan en <csv>.sent.log y se saltan al reintentar.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # correr sin PYTHONPATH

RAMOS = frozenset({"HOGAR", "PYME", "COPROPIEDADES", "COPROPIEDAD"})
CONC = 20
PAGE = 500
TEAMS_URL = "https://teams.microsoft.com/meet/238625653412800?p=yhMeNOfcsTrR9ysJQ8"
# Lo que ve cartera en Chatwoot: la plantilla no viaja por el dispatcher que
# espeja los envios, asi que aca se registra su contenido + el link del boton.
MIRROR_TEXT = (
    "[Campana conversatorio 11-sep] Plantilla enviada:\n\n"
    + (Path(__file__).with_name("broadcast_conversatorio_body.txt"))
    .read_text(encoding="utf-8")
    .strip()
    + f"\n\nBoton: Conectarme -> {TEAMS_URL}"
)


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def _phone(raw: str | None) -> str | None:
    """Normaliza a E.164 colombiano, o None si no es un celular usable.

    Datos reales sucios: "3116142119", "+57 302 8129461", "316-7008133",
    "+57 +573127673268" (prefijo duplicado), "32158730000" (11 digitos, malo),
    "7497000 GLORIA INES ZULUAGA" (texto libre, es un fijo). Solo pasan los
    que terminan en 10 digitos empezando por 3.
    """
    d = re.sub(r"\D", "", raw or "")
    while len(d) > 10 and d.startswith("57"):  # colapsa prefijos 57 repetidos
        d = d[2:]
    if len(d) == 10 and d.startswith("3"):
        return "+57" + d
    return None


async def segment(out: Path) -> None:  # noqa: C901 — script one-off
    """Barre api/poliza/ completo (5424 paginas de 10) y filtra client-side.

    La API ignora todo filtro server-side (ramo, estado, page_size). Unico
    parametro real: page=N. Orden id ASC estable. Concurrencia 20 = ~60 min.
    Reanudable: cada pagina cruda se guarda en <out>.raw.jsonl.
    """
    import json as _json

    import httpx

    base = os.environ["SOFTSEGUROS_BASE_URL"]
    raw_path = out.with_suffix(out.suffix + ".raw.jsonl")
    done: set[int] = set()
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(_json.loads(line)["page"])
            except Exception:  # noqa: BLE001, S110 — linea corrupta: se rebaja
                pass
        print(f"reanudando: {len(done)} paginas ya bajadas")

    async with httpx.AsyncClient(base_url=base, timeout=120) as c:
        tok = (
            await c.post(
                "api-token-auth/",
                json={
                    "username": os.environ["SOFTSEGUROS_USERNAME"],
                    "password": os.environ["SOFTSEGUROS_PASSWORD"],
                },
            )
        ).json()["token"]
        h = {"Authorization": "Token " + tok}
        first = (await c.get("api/poliza/", params={"page": 1}, headers=h)).json()
        total = int(first.get("count", 0))
        last = total // 10 + 2
        print(f"count={total} -> {last} paginas, concurrencia {CONC}")

        sem = asyncio.Semaphore(CONC)
        hits = [0]

        async def grab(page: int) -> None:
            if page in done:
                return
            async with sem:
                for _ in range(4):
                    try:
                        r = await c.get("api/poliza/", params={"page": page}, headers=h)
                        if r.status_code == 200:
                            res = r.json().get("results", [])
                            keep = [
                                {
                                    "ramo": x.get("ramo_nombre"),
                                    "estado": x.get("estado_poliza_nombre"),
                                    "cel": x.get("cliente_celular"),
                                    "nombre": (
                                        f"{x.get('cliente_nombres') or ''} "
                                        f"{x.get('cliente_apellidos') or ''}"
                                    ).strip(),
                                    "poliza": x.get("numero_poliza"),
                                    "id": x.get("id"),
                                }
                                for x in res
                                if _norm(x.get("ramo_nombre")) in RAMOS
                            ]
                            with raw_path.open("a", encoding="utf-8") as f:
                                f.write(_json.dumps({"page": page, "rows": keep}) + "\n")
                            hits[0] += len(keep)
                            return
                    except Exception:  # noqa: BLE001
                        await asyncio.sleep(1)

        pending = [p for p in range(1, last + 1) if p not in done]
        for start in range(0, len(pending), 400):
            batch = pending[start : start + 400]
            await asyncio.gather(*[grab(p) for p in batch])
            hechas = len(done) + start + len(batch)
            print(f"  {hechas}/{last} paginas, {hits[0]} filas del target")

    # ---- consolidar ----
    seen: dict[str, dict[str, str]] = {}
    descartes: list[str] = []
    ramos_hit: Counter[str] = Counter()
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = _json.loads(line)
        except Exception:  # noqa: BLE001, S112 — linea corrupta: se rebaja
            continue
        for x in rec.get("rows", []):
            if _norm(x.get("estado")) != "VIGENTE":
                continue
            ph = _phone(x.get("cel"))
            if not ph:
                if (x.get("cel") or "").strip():
                    descartes.append(f"{x.get('poliza')}	{x.get('cel')}")
                continue
            ramos_hit[_norm(x.get("ramo"))] += 1
            seen.setdefault(
                ph,
                {
                    "phone": ph,
                    "nombre": x.get("nombre") or "",
                    "ramo": x.get("ramo") or "",
                    "poliza": x.get("poliza") or "",
                },
            )

    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["phone", "nombre", "ramo", "poliza"])
        w.writeheader()
        w.writerows(seen.values())
    if descartes:
        bad = out.with_suffix(out.suffix + ".descartados.txt")
        bad.write_text("\n".join(descartes), encoding="utf-8")
        print(f"{len(descartes)} celulares no parseables -> {bad}")
    print(f"\n{len(seen)} contactos unicos VIGENTES -> {out}")
    for r_, n in ramos_hit.most_common():
        print(f"  {n:6d}  {r_}")


async def _mirror(phone: str) -> None:
    """Espeja el envio en Chatwoot para que cartera tenga contexto de la respuesta.

    El script llama a Meta directo (fuera del dispatcher del webhook, que es
    quien normalmente espeja), asi que sin esto el equipo ve la respuesta del
    cliente sin ver que se le mando. Fail-open: Chatwoot caido no frena el envio.
    """
    from app.features.payment.nodes import mirror_outgoing_to_chatwoot

    await mirror_outgoing_to_chatwoot(phone, MIRROR_TEXT)


async def backfill(csv_path: Path) -> None:
    """Espeja en Chatwoot los envios que ya salieron sin espejo (los 20 del lote 1)."""
    sent_log = csv_path.with_suffix(csv_path.suffix + ".sent.log")
    mirrored_log = csv_path.with_suffix(csv_path.suffix + ".mirrored.log")
    sent = [p for p in sent_log.read_text().split() if p]
    done = set(mirrored_log.read_text().split()) if mirrored_log.exists() else set()
    faltan = [p for p in sent if p not in done]
    print(f"{len(faltan)} por espejar de {len(sent)} enviados")
    ok = 0
    with mirrored_log.open("a") as log:
        for p in faltan:
            await _mirror(p)
            log.write(p + "\n")
            log.flush()
            ok += 1
    print(f"espejados: {ok}")


async def send(csv_path: Path, image: Path, template: str, lang: str, limit: int | None) -> None:
    from app.integrations.meta_cloud import get_meta_client

    meta = get_meta_client()
    sent_log = csv_path.with_suffix(csv_path.suffix + ".sent.log")
    done = set(sent_log.read_text().split()) if sent_log.exists() else set()
    rows = [r for r in csv.DictReader(csv_path.open(encoding="utf-8")) if r["phone"] not in done]
    if limit:
        rows = rows[:limit]
    print(f"{len(rows)} por enviar ({len(done)} ya enviados). Subiendo imagen...")
    media_id = await meta.upload_media(image, "image/png")
    mirrored_log = csv_path.with_suffix(csv_path.suffix + ".mirrored.log")
    ok = fail = 0
    with sent_log.open("a") as log, mirrored_log.open("a") as mlog:
        for i, r in enumerate(rows, 1):
            try:
                await meta.send_template(
                    r["phone"], template, lang, body_params=[], header_image_id=media_id
                )
                log.write(r["phone"] + "\n")
                log.flush()
                ok += 1
                # El envio YA salio: un fallo de espejo no debe reintentarlo ni
                # contarse como fail. Queda pendiente para el subcomando backfill.
                try:
                    await _mirror(r["phone"])
                    mlog.write(r["phone"] + "\n")
                    mlog.flush()
                except Exception as exc:  # noqa: BLE001
                    print(f"MIRROR-FAIL {r['phone']}: {exc}")
            except Exception as exc:  # noqa: BLE001 — seguir con el resto
                fail += 1
                print(f"FAIL {r['phone']}: {exc}")
            if i % 50 == 0:
                print(f"{i}/{len(rows)} ok={ok} fail={fail}")
            await asyncio.sleep(0.1)  # ponytail: ~10 msg/s; bajar si Meta devuelve 130429
    print(f"listo: ok={ok} fail={fail}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("segment")
    s.add_argument("out", type=Path)
    b = sub.add_parser("backfill")
    b.add_argument("csv", type=Path)
    e = sub.add_parser("send")
    e.add_argument("csv", type=Path)
    e.add_argument("image", type=Path)
    e.add_argument("--template", required=True)
    e.add_argument("--lang", default="es")
    e.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.cmd == "segment":
        asyncio.run(segment(a.out))
    elif a.cmd == "backfill":
        asyncio.run(backfill(a.csv))
    else:
        asyncio.run(send(a.csv, a.image, a.template, a.lang, a.limit))


if __name__ == "__main__":
    main()
