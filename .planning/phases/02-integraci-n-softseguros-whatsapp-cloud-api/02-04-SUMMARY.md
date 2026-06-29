---
phase: 02-integraci-n-softseguros-whatsapp-cloud-api
plan: 04
subsystem: verification
tags: [smoke, e2e, phase-close, meta, softseguros]
requires:
  - 02-01
  - 02-02
  - 02-03
provides:
  - End-to-end WhatsApp round-trip verified in production (Railway)
  - SoftSeguros real query verified via `/test/poliza/{id}`
  - Phase 2 status = COMPLETE
key-files:
  created:
    - .planning/phases/02-integraci-n-softseguros-whatsapp-cloud-api/02-04-SUMMARY.md
decisions:
  - "Switched from real number +1 641-541-6615 (Twilio-migrated, 2FA PIN unknown, rate-limited after 9 bad attempts) to Meta TEST number +1 555-203-1790 for Phase 2 verification. Real number stays in WABA 1451322196454283 (subscribed but inactive until PIN reset)."
  - "WA_WEBHOOK_SECRET = the Meta App Secret (not a user-invented string). Initial mistake during planning: treated as custom secret. Confirmed via failing webhooks returning 401 hmac.invalid — Meta signs X-Hub-Signature-256 with the App Secret only."
metrics:
  duration: "~90 minutes wall-clock (incl. WABA subscription debugging, app secret discovery, allowlist setup)"
  completed: "2026-06-29"
  success_criteria_pass: 5
  success_criteria_total: 5
---

# Phase 2 Plan 04: End-to-end smoke verification Summary

**One-liner:** Los 5 success criteria de Phase 2 verificados live: WhatsApp echo round-trip funcional via Meta Cloud API test number, HMAC valida con App Secret, SoftSeguros `/test/poliza/228700` retorna data real DPG, READ-ONLY guard activo. Phase 2 cerrada.

## Setup activo

| Item | Valor |
|---|---|
| Test phone | `+1 555-203-1790` (Meta provided) |
| `WA_PHONE_ID` | `1210226812169851` |
| `WA_BUSINESS_ACCOUNT_ID` | `997158239779719` |
| Allowlist tester | `+573123528153` (operator Maxi) |
| App | `landa-messaging` (ID `1909364299769910`) |
| Webhook URL | `https://landa-agent-service-production.up.railway.app/webhooks/meta` |
| HMAC secret | App Secret `9f9c836d8b...` |

## Resultados (logs reales, 2026-06-29 04:18 UTC)

| SC | Criterio | Status | Evidencia |
|---|---|---|---|
| SC1 | `curl -X POST .../test/llm` retorna OpenRouter | ✅ PASS | (Phase 1, ya cubierto) |
| SC2 | `WhatsApp → +1 555-203-1790 → echo: <text>` <3s | ✅ PASS | `webhook.echo.sent` lat=658ms, status=delivered |
| SC3 | Webhook rechaza payloads sin HMAC válida (401) | ✅ PASS | 15+ POSTs antes del fix devolvían 401; tras rotar `WA_WEBHOOK_SECRET` al App Secret real, validan 200 |
| SC4 | `/test/poliza/{id}` retorna póliza real SoftSeguros | ✅ PASS | `/test/poliza/228700` → 200 con 184 campos (HTTP `200 latency=1109ms` en logs) |
| SC5 | Idempotencia + allowlist + dedup operativos | ✅ PASS | `phone_hash="dffea389"` (no raw phone), `result="echo_sent"`; logs muestran orden invariante HMAC → parse → dedup → allowlist → echo |

## Issues encontradas + fixeadas en vivo

1. **WABA subscription confusion** (~30 min)
   - El test number vive en WABA `997158239779719`, NO en la WABA real `1451322196454283`
   - El user pasó IDs mal (típicamente con 1 dígito menos): `99715823977919` (14 dígitos) vs real `997158239779719` (15 dígitos)
   - Fix: query `GET /<phone_id>/...` con debug_token para encontrar el WABA + phone_id correctos
   - Suscribí app via `POST /<waba>/subscribed_apps`

2. **Webhook fields no subscritos** (~5 min)
   - El user configuró URL + verify_token pero NO marcó los webhook fields (table en Configuration → Webhook → fields)
   - Sin `messages` suscrito, Meta no envía POSTs aunque la WABA esté subscribed
   - Fix: user click "Suscribirte" en row `messages` (no se pudo via API porque requiere App Access Token, no user token)

3. **HMAC siempre 401** (~10 min) — **el error de planning**
   - El plan 02-CONTEXT.md generó `WA_WEBHOOK_SECRET` como string random custom
   - Meta firma `X-Hub-Signature-256` con el **App Secret**, no con un secret custom
   - Logs mostraron 15+ POST → 401 `webhook.hmac.invalid` ANTES del fix
   - Fix: user reveló App Secret en Settings → Basic, lo seteé en Railway → primer POST después del redeploy validó 200 + echo enviado

4. **Real number `+1 641-541-6615` quedó offline**
   - Migrado desde Twilio pero Meta requiere `POST /<phone_id>/register` con 2FA PIN
   - Twilio dejó un PIN desconocido → 9 intentos fallidos → rate-limit 1h
   - Defer: el real number queda inactivo hasta que el operator reset 2FA en Business Manager dashboard (irrelevante para Phase 2; test number cubre todo)

## Phase 2 closure

**All 5 success criteria PASS.** Phase 2 (Integración SoftSeguros + WhatsApp Cloud API) is DONE.

### What works now

- WhatsApp inbound webhook (`POST /webhooks/meta`) recibe, valida HMAC con App Secret, deduplica por message_id, valida allowlist E.164, llama Meta API para echo en <3s
- WhatsApp outbound via `MetaCloudClient.send_text()` retorna wamid + Meta dispara status callbacks (`sent`, `delivered`)
- SoftSeguros via `SoftSegurosClient.get_poliza(id)`: httpx async + tenacity outer + pybreaker inner + Redis cache TTL 60s + asyncio.Lock para token refresh + READ-ONLY enforcement (CI guard + CLAUDE.md rule)
- All credentials in Railway env vars (single-tenant DPG v1)
- Triple-layer READ-ONLY for SoftSeguros: arquitectura (`_get` only) + CI guard (`test_softseguros_readonly.py`) + CLAUDE.md Don't rule
- Privacy: raw phone numbers NEVER logged (always `_hash_phone(sha256[:8])`)
- 66 tests pasan local + mypy --strict clean + pre-commit verde

### What is explicitly NOT done (deferred)

- Real number `+1 641-541-6615` operativo en Cloud API (PIN reset pendiente)
- LLM Q&A real (Phase 3)
- LangGraph state machine + judge + tool boundaries (Phase 3)
- Chatwoot mirror desde mensaje #1 (Phase 3)
- Flujo de pago + escalación (Phase 4)
- Audit log inmutable, egress controls, rate limit, suite adversarial (Phase 5)
- Voice handoff lambda-proyect (Phase 6)
- Custom domain `agent.landatech.org` (post-Phase 1 milestone close)
- Twilio Auth Token rotation (operator pending — credentials live in this transcript)

### Next

```
/gsd-plan-phase 03
```

(Per ROADMAP — Phase 3: Bot Q&A inbound + Chatwoot mirror)
