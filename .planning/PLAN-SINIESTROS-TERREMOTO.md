# Plan operativo: siniestros terremoto (Hogar / Pyme / Copropiedad)

Estado: borrador 2026-09-14. Dueño: LANDA + DPG.

## Lo que ya tenemos (no rehacer)

- `segmento.csv`: 447 polizas VIGENTES de HOGAR / PYME / COPROPIEDADES con celular
  valido (barrido completo de `api/poliza/` del 8-sep). 14 descartadas sin celular usable.
- Las 447 ya recibieron la plantilla del conversatorio de reclamaciones (11-sep).
- `scripts/broadcast_conversatorio.py`: segmentar, crear plantilla Meta, enviar,
  reanudable, espeja en Chatwoot. Se reutiliza tal cual con otra plantilla.
- Bot Q&A + escalacion a humano por Chatwoot ya en produccion.

## Preguntas que bloquean (responder antes de ejecutar)

1. Fecha del sismo y nueva fecha limite de aviso por aseguradora (va en el mensaje).
2. Municipios afectados (para priorizar, no para filtrar).
3. Siniestros en SoftSeguros: DPG los carga en el modulo Siniestros? Se puede exportar
   a Excel desde la UI? El schema de poliza (184 campos, `SOFTSEGUROS_API_NOTES.md`)
   NO tiene campo de siniestro: viven en otro endpoint, nunca probado. Sonda de solo
   lectura (patron documentado en 03-00-PLAN, la corre el operador):

   ```bash
   TOKEN=$(curl -s -X POST https://app.softseguros.com/api-token-auth/      -H "Content-Type: application/json"      -d '{"username":"<DPG_USERNAME>","password":"<DPG_PASSWORD>"}' | jq -r .token)
   for p in api/ api/siniestro/ api/siniestros/ api/siniestropoliza/ api/reclamacion/; do
     echo "== $p"; curl -s -o /dev/null -w "%{http_code}
" "https://app.softseguros.com/$p?page=1"        -H "Authorization: Token $TOKEN"
   done
   curl -s "https://app.softseguros.com/api/siniestro/?page=1" -H "Authorization: Token $TOKEN" | jq '.results[0] // .'
   ```
   Si algo da 200, pegar las keys del primer resultado sanitizado y se cablea igual que
   `get_cartera_status`. Si todo da 404, la fuente es el export Excel de la UI.
4. Quien atiende siniestros en DPG (mismo inbox de cartera o equipo aparte).
5. Documentos exigidos por etapa, por aseguradora (para el KB y el checklist).

## Fase 0 — Datos (dia 0, sin codigo nuevo)

Objetivo: dos listas.

- **Lista A (con siniestro registrado)**: export del modulo Siniestros de SoftSeguros
  (UI → Excel) filtrado por ramo + fecha >= sismo. Si hay API, script de 30 lineas
  calcado de `segment()`. Cruce con `segmento.csv` por `numero_poliza`.
- **Lista B (sin siniestro registrado)**: `segmento.csv` menos Lista A. Es el universo
  a sondear. Aqui SoftSeguros no puede decir quien tuvo dano; hay que preguntarle
  al cliente.
- **Lista C (sin celular)**: las 14 descartadas + las de Lista A sin celular →
  llamada manual o agente de voz de lambda-proyect.

Geografia: la poliza trae `cliente_ciudad` / `cliente_direccion` y ya tenemos el `id`
de cada una en `segmento.csv.raw.jsonl`. Son 447 GET a `/api/poliza/{id}/` (minutos),
no el barrido de 60 min. Sirve para priorizar Lista B por municipio afectado.

## Fase 1 — Deteccion (dia 1-2)

Plantilla Meta `afectacion_sismo` con 3 quick replies:
`Si, tuve danos` / `No tuve danos` / `Ya lo reporte`. Texto: hubo sismo, el plazo de
aviso se amplio hasta <fecha>, DPG acompana el proceso. Aprobacion Meta ~horas.

- Enviar a Lista B con `broadcast_conversatorio.py send` (agregar soporte de
  quick replies al `send_template` de `meta_cloud.py`; ~15 lineas).
- Respuesta `Si` → etiqueta Chatwoot `siniestro:aviso` + asignar a humano.
  Respuesta `Ya lo reporte` → `siniestro:reclamacion`. `No` → cerrar.
- Texto libre que mencione dano/sismo/grieta → el bot escala con etiqueta
  `siniestro:aviso` (regla determinista en el firewall de entrada, no LLM).
- Lista A recibe otra plantilla: "vimos tu reporte, este es el siguiente paso".

## Fase 2 — Acompanamiento (semana 1 en adelante)

Etapas como etiquetas Chatwoot (una por conversacion, se reemplaza al avanzar):

| Etiqueta | Que espera el cliente | Nudge si no avanza |
|---|---|---|
| `siniestro:aviso` | Confirmar aviso a la aseguradora | 24h |
| `siniestro:reclamacion` | Enviar documentos formales | 3 dias |
| `siniestro:presupuesto` | Cotizacion de reparacion | 5 dias |
| `siniestro:perito` | Visita del ajustador | 7 dias |
| `siniestro:ofrecimiento` | Respuesta de la aseguradora | 10 dias |
| `siniestro:acuerdo` | Firma / aceptacion | 5 dias |
| `siniestro:pago` | Indemnizacion recibida | cierre |

Gastos profesionales (ajustador propio, abogado) como atributo, no etapa.

- Fuente de verdad: Chatwoot. Sin dashboard nuevo (fuera de alcance v1). Un CSV
  semanal exportado de Chatwoot por etiqueta basta para el reporte a DPG.
- Bot: seccion nueva en `knowledge/dpg_cartera.md` con el proceso, plazos ampliados,
  documentos por etapa y contacto. Con eso responde "que sigue?" sin humano.
- Nudges: v1 manual (agente revisa etiquetas). v2 si hay volumen: script semanal que
  lista conversaciones por etiqueta + dias sin actividad y manda plantilla recordatorio,
  mismo patron que `sin-respuesta tras 5h`.

## Orden de ejecucion

1. Hoy: respuestas a las 5 preguntas + export de siniestros de SoftSeguros.
2. Hoy: Listas A/B/C (script de cruce, 20 lineas).
3. Hoy: enviar plantilla `afectacion_sismo` a aprobacion de Meta.
4. Manana: KB + regla de escalacion + quick replies en `meta_cloud.py`.
5. Al aprobarse: envio a Lista B (empezar `--limit 20`, luego el resto).
6. Semana 1: acompanamiento por etiquetas, reporte semanal a DPG.

Fuera de alcance: OCR de documentos, integracion con aseguradoras, dashboard nuevo.
