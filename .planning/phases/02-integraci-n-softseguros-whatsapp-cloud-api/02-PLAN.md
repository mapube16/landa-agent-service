---
phase: 02-integraci-n-softseguros-whatsapp-cloud-api
type: phase-overview
plans:
  - 02-01-PLAN.md
  - 02-02-PLAN.md
  - 02-03-PLAN.md
  - 02-04-PLAN.md
waves: 3
mvp_mode: false
tdd_mode: false
---

# Phase 2 — Integración SoftSeguros + WhatsApp Cloud API (Overview)

Round-trip primer mensaje cliente ↔ bot end-to-end via Meta Cloud API + cliente SoftSeguros funcional aislado para consultas REST. **NO LLM, NO LangGraph, NO judge, NO flujo de pago, NO Chatwoot mirror, NO audit log, NO rate limiting.** Esos viven en Phase 3+.

Después de F2 funciona:

1. **Round-trip WhatsApp echo (allowlisted)** — enviar al `+16415416615` → webhook Railway valida HMAC + dedup → echo bot responde `"echo: <texto>"` solo a números en `WA_ECHO_ALLOWLIST`. Cualquier otro número: HTTP 200 + log `ignored`, sin side effect outbound.
2. **Cliente SoftSeguros aislado** — `GET /test/poliza/{poliza_id}` retorna JSON crudo (tenacity + pybreaker + Redis cache 60s + auth token refresh con `asyncio.Lock`). Sin LLM en el path.
3. **HMAC SHA-256** validado en cada `POST /webhooks/meta` (D-16, `hmac.compare_digest`).
4. **Idempotencia** por `message_id` Redis `SET NX EX 86400` antes de cualquier side effect (D-14, D-15).
5. **SoftSeguros READ-ONLY enforcement** — código sin métodos write + CI guard test + regla en CLAUDE.md. (Operator special request, ver Plan 02-03.)

## Plan files

| # | Plan | Wave | Autonomous | Goal one-liner |
|---|---|---|---|---|
| 01 | [Settings + models + module skeletons + pre-commit deps](./02-01-PLAN.md) | 1 | yes | `WhatsAppSettings(env_prefix="WA_")` + `SoftSegurosSettings(env_prefix="SOFTSEGUROS_")` + `LangSmithSettings.workspace_id`, Pydantic models `InboundEnvelope`/`OutboundText`/`MetaError` en `app/models/meta.py`, module skeletons (`app/integrations/_circuit.py`, `app/integrations/meta_cloud.py`, `app/integrations/softseguros.py`, `app/webhooks/meta.py`, `app/features/handoff/echo.py`, `app/models/meta.py`, `app/models/softseguros.py`), pre-commit mypy `additional_dependencies += tenacity==9.1.4, pybreaker==1.4.1`, conftest extiende env vars |
| 02 | [Meta Cloud API integration (client + webhook + HMAC + idempotency + echo)](./02-02-PLAN.md) | 2 | yes | `MetaCloudClient.send_text()` + `get_meta_client()` factory cached singleton, `app/webhooks/meta.py` con `GET /webhooks/meta` (challenge) + `POST /webhooks/meta` (raw body → HMAC → parse → dedup → allowlist → echo), `is_echo_allowed()` + `format_echo()` en `app/features/handoff/echo.py` con E.164 normalization, integración en `app/main.py` lifespan + router, tests HMAC valid/invalid/missing-header, idempotency dup-skip, echo allowlist truth table, factory cache identity |
| 03 | [SoftSeguros integration (client + token refresh + cache + tenacity + pybreaker)](./02-03-PLAN.md) | 2 | yes | `async_call(breaker, fn)` wrapper en `app/integrations/_circuit.py` (pybreaker 1.4.1 lacks asyncio), `SoftSegurosClient` con `_get()` único primitivo HTTP (NO `_post/_put/_patch/_delete` — operator READ-ONLY directive), `_get_token()` + `_refresh_token_on_401()` con `asyncio.Lock`, tenacity outer / pybreaker inner stack, Redis read-through cache (TTL 60s key `softseguros:{poliza_id}:{query_type}`), 4 public methods `get_poliza`/`get_cliente`/`get_estado`/`get_pagos`, integración en lifespan + `GET /test/poliza/{poliza_id}` endpoint, CI guard test `tests/test_softseguros_readonly.py` que falla si aparecen métodos write, CLAUDE.md `Don't` actualizado |
| 04 | [End-to-end smoke verification (operator-driven)](./02-04-PLAN.md) | 3 | no | Operador envía WhatsApp real desde número en allowlist → verifica echo recibido; hit `/test/poliza/{poliza_real_dpg}` → verifica JSON. Plus: operador rota `WA_TOKEN`, setea env vars finales en Railway (`WA_*`, `SOFTSEGUROS_*`, `WA_ECHO_ALLOWLIST`, `LANGSMITH_WORKSPACE_ID`), suscribe `messages` + `message_status` en Meta. Documenta resultado en `02-SMOKE.md`. |

