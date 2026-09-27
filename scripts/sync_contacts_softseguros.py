"""One-off: poblar Chatwoot con TODOS los contactos de SoftSeguros + atributos.

Hermano mayor de sync_contact_names.py (que solo rellenaba nombres de los
contactos que el bot ya habia creado). Este crea los que faltan y escribe los
6 atributos personalizados para que DPG pueda segmentar desde Chatwoot.

Los 6 atributos hay que crearlos ANTES a mano en Chatwoot (Configuracion ->
Atributos personalizados -> Contacto), tipo Texto, con estas claves exactas:
  polizas, ramos, estado_poliza, vence, tiene_siniestro
El email va al campo nativo del contacto: Chatwoot reserva esa clave.

  # ver que haria, sin escribir nada:
  python scripts/sync_contacts_softseguros.py all_polizas.json --dry-run

  # aplicar:
  python scripts/sync_contacts_softseguros.py all_polizas.json [--limit 50]

Entrada: el barrido crudo de api/poliza/ (lista JSON de filas con los campos
cliente_*, ramo_nombre, aseguradora_nombre, estado_poliza_nombre,
siniestro_poliza). Una persona = un contacto: las polizas se agrupan por
telefono, no una fila por poliza.

Reanudable: los telefonos ya escritos quedan en <raw>.contacts.log.
Nombres: por defecto NO pisa un nombre existente (un agente pudo escribir algo
mejor a mano). --overwrite-names para que SoftSeguros mande siempre.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sync_contact_names import _is_unnamed, _norm, _phone, fetch_contacts  # noqa: E402

TIMEOUT = 30
ATTRS = ("polizas", "ramos", "estado_poliza", "vence", "tiene_siniestro")


def _email(raw: str | None) -> str:
    """Email plausible, o "" si no lo es.

    SoftSeguros trae basura en el campo: ".", "no tiene", "N/A", espacios.
    Chatwoot rechaza el contacto entero con 422 si el email no le gusta.
    """
    e = (raw or "").strip().lower()
    return e if re.fullmatch(r"[^@\s]+@[^@\s.]+\.[^@\s]{2,}", e) else ""


def _join(values: list[str], limit: int = 8) -> str:
    """Valores unicos en orden de aparicion, recortados: Chatwoot no anida.

    Un cliente con 30 polizas haria el atributo ilegible en la UI y romperia
    el filtro por texto, asi que se corta y se marca el resto.
    """
    uniq = list(dict.fromkeys(v for v in values if v))
    head = uniq[:limit]
    if len(uniq) > limit:
        head.append(f"+{len(uniq) - limit}")
    return ", ".join(head)


def build_people(rows: list[dict]) -> dict[str, dict]:
    """telefono -> {name, attrs}. Agrupa las polizas de una misma persona."""
    by_phone: dict[str, list[dict]] = defaultdict(list)
    for x in rows:
        if phone := _phone(x.get("cliente_celular")):
            by_phone[phone].append(x)

    people: dict[str, dict] = {}
    for phone, polizas in by_phone.items():
        names = {
            _norm(f"{x.get('cliente_nombres') or ''} {x.get('cliente_apellidos') or ''}")
            for x in polizas
        }
        names.discard("")
        # ponytail: mismo criterio que sync_contact_names — celular compartido
        # por dos titulares distintos no se nombra, pero si se crea el contacto.
        name = next(iter(names)) if len(names) == 1 else ""
        vigentes = [x for x in polizas if _norm(x.get("estado_poliza_nombre")) == "VIGENTE"]
        base = vigentes or polizas
        people[phone] = {
            "name": name,
            # aseguradora_nombre y cliente_ciudad vienen NULL en el listado
            # (0/54145); el email va al campo nativo, que Chatwoot reserva.
            "email": next((e for x in base if (e := _email(x.get("cliente_email")))), ""),
            "attrs": {
                "polizas": _join([str(x.get("numero_poliza") or "") for x in base]),
                # _norm: SoftSeguros trae "PYME" y "pyme" mezclados y el filtro
                # de Chatwoot es sensible a mayusculas.
                "ramos": _join([_norm(x.get("ramo_nombre")) for x in base]),
                "estado_poliza": (
                    "Vigente" if vigentes else (polizas[0].get("estado_poliza_nombre") or "")
                ),
                "vence": max((x.get("fecha_fin") or "" for x in base), default=""),
                "tiene_siniestro": (
                    "Si" if any(_norm(x.get("siniestro_poliza")) == "SI" for x in polizas) else "No"
                ),
            },
        }
    return people


def main() -> None:  # noqa: C901 — script one-off
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", type=Path, help="JSON con las filas crudas de api/poliza/")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="tope de contactos a escribir (prueba)")
    ap.add_argument("--overwrite-names", action="store_true", help="SoftSeguros pisa el nombre")
    a = ap.parse_args()

    account = os.environ["CHATWOOT_ACCOUNT_ID"]
    inbox = int(os.environ["CHATWOOT_INBOX_ID"])
    client = httpx.Client(
        base_url=os.environ["CHATWOOT_URL"],
        headers={"api_access_token": os.environ["CHATWOOT_API_KEY"]},
        timeout=TIMEOUT,
    )

    people = build_people(json.loads(a.raw.read_text(encoding="utf-8")))
    print(f"{len(people)} personas con celular usable en SoftSeguros")

    log_path = a.raw.with_suffix(a.raw.suffix + ".contacts.log")
    done = set(log_path.read_text().split()) if log_path.exists() else set()

    existing = {
        p: c for c in fetch_contacts(client, account) if (p := _phone(c.get("phone_number")))
    }
    print(f"{len(existing)} contactos ya en Chatwoot, {len(done)} ya sincronizados")

    todo = [(p, v) for p, v in people.items() if p not in done]
    if a.limit:
        todo = todo[: a.limit]
    crear = sum(1 for p, _ in todo if p not in existing)
    print(f"{len(todo)} a procesar: {crear} nuevos, {len(todo) - crear} actualizaciones")

    if a.dry_run:
        for p, v in todo[:15]:
            estado = "NUEVO" if p not in existing else "update"
            print(f"  {p}  {estado}  {v['name'] or '(sin nombre)'}")
            print(f"     {v['attrs']}")
        return

    ok = fail = 0
    with log_path.open("a") as log:
        for i, (phone, v) in enumerate(todo, 1):
            payload: dict = {"custom_attributes": v["attrs"]}
            if v["email"]:
                payload["email"] = v["email"]
            try:
                if (c := existing.get(phone)) is None:
                    payload |= {
                        "inbox_id": inbox,
                        "phone_number": phone,
                        "identifier": phone.lstrip("+"),
                        "name": v["name"] or phone,
                    }
                    r = client.post(f"/api/v1/accounts/{account}/contacts", json=payload)
                else:
                    if v["name"] and (a.overwrite_names or _is_unnamed(c)):
                        payload["name"] = v["name"]
                    r = client.put(f"/api/v1/accounts/{account}/contacts/{c['id']}", json=payload)
                # Un email repetido entre dos titulares (pasa en SoftSeguros)
                # tumba el contacto entero. El contacto importa mas que el email.
                if r.status_code == 422 and "email" in r.text.lower():
                    payload.pop("email", None)
                    r = (
                        client.post(f"/api/v1/accounts/{account}/contacts", json=payload)
                        if c is None
                        else client.put(
                            f"/api/v1/accounts/{account}/contacts/{c['id']}", json=payload
                        )
                    )
                r.raise_for_status()
                log.write(phone + "\n")
                log.flush()
                ok += 1
            except Exception as exc:  # noqa: BLE001 — one-off: seguir con el resto
                fail += 1
                print(f"  FALLO {phone}: {type(exc).__name__} {str(exc)[:120]}")
            if i % 100 == 0:
                print(f"  {i}/{len(todo)}  ok={ok} fail={fail}", flush=True)
    print(f"listo: ok={ok} fail={fail}")


if __name__ == "__main__":
    main()
