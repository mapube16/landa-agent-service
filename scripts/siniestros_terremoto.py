"""One-off: polizas con siniestro (filtro 'Tiene siniestros: Si' de la UI).

Mismo entorno que broadcast_conversatorio (railway run -s landa-agent-service):

  python scripts/siniestros_terremoto.py siniestros.csv [--tiene 1] [--cruce segmento.csv]

Solo lectura. Escribe:
  <out>            polizas de HOGAR/PYME/COPROPIEDAD con siniestro (todas las estados)
  <out>.raw.jsonl  filas crudas completas, para ver que campos de siniestro trae la API
  <out>.cruce.csv  si --cruce: las de <out> que estan/no estan en el segmento (Lista A vs B)

El endpoint es api/poliza/ pero SOLO filtra server-side con
filterType=searchGeneral&busqueda_por=searchGeneral&sede=1047 (curl de la UI, 2026-09-14).
tiene_siniestro: -1 = todas, 1 = si (verificar con el count impreso), 0 = no.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.broadcast_conversatorio import RAMOS, _norm, _phone  # noqa: E402

BASE_PARAMS = {
    "filterType": "searchGeneral",
    "busqueda_por": "searchGeneral",
    "sede": "1047",
    "texto_busqueda": "",
    "tipo_moneda": "-1",
    "mostrar_renovable": "todas",
    "beneficiario_oneroso": "-1",
    "poliza_nueva_renovada": "-1",
    "con_pagos": "-1",
    "tiene_remision": "-1",
    "con_archivos": "-1",
    "con_co_corretaje": "-1",
    "with_override_commission": "-1",
    "poliza_sincronizada": "-1",
    "ghl_sync_status": "-1",
    "fecha_a_buscar": "fecha_inicio",
    "order_by": "fecha_inicio",
    "sort_by": "asc",
}


async def fetch(out: Path, tiene: str) -> None:
    import httpx

    base = os.environ["SOFTSEGUROS_BASE_URL"]
    raw = out.with_suffix(out.suffix + ".raw.jsonl")
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
        params = {**BASE_PARAMS, "tiene_siniestro": tiene, "page": 1}
        first = (await c.get("api/poliza/", params=params, headers=h)).json()
        total = int(first.get("count", 0))
        rows = first.get("results", [])
        print(f"tiene_siniestro={tiene}: count={total} (si es ~6406 el filtro NO aplico)")
        if rows:
            sin = sorted(k for k in rows[0] if "siniestr" in k.lower())
            print("campos con 'siniestr' en la fila:", sin or "NINGUNO")
        pages = total // max(len(rows), 1) + 1
        with raw.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            for page in range(2, pages + 1):
                r = await c.get("api/poliza/", params={**params, "page": page}, headers=h)
                for x in r.json().get("results", []):
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
                print(f"  pagina {page}/{pages}")

    # ponytail: filtro por ramo en cliente, igual que segment(); ids de r[] no los conocemos
    keep = 0
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["poliza", "id", "nombre", "phone", "cel_raw", "ramo", "estado", "ciudad"]
        )
        w.writeheader()
        for line in raw.read_text(encoding="utf-8").splitlines():
            x = json.loads(line)
            if _norm(x.get("ramo_nombre")) not in RAMOS:
                continue
            keep += 1
            w.writerow(
                {
                    "poliza": x.get("numero_poliza"),
                    "id": x.get("id"),
                    "nombre": " ".join(
                        filter(None, (x.get("cliente_nombres"), x.get("cliente_apellidos")))
                    ).strip(),
                    "phone": _phone(x.get("cliente_celular")) or "",
                    "cel_raw": x.get("cliente_celular") or "",
                    "ramo": x.get("ramo_nombre"),
                    "estado": x.get("estado_poliza_nombre"),
                    "ciudad": x.get("cliente_ciudad") or "",
                }
            )
    print(f"{keep} polizas de {sorted(RAMOS)} con siniestro -> {out}")


def cruce(out: Path, segmento: Path) -> None:
    seg = {r["poliza"]: r for r in csv.DictReader(segmento.open(encoding="utf-8"))}
    con = list(csv.DictReader(out.open(encoding="utf-8")))
    a = {r["poliza"] for r in con}
    lista_a = [r for r in con]
    lista_b = [r for p, r in seg.items() if p not in a]
    dst = out.with_suffix(".cruce.csv")
    with dst.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lista", "poliza", "nombre", "phone", "ramo", "estado"])
        for r in lista_a:
            w.writerow(["A", r["poliza"], r["nombre"], r["phone"], r["ramo"], r["estado"]])
        for r in lista_b:
            w.writerow(["B", r["poliza"], r["nombre"], r["phone"], r["ramo"], "Vigente"])
    en_seg = sum(1 for r in lista_a if r["poliza"] in seg)
    print(
        f"Lista A: {len(lista_a)} ({en_seg} estaban en el segmento)  "
        f"Lista B: {len(lista_b)} -> {dst}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--tiene", default="1", help="1=si, 0=no, -1=todas")
    ap.add_argument("--cruce", type=Path, help="segmento.csv para separar Lista A / B")
    a = ap.parse_args()
    asyncio.run(fetch(a.out, a.tiene))
    if a.cruce:
        cruce(a.out, a.cruce)


if __name__ == "__main__":
    main()
