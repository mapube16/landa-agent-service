# HANDOFF — landa-agent-service (para el agente que continúa)

Generado: 2026-07-04. Actualizado: 2026-07-05 (Fase 6 lado WA construido).
Reemplaza el handoff anterior (cambio de compu, Fase 4).

## 0. Qué es este repo y qué NO

`landa-agent-service` = **agente WhatsApp** de DPG Seguros (Q&A de pólizas +
validación de pago con escalación a Chatwoot). Dueño de TODO el canal WhatsApp
vía **Meta Cloud API directo** (NO Twilio).

**NO es este repo:** el agente de **voz/llamadas** vive en `lambda-proyect`
(repo aparte). La integración voz↔WhatsApp (Fase 6) está **definida por contrato,
sin construir**.

- GitHub: https://github.com/mapube16/landa-agent-service — rama **`main`** (se
  trabaja directo en main, todo pusheado).
- Local (esta máquina): `C:\Users\maxim\Desktop\landa-agent-service`
- venv: `.venv/Scripts/python.exe` · Tests: `.venv/Scripts/python.exe -m pytest -q -m "not integration"`
- Deploy: Railway, proyecto `brilliant-perfection`/production. Web
  `landa-agent-service` (auto-deploy desde GitHub main) + worker `agent-worker`
  (deploy MANUAL: `railway up -s agent-worker --ci`).

## 1. Estado actual (2026-07-04)

- **Fases 1-5 COMPLETAS, verificadas, desplegadas.** CI de GitHub Actions
  **VERDE**. **438 tests** (`-m "not integration"`), ruff+black+mypy --strict
  limpios (subió de 419 tras Fase 6 lado WA, commit `61edf7e`).
- **Fase 4** (pago + Chatwoot bidi): smoke en vivo hecho con números de prueba
  Meta. 6 bugs de prod encontrados+arreglados (ver §3).
- **Fase 5** (seguridad + audit log): audit log inmutable (trigger Postgres) +
  hash chain + rate limiting Redis + 19 tests de jailbreak + 13 capas auditadas.
- **Volumen Railway montado** en `agent-worker` (`/data/comprobantes`) →
  comprobantes ya no se pierden en redeploy.

Fuente de verdad detallada (LEER estos 5 antes de tocar nada):
1. `.planning/STATE.md` — posición GSD, decisiones.
2. `.planning/phases/04-*/04-SMOKE-RESULTS.md` — 6 bugs fixed, 3 latentes, ops.
3. `.planning/phases/04-*/04-VERIFICATION.md` y `05-*/05-VERIFICATION.md`.
4. `.planning/contracts/lambda-handoff-contract.md` — contrato Fase 6.
5. `CLAUDE.md` — reglas críticas (READ-ONLY SoftSeguros, no list_* al LLM, etc.).

## 2. Ruta pendiente (qué sigue, en orden)

### A. Fase 6 — integración con el voice agent (lambda-proyect) — AMBOS LADOS CONSTRUIDOS
Contrato: `.planning/contracts/lambda-handoff-contract.md`.

- **Lado WA (este repo, commit `61edf7e`, 2026-07-05): entregables 1-5 DONE.**
  `POST /case/handoff` (Contrato A, en `app/webhooks/handoff.py`), migración
  `0004_cases_cross_canal` (solo `debtor_id` + `call_ids` — el resto del draft
  original, `conversation_ids`/`escalations`/`events`, se DIFIRIÓ por YAGNI,
  nada los consume hoy), `app/memory/debtor_flags.py` (L4, inyectado en
  `node_answer`), `app/integrations/lambda_proyect.py` (cliente REST Contrato B:
  `escalate_case`/`update_debtor`, fail-open — nunca bloquea el flujo al
  cliente). Wireado en `node_confirming` (B2 al aprobar) y
  `node_payment_escalate` (B1 al escalar), ambos gateados por
  `case.debtor_id is not None` (casos WhatsApp-only no llaman a VOICE). 438
  tests (19 nuevos), ruff+black+mypy limpios.
- **Lado VOICE (lambda-proyect, repo `hive-pixel-office` en esta máquina,
  rama `eval/dpg-cobranza-microservice`, commit `add742d`, 2026-07-05):
  entregables 6-9 DONE.** `cobranza/wa_bridge_router.py` (B1 escalate + B2
  update, fail-closed 503 si `WA_TO_VOICE_TOKEN` no está configurado),
  `cobranza/wa_bridge.py` (llama a WA con Contrato A), fix real del stub
  muerto `whatsapp_notifier.py`, `cobranza/shared_models.py` (Debtor/Policy/
  ConversationContext copiados, sin submodule `landa-shared` — decisión v1).
  Self-check 8/8 contra Mongo real.
  ⚠️ **OJO con este repo**: el `HEAD` local salta de rama solo entre
  sesiones (se observó pasar de `eval/dpg-cobranza-microservice` a `master`
  sin que nadie de esta sesión lo pidiera) — probablemente otra sesión tuya
  trabajando ahí en paralelo. Antes de tocarlo, correr
  `git log --oneline -1` y `git branch --show-current` para confirmar dónde
  quedó, y si hay otra sesión activa, coordinar antes de hacer `checkout`.

