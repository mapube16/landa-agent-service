---
phase: 01-setup-infra
plan: 07
subsystem: verification
tags: [smoke, e2e, phase-close, success-criteria, langsmith, sentry]
requires:
  - 01-05  # Railway agent service group deployed
  - 01-06  # Chatwoot self-hosted deployed
provides:
  - .planning/phases/01-setup-infra/SMOKE_E2E.md (live verification report)
  - Phase 1 status = COMPLETE
affects:
  - app/config/settings.py (no changes here, but env var LANGSMITH_WORKSPACE_ID added in Railway as part of unblocking SC4)
key-files:
  created:
    - .planning/phases/01-setup-infra/SMOKE_E2E.md
decisions:
  - "Accepted /health 'degraded' on the first probe of a smoke run when OpenRouter latency lands at 1000.8 ms (1 ms over the 1 s timeout). 5/5 retries returned 'healthy' with OpenRouter latency 500-900 ms. Documented as a known tail flake; not a Phase 1 blocker. F5 may revisit the timeout budget when egress controls land."
  - "Replaced the plan's hardcoded `google/gemini-2.0-pro` SC2 expectation with the real OpenRouter slug `google/gemini-2.5-pro` (the original does not exist in OpenRouter's catalog, fixed in plan 01-05). Test asserts `model='google/gemini-2.5-pro'`."
  - "Replaced LangSmith project `landa-agent-dev` (from plan) with `landa-agente-dpg` (operator's actual project in LangSmith)."
  - "SC4 verified via the LangSmith REST API (`/api/v1/runs/query`) instead of the dashboard. The runtime trace ingestion was broken until `LANGSMITH_WORKSPACE_ID=40a45125-...` was added — the Service Key (`lsv2_sk_*`) is ORG-scoped and requires a workspace tenant header that the langsmith SDK injects from this env var."
  - "Used Railway *.up.railway.app URLs throughout the smoke. Custom domain DNS work (chat.landatech.org + agent.landatech.org) explicitly deferred per operator decision."
metrics:
  duration: "~30 minutes wall-clock (most of it diagnosing the LangSmith Service Key + workspace header)"
  completed: "2026-06-28"
  tasks_completed: 3
  files_created: 2  # SMOKE_E2E.md, 01-07-SUMMARY.md
  files_modified: 0  # no source code changes; runtime env-var change only
  success_criteria_pass: 5
  success_criteria_total: 5
---

# Phase 1 Plan 07: End-to-end smoke verification Summary

**One-liner:** Los 5 success criteria de Phase 1 PASS. `/health` healthy con 4 components, `/test/llm` retorna respuesta real de Gemini 2.5 Pro vía OpenRouter, Chatwoot UI permite crear+borrar un inbox end-to-end, LangSmith proyecto `landa-agente-dpg` recibe traces (tras configurar `LANGSMITH_WORKSPACE_ID`), Sentry captura el error sintético con PII scrubbed. Phase 1 cerrada.

## Tareas ejecutadas

### Tarea 1 (automated) — `/health` + `/test/llm` + `/test/sentry` trigger

`curl` directos contra `https://landa-agent-service-production.up.railway.app/`. Resultados:

| Endpoint | Status | Verificación |
|---|---|---|
| `GET /health` (cold) | 200, `status=degraded` | OpenRouter probe 1000.8 ms (tail flake); 5/5 retries volvieron `healthy` con 500-900 ms |
| `POST /test/llm` | 200 | `{"reply":"pong","model":"google/gemini-2.5-pro","role":"conversation","latency_ms":7225.5}` |
| `POST /test/sentry` | 500 | esperado — endpoint raisea `RuntimeError` para que Sentry capture |

### Tarea 2 (human-verify) — Chatwoot inbox + LangSmith + Sentry visuales

- **SC3 Chatwoot**: operador creó inbox API `smoke-test` con webhook placeholder. UI mostró "¡Tu bandeja de entrada está lista!". Borrado para dejar Chatwoot idle.
- **SC4 LangSmith**: verificado por mi mismo via API (`/api/v1/runs/query`), 1 `ChatOpenAI [success]` en project `landa-agente-dpg`. **No fue trivial llegar acá**: el primer test mostró `[WARN] Failed to multipart ingest runs: 403 Forbidden`. Diagnóstico — la key `lsv2_sk_*` es Service Key ORG-scoped, no Personal API key. El endpoint `/runs/multipart` rebota con 403 sin el header `X-Tenant-Id`. El langsmith SDK lo deriva del env var `LANGSMITH_WORKSPACE_ID` que **el plan original no contemplaba**. Una vez seteado `LANGSMITH_WORKSPACE_ID=40a45125-...` en agent-service + agent-worker y restart, el WARN desapareció y el trace landed.
- **SC5 Sentry**: operador confirmó visual en sentry.io: `transaction=app.main.test_sentry`, `handled=no`, trace `000ab284d23f4171b53a275a1fc7c3bd`. PII check: clean (scrubber actuó).