## Parallelization

| Wave | Plans concurrentes | Notas |
|---|---|---|
| **1** | `02-01` | Settings + models + skeletons + pre-commit. Wave única; todo lo demás depende de los contracts que define. |
| **2** | `02-02`, `02-03` | Disjuntos en `files_modified`. Plan 02-02 toca Meta (`integrations/meta_cloud.py`, `webhooks/meta.py`, `features/handoff/echo.py`). Plan 02-03 toca SoftSeguros (`integrations/softseguros.py`, `integrations/_circuit.py`). Ambos integran en `app/main.py`. **Conflict point:** `app/main.py` lo modifican los dos — Plan 02-02 añade `meta_router` include + `app.state.meta`; Plan 02-03 añade `app.state.softseguros` + `/test/poliza/{id}` endpoint. Coordinación: Plan 02-02 corre primero por convención (alfabético) y Plan 02-03 rebasea — ver `<merge_strategy>` en cada plan. |
| **3** | `02-04` | Smoke E2E — depende de los dos wires-up para tener Railway live con WA + SoftSeguros funcionando. Requiere checkpoints humanos (rotar token, enviar WhatsApp real). |

## Dependency graph

```
                              Wave 1
                              ──────
                ┌────────────────────────────────────┐
                │ 02-01 (Settings + Pydantic models  │
                │ InboundEnvelope/OutboundText/      │
                │ MetaError, module skeletons,       │
                │ pre-commit mypy deps, conftest)    │
                └─────────────────┬──────────────────┘
                                  │
                  ┌───────────────┴────────────────┐
                  │                                │
              Wave 2                           Wave 2
              ──────                           ──────
       ┌──────────────────────┐     ┌──────────────────────────────┐
       │ 02-02 (Meta Cloud +  │     │ 02-03 (SoftSeguros client +  │
       │ webhook + HMAC +     │     │ tenacity + pybreaker +       │
       │ idempotency + echo)  │     │ asyncio.Lock + cache +       │
       │                      │     │ READ-ONLY CI guard)          │
       └──────────┬───────────┘     └──────────────┬───────────────┘
                  │                                │
                  └───────────────┬────────────────┘
                                  │
                              Wave 3
                              ──────
                ┌────────────────────────────────────┐
                │ 02-04 (E2E smoke con WhatsApp real │
                │ + /test/poliza/{real DPG poliza} + │
                │ Railway env-vars wireup + token    │
                │ rotation + Meta subscriptions)     │
                └────────────────────────────────────┘
```

## Goal-backward verification

Cada CONTEXT decision (D-XX) + ROADMAP requirement mapea a el(los) plan(es) que lo satisface:

