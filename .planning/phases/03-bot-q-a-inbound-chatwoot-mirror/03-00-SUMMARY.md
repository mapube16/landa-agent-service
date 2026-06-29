---
phase: 03-bot-q-a-inbound-chatwoot-mirror
plan: 00
type: execute
wave: 0
autonomous: false
status: complete
date_completed: 2026-06-29
---

# Plan 03-00 — Wave 0 Probe Summary

## Performance

- **Wallclock:** ~15min (operator probes + 1 Chatwoot inbox creation + 1 Gemini Flash test).
- **Token usage (orchestrator + Chatwoot/SoftSeguros/OpenRouter probes):** negligible (curl + tiny Python).
- **Commits:** 2 (probe findings Task 1 + this summary + PROBE addendum for Tasks 2/3).

## Accomplishments

Las 3 Open Questions de RESEARCH.md quedaron resueltas con findings reales contra el sandbox DPG:

1. **`/api/cliente/listar_cliente_por_documento/` shape confirmed.** Param correcto = `numero_documento`. Devuelve single object con 122 fields (mismo shape que `/api/cliente/{id}/`). **CERO pólizas embebidas** → patrón doble-call obligatorio (`listar_cliente` → `cliente_id` → `/api/poliza/?cliente=<id>`). Fallback de `/api/poliza/?cliente_numero_documento=` descartado (filter ignorado, devuelve count=52898). Test cliente `900144220-7` tiene 20 pólizas en 6+ ramos → fixture canónica para D-02.

2. **Chatwoot API Channel inbox creado y verificado.** `Channel::Api` confirmado via smoke `GET /api/v1/accounts/1/inboxes/2` (200 OK). Env vars capturadas en transcript del operador para set en Railway durante Plan 03-06. Custom domain `chat.landatech.org` deferido (no bloquea F3 — usa Railway URL).

3. **Gemini Flash structured output via OpenRouter funciona end-to-end** con `response_format.json_schema.strict=true`. Judge razonó correctamente sobre un escenario sintético (78 tokens prompt / 81 tokens output, ~$0.000035/call). LangChain `with_structured_output(JudgeRubric)` es feasible para Plan 03-04.

## Task Commits

| Commit | Tarea | Atómica |
|---|---|---|
| `c556b16` | docs(03-00): probe SoftSeguros endpoint — listar_cliente_por_documento confirmed, two-call pattern required | ✅ |
| `<this commit>` | docs(03-00): close Wave 0 — Chatwoot + Gemini probes + summary | ✅ |

## Files Created/Modified

| Path | Tipo | Resumen |
|---|---|---|
| `.planning/phases/03-bot-q-a-inbound-chatwoot-mirror/03-00-PROBE.md` | nuevo + appended | 4 secciones (Task 1/2/3/4), findings literales, implications por plan downstream |
| `.planning/phases/03-bot-q-a-inbound-chatwoot-mirror/03-00-SUMMARY.md` | nuevo | este archivo |

No tocó código de `app/`. Wave 0 es 100% planning artifact.

## Decisions Made

- **Pattern doble-call mandatorio** para identificación por documento. Affecta a `SoftSegurosClient` API surface en Plan 03-02: dos métodos READ-ONLY nuevos (`get_clientes_by_documento`, `get_polizas_by_cliente`). Plan 03-02 `must_haves` ya documenta esta variante.
- **`ClienteRaw` modela subset (~20 fields)** del response 122-field. `model_config = ConfigDict(extra="ignore")` para tolerancia a cambios upstream.
- **Endpoint 404/empty behavior open** (no probado con documento inexistente). Plan 03-02 debe documentar handling. Capturado como follow-up minor, no bloquea.
- **Mantener pattern de plain `Channel::Api`** verificable con literal-string assertion en Plan 03-04 conftest test.
- **Custom domain `chat.landatech.org` queda fuera de scope F3.** Cambio de env var posterior si llega antes del PR de F3.
- **Webhook URL del inbox queda apuntando a `/webhooks/chatwoot`** aunque F3 no implementa ese endpoint. Chatwoot va a recibir 404s benignos hasta que F4 implemente el receptor. Documentado.

## Deviations from Plan

- **Orquestador abandonó el worktree spawneado** para Wave 0 (worktree `agent-a82e3bc30416d8689`) por limitación de runtime: `SendMessage` no estaba disponible para continuar el agente después del primer checkpoint. Plan 03-00 se completó inline con curl directos + escritura manual de PROBE.md por el orquestador. Resultado neto idéntico al spawning path; no se perdió ni código ni decisiones (el worktree estaba sin commits cuando se limpió).
- **Plan 03-00 originalmente esperaba 4 tasks discretos**; en la práctica se ejecutaron como 3 probes secuenciales (SoftSeguros + Chatwoot + Gemini) y un step de consolidación final. Conceptualmente equivalente.

## Issues Encountered

1. **`SendMessage` no disponible en este runtime** → orquestador no pudo continuar el subagente del worktree después del primer checkpoint. Resuelto pivoteando a ejecución inline.
2. **Python console default codec cp1252** en Windows → al imprimir JSON con caracteres unicode (`á`, `é`) muestra mojibake (`�`). NO afecta los datos JSON — solo el render en stdout. Resuelto usando `open(..., encoding='utf-8')` explícito.
3. **Operator accidentally landed in System Settings** primero en lugar de Account Settings al buscar dónde crear el inbox. Resuelto guiando al path correcto (`/app/accounts/1/settings/inboxes/new/api` o equivalente UI flow).

## User Setup Required

Antes de Plan 03-06 (smoke), el operador debe:

```bash
railway variable set CHATWOOT_URL=https://chatwoot-production-d073.up.railway.app
railway variable set CHATWOOT_API_KEY=<from transcript>
railway variable set CHATWOOT_ACCOUNT_ID=1
railway variable set CHATWOOT_INBOX_ID=2
railway variable set CHATWOOT_INBOX_CHANNEL_TYPE=Channel::Api
```

Y al cierre de F3 (igual que con `WA_TOKEN` en F2):

```bash
# Rotación post-PR
railway variable set SOFTSEGUROS_PASSWORD=<new from SoftSeguros panel>
railway variable set CHATWOOT_API_KEY=<new from Chatwoot Profile → Access Token>
```

## Next Phase Readiness

**Wave 1 (Plan 03-01 — foundation) está unlocked.** Inputs disponibles:

- `ClienteRaw` shape confirmada → modelar con subset + `extra="ignore"`
- `Channel::Api` literal validado → conftest stub puede usar string literal exacto
- Structured output via OpenRouter `response_format.json_schema.strict=true` validado → `JudgeRubric` skeleton confiable
- Dependency graph del fase intacto: 03-00 → 03-01 → [03-02 || 03-03 || 03-04] → 03-05 → 03-06

**Recomendación al orquestador:** spawn Plan 03-01 cuando context window lo permita (foundation puede ser autonomous, sin operator interaction). Si context tight, `/clear` + restart de execute-phase desde Wave 1.