**Pendiente para activar el puente end-to-end (no es código, es ops):**
configurar en Railway, AMBOS repos: `LAMBDA_PROYECT_BASE_URL`/
`LAMBDA_PROYECT_INTERNAL_TOKEN` (VOICE→WA, ya existía) y
`LAMBDA_PROYECT_WA_TO_VOICE_TOKEN`/`WA_TO_VOICE_TOKEN` (WA→VOICE, nuevo —
mismo secreto, nombre de env var distinto en cada repo). Sin esto, el lado WA
sigue funcionando normal (fail-open, solo no notifica a VOICE) y el lado
VOICE devuelve 503 fail-closed a cualquier llamada de WA.

### B. SoftSeguros — lookup de pagos rápido (✅ CABLEADO 2026-07-06)
`get_pagos` (504, muerto) fue **reemplazado** por `get_cartera_status(poliza_id)`
en `app/integrations/softseguros.py`, sobre el endpoint rápido
`list_pagospolizas_filtro_paginados` (~1s, confirmado en vivo contra prod).
Devuelve `CarteraStatus | None` (whitelist Capa 4 vía Pydantic
`extra="ignore"`: `fecha_pago`, `fecha_realizara_pago`, `saldo_pendiente`,
`edad_cartera`, `ramo_nombre`, `riesgo`). Consumido por
`app/memory/debtor_flags.py` (L4) cuando hay `poliza_id` — enriquece el
system prompt con `dias_mora`/`fecha_compromiso`/`saldo_pendiente`/`riesgo`,
fail-open independiente del historial local. `riesgo` = campo
`poliza_codio_objeto_asegurado` (sic) = el objeto asegurado (placa para
AUTOMÓVILES), NO el número de póliza — así lo llama DPG internamente.
Detalle + caveats en `.planning/phases/02-*/SOFTSEGUROS_API_NOTES.md`.

### C. Ops para salir a prod (acción humana / UI)
- **Rotar `CHATWOOT_API_KEY`** (se filtró en terminal en sesión previa).
- **Rotar el token SoftSeguros `b3565b44...`** (se pegó en una sesión de chat el
  2026-07-04 al compartir un curl).
- ~~`APP_ENV=dev`→`production` en web+worker~~ **✅ HECHO 2026-07-07** (`APP_ENV=prod` — OJO: el código usa el Literal `"prod"`, NO `"production"`; poner `"production"` crashea el arranque, ya pasó una vez).
- ~~Template Meta `voice_no_answer_followup`: confirmar estado APPROVED.~~ **✅ APROBADO 2026-07-06** (copy final = informe técnico Mensaje 1 + 2 botones quick-reply, ver `04-CONTEXT.md` D-20/D-21).
- Pasar el número WhatsApp de **modo test a live** en Meta (hoy máx 5 recipients).
- **Bloqueador nuevo, lado VOICE (no es de este repo):** el número de **Twilio** (llamadas salientes de ARIA) aún no está aprobado (2026-07-06). Sin esto, lambda-proyect no puede originar la llamada que dispararía el escenario "no contestó" → el template de WA ya está listo pero no tiene qué disparar el flujo end-to-end todavía.

### D. Smoke E2E pendiente
Criterios 1, 2 en vivo (ver 04-SMOKE-RESULTS.md). Criterios 4 y **5 (✅ 2026-07-07)**
ya validados en vivo. El 5 destapó 2 bugs reales de paso (ya arreglados,
`df08a5d` + `18a9d40`): un `log.error()` que crasheaba con CUALQUIER error
de Meta en vez de mostrarlo, y la plantilla aprobada resultó tener CERO
variables (ni header ni body) pese a que el editor mostraba `{Nombre del
Cliente}` en la preview — no era una variable real guardada.

## 3. Contexto que NO está obvio en el código

- **Chatwoot HMAC (bug arreglado, e802947):** Chatwoot firma
  `HMAC-SHA256(channel.secret, "{X-Chatwoot-Timestamp}.{body}")`. El secreto
  correcto es el campo `secret` del canal API (NO `hmac_token`). `CHATWOOT_WEBHOOK_SECRET`
  = ese `secret`. Si el canal bidi da 401, es esto.
- **agent-worker NO auto-deploya** desde GitHub — deploy manual `railway up`.
- **Meta modo test:** el bot solo envía a ≤5 números registrados como test
  recipients. Cliente prueba = +57 312 3528153; cartera prueba = +57 317 3717828.
- **Forward a cartera fuera de horario** (D-13): se difiere. Bug latente #1:
  el forward diferido tenía el status mal (arreglado en 3bdd829, ver
  04-SMOKE-RESULTS bugs latentes por si reaparece).
- **CI:** solo **black** es el formateador (se quitó `ruff format` que peleaba
  con black). ruff = lint. mypy --strict corre sobre `app/`.
- **Tests de integración** (`@pytest.mark.integration`) están apagados tras env
  vars: `INTEGRATION_LLM` (jailbreaks vs LLM real), `INTEGRATION_POSTGRES_URL`
  (trigger DB inmutable). Son el "eval real" del sistema.

## 4. Cómo retomar

```bash
cd "C:/Users/maxim/Desktop/landa-agent-service"
git pull
.venv/Scripts/python.exe -m pytest -q -m "not integration"   # debe dar 419 passed
cat .planning/STATE.md
# Siguiente: Fase 6 → leer .planning/contracts/lambda-handoff-contract.md
```
