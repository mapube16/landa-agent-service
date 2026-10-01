"""Saca de Chatwoot las preguntas de clientes que ARIA no supo responder.

Uso (las credenciales salen del entorno, nunca se imprimen):

    railway run --service landa-agent-service -- uv run python scripts/chatwoot_preguntas.py
    # o con CHATWOOT_URL / CHATWOOT_API_KEY / CHATWOOT_ACCOUNT_ID exportados

Escribe ``scratch/chatwoot_preguntas.txt`` (gitignored): por cada conversación,
los mensajes del cliente seguidos de la respuesta del bot, marcando los turnos
donde el bot escaló o dio una respuesta genérica. PII: solo se guarda el texto
del mensaje y el id de conversación; no teléfonos ni nombres.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

BASE = os.environ["CHATWOOT_URL"].rstrip("/")
KEY = os.environ["CHATWOOT_API_KEY"]
ACC = os.environ.get("CHATWOOT_ACCOUNT_ID", "1")
OUT = Path("scratch/chatwoot_preguntas.txt")

# Señales de "no supe responder": escalación, judge rechazado, fuera de scope.
_SIN_RESPUESTA = re.compile(
    r"te conecto con|un agente|no puedo ayudarte con eso|no tengo esa informaci|"
    r"fuera de mi alcance|agente de dpg|equipo de cartera te|no encontr[eé]",
    re.IGNORECASE,
)
_DOC_RE = re.compile(r"^\s*[\d.\- ]{5,}\s*$")  # documentos: no son preguntas


def _get(path: str, **params: object) -> dict:
    r = httpx.get(f"{BASE}{path}", headers={"api_access_token": KEY}, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    lines: list[str] = []
    page = 1
    total = 0
    while True:
        data = _get(f"/api/v1/accounts/{ACC}/conversations", status="all", page=page)
        convs = data.get("data", {}).get("payload", [])
        if not convs:
            break
        for c in convs:
            cid = c["id"]
            msgs = _get(f"/api/v1/accounts/{ACC}/conversations/{cid}/messages").get("payload", [])
            msgs = sorted(msgs, key=lambda m: m.get("created_at", 0))
            bloque: list[str] = []
            for i, m in enumerate(msgs):
                txt = (m.get("content") or "").strip()
                if not txt or m.get("private"):
                    continue
                if m.get("message_type") == 0:  # incoming (cliente)
                    if _DOC_RE.match(txt) or txt.lower() in {"si_ayudenme", "mas_tarde"}:
                        continue
                    # la siguiente salida del bot/agente
                    resp = next(
                        ((x.get("content") or "").strip() for x in msgs[i + 1 :] if x.get("message_type") == 1),
                        "",
                    )
                    flag = "  <-- SIN RESPUESTA" if _SIN_RESPUESTA.search(resp) else ""
                    bloque.append(f"  C: {txt}{flag}")
                    bloque.append(f"  A: {resp[:200]}")
            if bloque:
                total += 1
                lines.append(f"\n## conv {cid}")
                lines.extend(bloque)
        page += 1
        print(f"página {page - 1}: {len(convs)} conversaciones", file=sys.stderr)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{total} conversaciones -> {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
