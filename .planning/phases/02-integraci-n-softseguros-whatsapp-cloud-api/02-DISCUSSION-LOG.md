# Phase 2: Integración SoftSeguros + WhatsApp Cloud API - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-28
**Phase:** 02-Integración SoftSeguros + WhatsApp Cloud API
**Areas discussed:** Shared code, Tenant config storage, Echo bot scope, WhatsApp Business setup

---

## Shared code (landa-agent ↔ lambda-proyect)

| Option | Description | Selected |
|--------|-------------|----------|
| A. Hardcoded local en F2, refactor a shared package después | Duplicamos SoftSegurosAdapter en este repo ahora; refactor a `landa-shared` git submodule cuando F6 (voice handoff) lo demande. Riesgo de drift durante F3-F5 | ✓ (Claude's discretion) |
| B. Crear `landa-shared` submodule YA en F2 | Setup del submodule + extracción del adapter desde lambda-proyect ANTES de tocar nada. ~1 día extra | |
| C. Adapter local + interface contract documentado | Local PERO con ADR + protocolo Pydantic que matchea lambda-proyect | |

**User's choice:** No explícita — Claude eligió A por consistencia con la pragmática "DPG primero, multi-tenant después" expresada en otras decisiones.
**Notes:** Refactor estimado en 1 día cuando F6 lo demande. Bajo riesgo de drift porque hay 1 solo archivo (`integrations/softseguros.py`) duplicado.

---

## Storage de credenciales SoftSeguros

| Option | Description | Selected |
|--------|-------------|----------|
| A. Env vars en Railway (single-tenant v1) | `SOFTSEGUROS_USERNAME` + `SOFTSEGUROS_PASSWORD` como SecretStr. Cero setup. Refactor cuando entre cliente #2 | ✓ |
| B. Postgres tabla `tenant_configs` con encryption-at-rest | Schema + alembic + crypto helper. Multi-tenant friendly desde F2 | |
| C. Mongo `db.tenant_configs` compartido con lambda-proyect | Requiere acceso al Mongo de lambda-proyect, checkpoint humano | |

**User's choice:** A
**User's words:** "Si, opcion A variables en railway lo importante es DPG despues miramos multitenant"
**Notes:** Refactor a Postgres `tenant_configs` (opción B) estimado en 4-6h cuando llegue cliente #2. No bloqueante.

---

## Echo bot scope (Phase 2 transitional bot)

| Option | Description | Selected |
|--------|-------------|----------|
| A. Allowlist de números de prueba | `WA_ECHO_ALLOWLIST=+5491...` en env. Solo esos reciben echo. Cualquier otro: ignorado | ✓ |
| B. Solo en `APP_ENV=dev`, prod ignora | En dev: echo a todos. En prod: webhook recibe pero no responde | |
| C. Cualquiera en cualquier env | Echo a todo lo que entre | |

**User's choice:** A
**User's words:** "Echo bot scope: A (allowlist)"
**Notes:** Cero riesgo de cliente real recibiendo "echo: ..." raro. Operador debe pasar la lista de números de prueba antes del deploy de F2.

---

## WhatsApp Business setup (BSP migration)

| Option | Description | Selected |
|--------|-------------|----------|
| A. Ya registrado, tengo todas las credenciales | Listo para configurar webhook URL apuntando a Railway | |
| B. Aún NO registrado — F2 incluye setup Meta | Crear app en developers.facebook.com, registrar número, generar System User Token. ~2h setup humano | |
| C. Registrado pero apuntaba a otro webhook (Twilio) — hay que migrarlo | Cambiar webhook URL en Meta App Dashboard. Coordinación con BSP previo | ✓ |

**User's choice:** C (implicit)
**User's words:** "hagamoslo todo bien desde ya, vamos por cloud api, tenemos que deslinkarlo de twilio"
**Notes:** Migración Twilio → Meta completada en sesión:
- Twilio sender `XEa7ead03ff7a3ce579757d7fb95430bbd` releaseado via API (DELETE /v2/Channels/Senders/{sid}, HTTP 204)
- Meta WABA `LandaTech` (id `1451322196454283`) reclamado, número `+1 641-541-6615` verificado, `platform_type=CLOUD_API`, `quality_rating=GREEN` GREEN
- System User Token generado (App `landa-messaging`, id `1909364299769910`), scopes correctos, expiration Never
- Credenciales capturadas: `WA_TOKEN`, `WA_PHONE_ID=1267241483129092`, `WA_BUSINESS_ACCOUNT_ID=1451322196454283`, `WA_WEBHOOK_SECRET=e556d909...`, `WA_VERIFY_TOKEN=96715a9c...`
- ⚠️ `WA_TOKEN` quedó en transcript de chat → operador debe rotarlo antes del deploy de F2

---

## Claude's Discretion

- **Meta API version**: pin a `v21.0` (la del ROADMAP `v18.0` está stale).
- **Webhook layout**: `GET/POST /webhooks/meta` (vertical-slice convention).
- **Cliente HTTP de SoftSeguros**: httpx async + tenacity (3 retries exponential backoff) + pybreaker (5 failures → 30s open) + Redis cache TTL 60s. Patterns operacionales tomados directamente del ROADMAP sin debate.
- **Idempotency key shape**: Redis `wa:msg:{message_id}` SET NX, TTL 24h.
- **HMAC validation**: `hmac.compare_digest(...)` exclusivamente, nunca `==`.
- **Test fixtures HMAC**: pre-computed signature en `tests/test_webhooks_meta.py`.
- **Cliente SoftSeguros factory shape**: replica el patrón `@lru_cache` de `get_llm(role)` en `app/integrations/openrouter.py`.

## Deferred Ideas

- Cartera allowlist (números autorizados a validar pagos) → Phase 4
- LangGraph state machine para Q&A → Phase 3
- Chatwoot mirror desde el primer mensaje → Phase 3
- Output firewall → Phase 4/5
- Audit log inmutable con hash chain → Phase 5
- Rate limiting multi-nivel → Phase 5
- Refactor a `landa-shared` git submodule → Phase 6
- Custom domain `agent.landatech.org` → post-Phase 1 milestone close
- Postgres `tenant_configs` table → milestone futuro (cliente #2)
- API token de Chatwoot + cartera DPG agents invitados → Phase 3 prerequisite
