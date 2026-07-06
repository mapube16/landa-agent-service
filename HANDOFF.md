# HANDOFF — landa-agent-service (para el agente que continúa)

Generado: 2026-07-04. Reemplaza el handoff anterior (cambio de compu, Fase 4).

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
  **VERDE**. **419 tests** (`-m "not integration"`), ruff+black+mypy --strict limpios.
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

### A. Fase 6 — integración con el voice agent (lambda-proyect)
Estado: **contrato REST redactado**, código sin construir. El operador maneja
AMBOS repos (tiene acceso a lambda-proyect).
- Contrato: `.planning/contracts/lambda-handoff-contract.md` (Contrato A: VOICE→WA
  `POST /case/handoff`; Contrato B: WA→VOICE escalate + debtor/update; reparto de
  9 entregables; recomendación: **NO** montar submodule `landa-shared`, duplicar
  ~3 modelos frozen v1).
- Lado WA (este repo, entregables 1-5): `POST /case/handoff`, migración 0004
  (cases cross-canal: call_ids/conversation_ids/escalations/events + debtor_id),
  `memory/case_store.py` cross-canal, `memory/debtor_flags.py` (inyección al
  system prompt), cliente REST a VOICE (`integrations/lambda_proyect.py` es el stub).
- Lado VOICE (lambda-proyect, entregables 6-8): endpoints B1/B2 + reemplazar el
  stub muerto `cobranza/sub_agents/whatsapp_notifier.py`.
- Para construir el lado VOICE hay que **clonar lambda-proyect** en esta máquina
  primero y explorarlo (rellena los campos de Debtor/Policy que el contrato dejó
  abiertos). NO pushear a su main sin permiso.

### B. SoftSeguros — lookup de pagos rápido (RESUELTO, sin cablear)
El `get_pagos` actual (`app/integrations/softseguros.py:258`) da **504** (lento).
**Reemplazo encontrado y verificado** (2026-07-04):
`GET /api/pagopoliza/list_pagospolizas_filtro_paginados/?sede=1047&texto_busqueda={numero_poliza}&search_in=poliza_numero_poliza`
— HTTP 200, rápido, auth = token propio del app. Mapa de campos + caveats en
`.planning/phases/02-*/SOFTSEGUROS_API_NOTES.md` (open Q #3 marcada RESUELTO).
Trae fecha_pago (mora), fecha_realizara_pago (compromiso, CONFIRMAR con DPG),
saldo_pendiente. **Cablear cuando F6/debtor_flags lo consuma** (hoy no hay
consumidor — YAGNI). Al cablear: whitelist estricta Capa 4 (trae ~150 campos con
comisiones/PII), scopeado por póliza, nunca tool de búsqueda al LLM.

### C. Ops para salir a prod (acción humana / UI)
- **Rotar `CHATWOOT_API_KEY`** (se filtró en terminal en sesión previa).
- **Rotar el token SoftSeguros `b3565b44...`** (se pegó en una sesión de chat el
  2026-07-04 al compartir un curl).
- `APP_ENV=dev`→`production` en web+worker.
- Template Meta `voice_no_answer_followup`: confirmar estado APPROVED.
- Pasar el número WhatsApp de **modo test a live** en Meta (hoy máx 5 recipients).

### D. Smoke E2E pendiente
Criterios 1, 2, 5 en vivo (ver 04-SMOKE-RESULTS.md). El criterio 4 (Chatwoot→
cliente) ya se validó en vivo.

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