| Source | Item | Plan(s) responsables | Verificación |
|---|---|---|---|
| ROADMAP REQ-2.1 | Q&A inbound (póliza por número) — canal de identificación | 02-03 (SoftSeguros) + 02-04 (smoke `/test/poliza/{id}`) | `curl /test/poliza/{poliza_real}` retorna JSON |
| ROADMAP REQ-2.2 | SoftSeguros tiempo real (caché + circuit breaker) | 02-03 | tests unit cache hit/miss + breaker open simulation |
| ROADMAP REQ-2.3 | HMAC `X-Hub-Signature-256` en webhooks Meta | 02-02 | `tests/test_webhooks_meta.py` cases valid/invalid/missing |
| ROADMAP REQ-2.4 | Idempotencia por `message_id` | 02-02 | `tests/test_webhooks_meta.py` dup-skip test |
| CONTEXT D-01 | SoftSeguros creds via env vars `SOFTSEGUROS_USERNAME/PASSWORD` SecretStr | 02-01 (`SoftSegurosSettings`) + 02-04 (Railway env-var wireup) | Setting carga + lifespan boots clean |
| CONTEXT D-02 | Echo bot allowlist via `WA_ECHO_ALLOWLIST` CSV | 02-01 (settings + validator) + 02-02 (`is_echo_allowed`) + 02-04 (Railway env-var wireup) | `tests/test_features_handoff_echo.py` truth table |
| CONTEXT D-03 | Meta Cloud API directo (NO Twilio) | 02-02 (`MetaCloudClient` via httpx → graph.facebook.com) | grep ausencia de `twilio` en `app/` |
| CONTEXT D-04 | Webhook URL `landa-agent-service-production.up.railway.app/webhooks/meta` | 02-02 (route declared) + 02-04 (operator subscribes en Meta dashboard) | Operador valida 200 desde Meta webhook tester |
| CONTEXT D-05 | Suscripciones `messages` + `message_status` | 02-04 (operator action) | Documentado en `02-SMOKE.md` |
| CONTEXT D-06 | Token rotation (capturado token en transcript → operador rota) | 02-04 (rotation checkpoint blocking-human) | `WA_TOKEN` nuevo en Railway, viejo revocado en Meta dashboard |
| CONTEXT D-07 | Hardcoded local (NO `landa-shared` submodule yet) | 02-03 (`app/integrations/softseguros.py` self-contained) | grep ausencia de `landa_shared` import |
| CONTEXT D-08 | `META_API_VERSION = "v21.0"` constante | 02-01 (skeleton `app/integrations/meta_cloud.py` declara constante) + 02-02 (usa) | grep `META_API_VERSION` literal `v21.0` |
| CONTEXT D-09 | Endpoints `GET /webhooks/meta` (challenge) + `POST /webhooks/meta` (events) | 02-02 | `tests/test_webhooks_meta.py` ambos endpoints |
| CONTEXT D-10 | `GET /test/poliza/{poliza_id}` retorna JSON crudo | 02-03 (endpoint en `main.py`) | `curl /test/poliza/123` retorna 200 con JSON shape de SoftSeguros |
| CONTEXT D-11 | httpx async + tenacity 3 retries + pybreaker 5/30s + Redis cache TTL 60s | 02-03 | tests unit retry/breaker/cache |
| CONTEXT D-12 | Token via `POST /api-token-auth/` cached + refresh on 401 | 02-03 (`_get_token` + `_refresh_token_on_401` con `asyncio.Lock`) | tests unit token refresh race |
| CONTEXT D-13 | 4 endpoints SoftSeguros consumidos | 02-03 (`get_poliza`/`get_cliente`/`get_estado`/`get_pagos`) | tests unit cada uno |
| CONTEXT D-14 | Redis key `wa:msg:{id}` TTL 24h SET NX | 02-02 | grep + `tests/test_webhooks_meta.py` dup test |
| CONTEXT D-15 | Idempotency check ANTES de side effects | 02-02 (orden HMAC → parse → dedup → allowlist → echo documentado en código) | `tests/test_webhooks_meta.py` dup test asserta `meta.send_text` NO llamado |
| CONTEXT D-16 | HMAC `hmac.compare_digest` (NO `==`) | 02-02 (`_verify_signature` helper) | `tests/test_webhooks_meta.py` bad-sig case + grep code ausencia de `==` en HMAC path |
| CONTEXT D-17 | `verify_token` ≠ `webhook_secret` (dos SecretStr distintos) | 02-01 (`WhatsAppSettings` campos separados) + 02-02 (GET usa verify_token, POST usa webhook_secret) | tests unit ambos campos cargados |
| CONTEXT Specifics § media | Imagen/audio/sticker/location: responder `"echo: [media type] received"` | 02-02 (`format_media_echo`) | test unit cada `message.type` |
| CONTEXT Specifics § logging | Log `result` enum: `echo_sent|ignored_not_allowlisted|ignored_duplicate|error` + `phone_hash` (NO raw phone) | 02-02 (handler logs estructurados) | grep no `from_phone=` raw, sí `phone_hash=` |
| RESEARCH Pitfall 9 | `LANGSMITH_WORKSPACE_ID` deferred from Phase 1 | 02-01 (`LangSmithSettings.workspace_id: SecretStr | None = None`) + 02-04 (Railway env-var wireup) | `Settings()` carga con + sin la var |
| RESEARCH State-of-art § workers | 1-worker assumption documented for pybreaker per-process state | 02-04 (nota en `02-SMOKE.md` + extender `RAILWAY_AGENT_NOTES.md`) | grep nota en runbook |
| Operator special request | SoftSeguros READ-ONLY enforcement (architecture + CI test + CLAUDE.md rule) | 02-03 (3 entregables atómicos) | `tests/test_softseguros_readonly.py` pass + grep CLAUDE.md `❌ No agregar métodos write` |

