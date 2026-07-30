#!/usr/bin/env bash
# QA: dispara la plantilla "no contestó" (Bloque 7) sin necesitar una llamada real.
# Uso:
#   ./scripts/qa_handoff_no_answer.sh "+573001112233" "Juan Perez" "POL-12345" [documento]
#
# El 4º arg (documento) es OPCIONAL:
#   - CON documento  -> el bot NO pide documento; entra directo a esa póliza (caso 🟡 7.68).
#   - SIN documento  -> saluda con contexto pero pide documento para confirmar identidad.
#
# case_id se genera fresco en cada corrida (idempotencia = mismo case_id no reenvía).
set -euo pipefail

PHONE="${1:?Falta el número E.164, ej: +573001112233}"
NOMBRE="${2:?Falta el nombre del cliente}"
POLIZA="${3:?Falta el número de póliza}"
DOCUMENTO="${4:-}"

BASE="https://landa-agent-service-production.up.railway.app"
TOKEN="gbqyAHpxvJl-PS2jOxeuj20hXyTwuIjWWrfc1rKWCVTwl7y7YLH7fKG5fgVEtZqC"
CASE_ID="$(python -c 'import uuid; print(uuid.uuid4())')"

if [[ -n "$DOCUMENTO" ]]; then
  BODY=$(printf '{"phone":"%s","cliente_nombre":"%s","numero_poliza":"%s","case_id":"%s","documento":"%s"}' \
    "$PHONE" "$NOMBRE" "$POLIZA" "$CASE_ID" "$DOCUMENTO")
else
  BODY=$(printf '{"phone":"%s","cliente_nombre":"%s","numero_poliza":"%s","case_id":"%s"}' \
    "$PHONE" "$NOMBRE" "$POLIZA" "$CASE_ID")
fi

echo "case_id=$CASE_ID"
echo "POST $BASE/case/handoff/no_answer"
echo "body=$BODY"
echo "---"
curl -sS -X POST "$BASE/case/handoff/no_answer" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$BODY"
echo
echo "---"
echo "Espera la plantilla en WhatsApp ($PHONE). Toca 'Sí, ayúdenme' o 'Más tarde' para seguir el flujo."
echo "Reenvia el MISMO case_id para probar idempotencia (debe responder sent:false, sin 2a plantilla):"
echo "  curl -sS -X POST $BASE/case/handoff/no_answer -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '$BODY'"
