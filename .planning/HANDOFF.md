# Handoff — continuar desde otro computador

Generado: 2026-07-03T23:29Z (hora local Bogotá ~18:29)

## Contexto rápido

Ejecutando **Fase 4** (`04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona`) vía `/gsd-execute-phase 04`. Van **5/8 planes** mergeados a `main`:

- ✅ 04-01 — settings, modelos SQLAlchemy, skeletons
- ✅ 04-02 — `MetaCloudClient` media/templates + validador magic-byte
- ✅ 04-03 — webhook Chatwoot bidireccional (HMAC + anti-loop) + índice inverso Redis
- 🔄 04-04 — subgrafo de pago (**en progreso**, ver abajo)
- ✅ 04-07 — endpoint handoff voice-agent `/case/handoff/no_answer`
- ⏳ 04-05 — branch de cartera en webhook Meta + resume del grafo (bloqueado por 04-04)
- ⏳ 04-06 — timers business-hours (ARQ) + cleanup 90 días
- ⏳ 04-08 — output firewall + tests integración (gate de cierre de fase)

`main` está limpio y con todo lo anterior commiteado (no hay push a GitHub — el remote `origin` existe pero nunca se hizo push explícito; confirmar con el usuario antes de pushear).

## 04-04 — estado en el momento del corte

Plan: `.planning/phases/04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona/04-04-PLAN.md`

- **Task 1** (comprobante storage helper, `app/features/payment/storage.py`) — ✅ terminado y mergeado a `main` (commits `8c24e5b` RED, `c0e3610` GREEN).
- **Task 2** (subgrafo de pago: `build_payment_subgraph`, entry router en `app/features/qa/graph.py`, registro `process_attachment` en `app/worker.py`) — el intento anterior murió 2 veces sin avisar (corte de sesión silencioso, sin notificación de finalización). Un tercer intento (`agentId a44e4d1fce12057c4`, worktree `.claude/worktrees/agent-a44e4d1fce12057c4`) estaba corriendo en background al momento de este handoff.

### Qué revisar primero al retomar

1. Correr `git worktree list` — si el worktree `agent-a44e4d1fce12057c4` (o cualquier otro `agent-*`) sigue ahí, revisar si tiene commits nuevos (`git log --oneline main..worktree-agent-a44e4d1fce12057c4`) y si tiene `04-04-SUMMARY.md` committeado.
2. Si terminó limpio (SUMMARY.md presente): mergear a main (`git merge --no-edit worktree-agent-<id>`), correr suite completa (`.venv/Scripts/python.exe -m pytest -q`), limpiar el worktree (`git worktree remove --force <path>` + `git branch -D <branch>`), actualizar `.planning/STATE.md` (`completed_plans`), y seguir con Wave 4 (04-05 + 04-06 en paralelo).
3. Si murió otra vez sin avanzar: el problema NO es el código, es el límite de sesión/duración del agente cortando el trabajo antes de que termine una tarea que aparentemente requiere mucho contexto (grafo LangGraph + wiring de router + worker). Considera:
   - Dividir Task 2 en sub-pasos más pequeños y ejecutarlos con llamadas `Agent` separadas en vez de un solo agente largo.
   - Ejecutar 04-04 con `--interactive` (inline, sin subagente) para tener más control y visibilidad.
   - Revisar si hay un timeout de sesión más corto configurado que esté cortando agentes de larga duración.
4. **No confíes en la ausencia de notificación como señal de "sigue vivo"** — verifica con timestamps de archivos (`ls -la --time-style=full-iso`) contra la hora actual, y si hace mucho que no cambia nada, asume que murió y relanza.

## Cómo retomar la fase completa

```bash
cd "landa-agent-service"
git status  # confirmar que estás en main, limpio
cat .planning/STATE.md  # ver completed_plans actual
```

Luego invocar de nuevo:
```
/gsd-execute-phase 04
```
El workflow detecta automáticamente los planes con `has_summary: true` (ya hechos) y solo ejecuta los que faltan (04-04 si no cerró, 04-05, 04-06, 04-08).

## Pendientes de infraestructura (no bloquean código, pero anótalos)

- **Rotar `CHATWOOT_API_KEY`** en Railway — el token quedó impreso en una sesión de terminal anterior durante el debug de `chat.landatech.org`. Regenerar en Chatwoot (Profile Settings → Access Token) y actualizar la var en el servicio `landa-agent-service` en Railway.
- **Configurar webhook saliente de Chatwoot** hacia `landa-agent-service` + `CHATWOOT_WEBHOOK_SECRET` en Railway (detalle completo en `.planning/phases/04-.../04-03-SUMMARY.md`) — necesario para que el flujo bidireccional de 04-03 funcione en producción.
- **Aprobar en Meta** el template UTILITY `voice_no_answer_followup` (usado por 04-07) — sin esto el reenganche del voice agent no puede enviar el mensaje en producción.
- El healthcheck de Railway para el servicio Chatwoot quedó **deshabilitado** (fue lo que destrabó un deploy atascado por mala config de puerto). Si quieres detección de crashloops, hay que re-habilitarlo apuntando al puerto/ruta correctos.

## Chatwoot — ya resuelto (no repetir)

`chat.landatech.org` está sano: deploy `SUCCESS`, target port 8080 (Rails, no el 7433 de SidekiqAlive), `PORT=8080` seteado como env var. Ver historial de esta sesión si necesitas el detalle del debugging.