### Tarea 3 (auto) — Consolidación SMOKE_E2E.md + cierre

Escrito `.planning/phases/01-setup-infra/SMOKE_E2E.md` con los 5 SC = PASS + sección "Phase 1 closure" listando qué quedó vivo y qué quedó explícitamente deferred.

## Verificación

| Check | Resultado |
|---|---|
| 5/5 success criteria PASS | ✅ |
| SMOKE_E2E.md existe con sección "Phase 1 closure" | ✅ |
| Sin PII en evento Sentry | ✅ operador-verified |
| `LANGSMITH_API_KEY` rotada en ambos services | ✅ |
| `LANGSMITH_WORKSPACE_ID` seteada en ambos services | ✅ |
| LangSmith logs sin 403 warnings post-fix | ✅ |
| Chatwoot ipso facto operativo (login + crear/borrar inbox) | ✅ |

## Desviaciones del plan

1. **[Plan stale — corregido] `model='google/gemini-2.0-pro'` no existe**
   - Plan 01-07 asumía SC2 con model `2.0-pro`. OpenRouter rechaza ese slug; el real es `2.5-pro` (verificado en plan 01-05).
   - SMOKE_E2E.md y este SUMMARY usan `2.5-pro` consistentemente.

2. **[Plan stale — corregido] LangSmith project `landa-agent-dev` no existe**
   - Operador creó manualmente `landa-agente-dpg` en LangSmith UI.
   - Env var `LANGSMITH_PROJECT` retargeted a `landa-agente-dpg` en plan 01-06 → ahora en este plan.

3. **[Auth model gap — fixeado] LangSmith Service Key requiere `LANGSMITH_WORKSPACE_ID`**
   - El plan original no contemplaba este env var.
   - Sin él, `/runs/multipart` rebotaba 403 silenciosamente (solo WARN en logs del agent, no bloquea el call al LLM).
   - Fix runtime: `railway variable set --service landa-agent-service LANGSMITH_WORKSPACE_ID=...` en ambos services.
   - **Acción de follow-up**: agregar este env var al `.env.example` y a `app/config/settings.py:LangSmithSettings` con default `None` para que CI/dev sepan que existe. **No bloquea Phase 1 cerrar**; va como mini-PR en F2.

4. **[Plan stale — operacional] URLs del plan apuntan a custom domains que no existen aún**
   - Plan 01-07 hardcodea `https://chat.landatech.org` y asume `agent.landatech.org`.
   - Operador decidió diferir custom domains al final de Phase 1.
   - Smoke ejecutado contra Railway `*.up.railway.app`. Cuando los custom domains aterricen, el SMOKE_E2E.md vale igual — solo se actualiza la sección "Agent domain"/"Chatwoot domain".

5. **[Tail flake — accepted] `/health` `degraded` ocasional por OpenRouter al borde del 1 s timeout**
   - 1/6 calls (la primera) reportó OpenRouter 1000.8 ms. Las 5 siguientes 500-900 ms.
   - Documentado en plan 01-05 ya. No bloquea F1. F5 puede revisar el budget.

## Self-Check: PASSED

- [x] SMOKE_E2E.md creado y completo
- [x] 5 SC marcados PASS en la tabla resumen
- [x] Sección "Phase 1 closure" presente con "what works" + "what's deferred" + "next"
- [x] Sin secretos en SMOKE_E2E.md (correlation_ids son UUIDs; API keys nunca pegados)

## Phase 1 → Phase 2 handoff

Phase 1 (Setup infra) **CLOSED** con los 8 plans done:
- 01-01 scaffold + deps + tooling
- 01-02 Dockerfiles + Settings + structlog + Sentry
- 01-03 DB + Alembic + checkpointer + Redis
- 01-04 LLM factory + FastAPI + /health + tests
- 01-05 Railway deploy agent service group
- 01-06 Chatwoot self-hosted
- 01-07 (este) — Smoke E2E verification
- 01-08 CLAUDE.md → uv quickstart

Pendientes que NO bloquean Phase 2:
- Custom domains `agent.landatech.org` + `chat.landatech.org` (final-of-milestone work)
- Backlog item: fix `alembic/versions/0001_initial_schema.py` (asyncio.run nesting). Workaround actual = `alembic stamp head`; safe porque `checkpointer.setup()` crea las tablas idempotently.
- Mini-PR: agregar `LANGSMITH_WORKSPACE_ID` a `app/config/settings.py:LangSmithSettings` + `.env.example` para que CI/dev no se sorprenda.

**Próximo**: `/gsd-discuss-phase 02` o `/gsd-plan-phase 02` para arrancar Phase 2 (Integración SoftSeguros + WhatsApp Cloud API).