**Coverage status:** ✅ todos los items COVERED. No gaps. No phase split required.

## Notes for executors

- **Wave 2 file conflict en `app/main.py`:** Plan 02-02 y Plan 02-03 ambos lo editan. Por convención lexicográfica, Plan 02-02 corre primero (registra `meta_router` + `app.state.meta`); Plan 02-03 rebasea sobre el resultado (añade `app.state.softseguros` + `/test/poliza/{id}` debajo). Ver `<merge_strategy>` en cada plan para los pasos exactos.
- **Pre-commit mypy gotcha:** repetir el patrón de plans 01-02 y 01-04 — agregar `tenacity==9.1.4` y `pybreaker==1.4.1` a `.pre-commit-config.yaml` `additional_dependencies` **en el mismo commit** que introduce los imports en `app/integrations/`. De lo contrario, el primer commit falla pre-commit y requiere un fix-up commit.
- **Reading order para executors:** RESEARCH.md Patterns 1-5 + Pitfalls 1-10 son canon. PATTERNS.md mapea cada archivo nuevo a su analog en el repo con line numbers — leerlo antes de escribir cualquier archivo nuevo evita el "scavenger hunt".
- **NEVER copiar el explicit `__aenter__`/`__aexit__` pattern del checkpointer** para los clientes Meta/SoftSeguros. Son singletons plain httpx — `app.state.X = get_X_client()` y listo, no necesitan teardown asíncrono (PATTERNS.md Pitfall 1).
- **NEVER `==` para HMAC.** D-16 lo prohíbe. Solo `hmac.compare_digest`. Aunque sea "solo en tests".
- **NEVER reordenar HMAC → parse → dedup → allowlist → echo.** D-15. Cualquier reorden rompe la garantía de idempotencia.
- **NEVER agregar `_post`/`_put`/`_patch`/`_delete` en `SoftSegurosClient`.** Operator special request hace eso un commit-block, no una preferencia.
