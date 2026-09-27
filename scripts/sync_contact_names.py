"""One-off: poblar el nombre de los contactos de Chatwoot desde SoftSeguros.

Los contactos creados por el bot salen con solo `phone_number` + `identifier`
(ver ChatwootClient._create_or_get_contact), asi que cartera ve un numero pelado
en el inbox. Este script les pone el nombre que ya tenemos en SoftSeguros.

Fuente de nombres: el barrido crudo que dejo broadcast_conversatorio.py
(<csv>.raw.jsonl). NO vuelve a crawlear SoftSeguros.

  # ver que haria, sin escribir nada:
  python scripts/sync_contact_names.py segmento.csv.raw.jsonl --dry-run

  # aplicar:
  python scripts/sync_contact_names.py segmento.csv.raw.jsonl

Solo rellena contactos SIN nombre. Nunca pisa un nombre existente: puede que un
agente haya escrito a mano algo mejor. Reanudable: los ya escritos quedan en
<raw>.synced.log y se saltan al reintentar.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from pathlib import Path

import httpx

TIMEOUT = 30


def _phone(raw: str | None) -> str | None:
    """Normaliza a E.164 colombiano, o None si no es un celular usable.

    Mismo criterio que broadcast_conversatorio.py: los datos vienen sucios
    ("+57 +573127673268", "7497000 GLORIA INES", "32158730000") y solo pasan
    los que quedan en 10 digitos empezando por 3.
    """
    d = re.sub(r"\D", "", raw or "")
    while len(d) > 10 and d.startswith("57"):  # colapsa prefijos 57 repetidos
        d = d[2:]
    if len(d) == 10 and d.startswith("3"):
        return "+57" + d
    return None


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    return re.sub(r"\s+", " ", s).strip()


def _is_unnamed(contact: dict) -> bool:
    """True si el contacto no tiene nombre real (vacio o el propio numero)."""
    name = (contact.get("name") or "").strip()
    phone = (contact.get("phone_number") or "").strip()
    return not name or name == phone or name.lstrip("+").isdigit()


def load_names(raw_path: Path) -> dict[str, str]:
    """telefono -> nombre, solo donde SoftSeguros es inequivoco.

    Un mismo celular puede aparecer con dos titulares distintos (lineas
    compartidas de hogar o de negocio). Escribir el nombre equivocado en el
    hilo de un deudor es peor que dejar el numero, asi que esos se descartan.
    """
    seen: dict[str, set[str]] = {}
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001, S112 — linea corrupta: se rebaja
            continue
        for x in rec.get("rows", []):
            phone = _phone(x.get("cel"))
            name = (x.get("nombre") or "").strip()
            if phone and name:
                seen.setdefault(phone, set()).add(_norm(name))
    return {p: next(iter(ns)) for p, ns in seen.items() if len(ns) == 1}


def fetch_contacts(client: httpx.Client, account: str) -> list[dict]:
    """Todas las paginas de /contacts."""
    out: list[dict] = []
    page = 1
    while True:
        r = client.get(f"/api/v1/accounts/{account}/contacts", params={"page": page})
        r.raise_for_status()
        payload = r.json().get("payload") or []
        if not payload:
            return out
        out.extend(payload)
        page += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", type=Path, help="<csv>.raw.jsonl del barrido de SoftSeguros")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    account = os.environ["CHATWOOT_ACCOUNT_ID"]
    client = httpx.Client(
        base_url=os.environ["CHATWOOT_URL"],
        headers={"api_access_token": os.environ["CHATWOOT_API_KEY"]},
        timeout=TIMEOUT,
    )

    names = load_names(a.raw)
    print(f"{len(names)} telefonos con nombre inequivoco en SoftSeguros")

    synced_log = a.raw.with_suffix(a.raw.suffix + ".synced.log")
    done = set(synced_log.read_text().split()) if synced_log.exists() else set()

    contacts = fetch_contacts(client, account)
    unnamed = [c for c in contacts if _is_unnamed(c)]
    todo = [
        (c, names[p])
        for c in unnamed
        if (p := _phone(c.get("phone_number"))) and p in names and str(c["id"]) not in done
    ]
    print(
        f"{len(contacts)} contactos, {len(unnamed)} sin nombre, "
        f"{len(todo)} con nombre disponible ({len(done)} ya sincronizados)"
    )

    if a.dry_run:
        for c, name in todo[:20]:
            print(f"  {c['id']}  {c.get('phone_number')}  -> {name}")
        if len(todo) > 20:
            print(f"  ... y {len(todo) - 20} mas")
        return

    ok = fail = 0
    with synced_log.open("a") as log:
        for i, (c, name) in enumerate(todo, 1):
            try:
                r = client.put(
                    f"/api/v1/accounts/{account}/contacts/{c['id']}", json={"name": name}
                )
                r.raise_for_status()
                log.write(f"{c['id']}\n")
                log.flush()
                ok += 1
            except Exception as exc:  # noqa: BLE001 — seguir con el resto
                fail += 1
                print(f"FAIL {c['id']}: {exc}")
            if i % 50 == 0:
                print(f"{i}/{len(todo)} ok={ok} fail={fail}")
    print(f"listo: ok={ok} fail={fail}")


if __name__ == "__main__":
    main()
