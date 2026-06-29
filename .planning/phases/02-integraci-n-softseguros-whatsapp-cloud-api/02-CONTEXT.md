# Phase 2: Integración SoftSeguros + WhatsApp Cloud API - Context

**Gathered:** 2026-06-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Round-trip primer mensaje cliente ↔ bot end-to-end via WhatsApp Meta Cloud API + cliente SoftSeguros funcional aislado para consultas REST. NO hay LLM, NO hay LangGraph state machine, NO hay flujo de pago, NO hay judge ni security pipeline.

**Concretamente, después de Phase 2 funciona:**

1. **Round-trip WhatsApp**: enviar WhatsApp al `+16415416615` → llega como webhook a Railway → bot responde con `"echo: <texto>"` → cliente lo recibe en WhatsApp. Solo para números en allowlist.
2. **Cliente SoftSeguros**: endpoint interno `/test/poliza/{poliza_id}` retorna JSON crudo de SoftSeguros (saldo, estado, coberturas, etc.) sin pasar por LLM. Verifica que httpx + auth + retry + circuit breaker + caché funcionan.
3. **Verificación HMAC** en cada webhook entrante de Meta (`X-Hub-Signature-256`).
4. **Idempotencia por `message_id`** de Meta (deduplicación Redis TTL 24h).

Lógica de bot real (Q&A inteligente con LangGraph + tools) es Phase 3.

</domain>

<decisions>
## Implementation Decisions

### Storage de credenciales SoftSeguros

- **D-01:** Env vars en Railway (`SOFTSEGUROS_USERNAME`, `SOFTSEGUROS_PASSWORD` como `SecretStr`), single-tenant DPG v1. Mismo patrón que las 5 credenciales existentes en `app/config/settings.py`.
- **Rationale:** YAGNI sobre multi-tenant. Cliente #2 no está en horizonte inmediato. Refactor a tabla `tenant_configs` cuando llegue (estimado 4-6h de trabajo, no bloqueante).
- **Acción para el operador:** pasar `SOFTSEGUROS_USERNAME` y `SOFTSEGUROS_PASSWORD` de DPG en chat. Yo las seteo en Railway con `railway variable set` al final del plan, junto con los WA_* (ya capturadas).

### Echo bot scope

- **D-02:** Allowlist hardcoded de números de prueba via env var `WA_ECHO_ALLOWLIST=+5491134567890,+...`. Solo esos números reciben `"echo: <texto>"`. Cualquier otro número que escriba al `+16415416615`: webhook responde HTTP 200 pero NO envía mensaje (status `ignored` en log, sin side effect outbound).
- **Rationale:** seguridad — cero riesgo de cliente real recibiendo `"echo: ..."` confuso si Meta routea tráfico inesperado. Config mínima (1 env var). Removible trivialmente cuando Phase 3 entre con bot Q&A real.
- **Implementación:** una function `is_echo_allowed(phone) -> bool` en `app/features/handoff/echo.py` (carpeta `handoff` porque es transitional pre-Phase 3; se borra cuando Phase 3 reemplace echo por LangGraph).

### WhatsApp Business setup

- **D-03:** **Meta Cloud API directo** (NO Twilio). Migración Twilio → Meta completada el 2026-06-28: sender `XEa7ead03ff7a3ce579757d7fb95430bbd` releaseado via API; WABA `LandaTech` (id `1451322196454283`) ya en Meta directo con `platform_type=CLOUD_API`, `code_verification_status=VERIFIED`, `quality_rating=GREEN`.
- **D-04:** Webhook URL pública: `https://landa-agent-service-production.up.railway.app/webhooks/meta` (custom domain `agent.landatech.org` queda diferido al final del milestone, ya planeado en post-Phase 1).
- **D-05:** Suscripciones del webhook: `messages` (mensajes entrantes), `message_status` (delivery/read receipts del bot).
- **D-06:** Token: System User Token de la app `landa-messaging` (App ID `1909364299769910`), expiration **Never**, scopes `whatsapp_business_messaging` + `whatsapp_business_management` + `whatsapp_business_manage_events`. (El token capturado el 2026-06-28 quedó en transcript → operador debe rotarlo y darme el nuevo antes del wire-up final).

### Shared code entre landa-agent y lambda-proyect

- **D-07:** **Hardcoded local en F2**, refactor a `landa-shared` git submodule cuando F6 (voice handoff) lo demande. Cero coordinación con equipo lambda-proyect requerida en F2.
- **Rationale:** YAGNI. La duplicación temporal es 1 archivo (`integrations/softseguros.py`), bajo riesgo de drift en 4 fases. Cuando F6 conecte ambos repos, el refactor es: extraer adapter + Pydantic models a `landa-shared` y reemplazar import en ambos lados (estimado 1 día). Hacerlo ahora costaría más por la coordinación.

### Meta Cloud API version

- **D-08:** Pin a `v21.0` (la versión estable más reciente al 2026-06-28). Plan original del ROADMAP decía v18.0 pero está stale; v18 entra en deprecation Q1 2026.
- **Implementación:** constante en `app/integrations/meta_cloud.py:META_API_VERSION = "v21.0"`. Si Meta deprecates v21, bump puntual ahí.

### Webhook layout y endpoints

- **D-09:** Endpoints F2 (vertical-slice en `app/webhooks/meta.py`):
  - `GET /webhooks/meta` — verification challenge de Meta (responde con `hub.challenge` si `hub.verify_token == settings.wa.verify_token`)
  - `POST /webhooks/meta` — recibe mensajes + status updates, valida HMAC, idempotency check, despacha a echo (si allowlisted) o ignora
- **D-10:** Endpoint test SoftSeguros: `GET /test/poliza/{poliza_id}` — retorna JSON crudo (no LLM) para verificar el cliente. Igual que `/test/llm` y `/test/sentry`, queda gateado/removido en Phase 5.

### Cliente SoftSeguros — patterns operacionales

- **D-11:** httpx async + tenacity (3 retries exponential backoff sobre `httpx.HTTPError` + `httpx.TimeoutException`) + pybreaker (5 failures → 30s open) + Redis cache `softseguros:{poliza_id}:{query_type}` con TTL 60s. Per ROADMAP.
- **D-12:** Auth: token via `POST /api-token-auth/` al boot (cached en process memory + refresh on 401). Token vive en módulo singleton; no en Redis (single instance escalado horizontal post-Phase 1 mediante stickiness o Redis-cached, evaluamos en Phase 3+).
- **D-13:** Endpoints SoftSeguros consumidos en F2: `/api/poliza/{id}/`, `/api/cliente/{id}/`, `/api/estadopoliza/{poliza_id}/`, `/api/pagopoliza/?poliza_id=` (los 4 listados en PROJECT.md context).

### Idempotencia + dedup

- **D-14:** Redis key `wa:msg:{message_id}` con TTL 24h. SET NX; si ya existe, log "duplicate, skipping" y responder 200 al webhook (Meta puede reentregar hasta 24h).
- **D-15:** Idempotencia se valida ANTES de cualquier side effect (echo response, log estructurado de turn, etc.).

### Webhook security

- **D-16:** HMAC SHA-256 validation: `hmac.compare_digest(expected, header_signature)` donde `expected = hmac.new(WA_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()`. Comparison `==` queda prohibida — solo `compare_digest`. Si HMAC falla: HTTP 401, log structured (sin pegar el body crudo en log).
- **D-17:** El `verify_token` (challenge GET) y el `webhook_secret` (HMAC POST) son **dos strings distintos**, ambos generados por el operador en setup Meta. Capturados en este CONTEXT.

### Captured credentials (para wire-up en Plan)

```
WA_TOKEN                = EAAbIjoT...kAZDZD  (PENDING ROTATION — quedó en transcript)
WA_PHONE_ID             = 1267241483129092
WA_BUSINESS_ACCOUNT_ID  = 1451322196454283
WA_WEBHOOK_SECRET       = e556d909fcb2d4dd3e573a28eafccda7
WA_VERIFY_TOKEN         = 96715a9c2658c915544f2faf735b98c0
META_API_VERSION        = v21.0
SOFTSEGUROS_BASE_URL    = https://app.softseguros.com/
SOFTSEGUROS_USERNAME    = (pendiente — operador me pasa después)
SOFTSEGUROS_PASSWORD    = (pendiente — operador me pasa después)
WA_ECHO_ALLOWLIST       = (pendiente — operador me pasa números de prueba)
```

### Claude's Discretion

- Webhook idempotency key shape y TTL exacto (sub-decisión de D-14).
- Logging schema para inbound/outbound messages (sigue patron structlog ya wireado en plan 01-02).
- Test fixtures para HMAC validation (un body conocido + signature pre-computed).
- Error handling de Meta API: 429 rate-limit → log + retry-after; 4xx fatal → no retry, alert; 5xx → tenacity retry.
- Cómo organizar el código de Meta: cliente en `app/integrations/meta_cloud.py`, webhook handler en `app/webhooks/meta.py`, tipos Pydantic en `app/models/meta.py`.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level
- `.planning/PROJECT.md` — Q&A inbound, flujo de pago, 13 capas de seguridad, identificación cliente vía número de póliza, SoftSeguros endpoints (`/api/poliza/`, `/api/cliente/`, `/api/estadopoliza/`, `/api/pagopoliza/`)
- `.planning/ROADMAP.md` §"Phase 2" — deliverables originales, success criteria, scope creep guard
- `CLAUDE.md` — vertical slice (`features/`, `integrations/`, `webhooks/`), reglas críticas (NO Twilio, NO SDK directo, idempotencia, HMAC en cada webhook, scope locked por póliza, no list-all tools, allowlist)

### Phase 1 artifacts (carry-forward)
- `.planning/phases/01-setup-infra/CONTEXT.md` §"AsyncPostgresSaver lifespan", §"asgi-correlation-id", §"PII redaction" — patterns vivos
- `.planning/phases/01-setup-infra/01-04-SUMMARY.md` — FastAPI `lifespan` pattern, `/health` probes, asgi-correlation-id wiring (Phase 2 webhook handler usa el mismo pattern para `X-Request-ID`)
- `.planning/phases/01-setup-infra/01-05-SUMMARY.md` — env vars en Railway, GraphQL workaround para service config
- `.planning/phases/01-setup-infra/RAILWAY_AGENT_NOTES.md` — runbook ops para wire-up de nuevos env vars
- `app/config/settings.py` — patrón `SecretStr` + `env_prefix` + `Field(default_factory=...)` que F2 extiende con `WhatsAppSettings` + `SoftSegurosSettings`
- `app/integrations/openrouter.py` — patrón factory + `@lru_cache` que el cliente SoftSeguros puede replicar (`get_softseguros_client()`)
- `app/healthcheck.py` — patrón `_probe(coro, timeout_s=1.0)` que el plan puede extender con probe SoftSeguros (opcional, no requirement F2 — pero queda barato)

### External docs (Meta Cloud API)
- https://developers.facebook.com/docs/whatsapp/cloud-api — root docs (versioned by `META_API_VERSION`)
- https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks — webhook payload spec, HMAC verification
- https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages — `POST /{phone_id}/messages` spec
- https://developers.facebook.com/docs/whatsapp/cloud-api/guides/migrate-existing-whatsapp-number-to-business-platform — ya completado, ref por si falla algo

### External docs (SoftSeguros)
- `https://app.softseguros.com/` — root (auth via `POST /api-token-auth/`)
- No public API docs externos — el contrato es el que DPG ya usa en lambda-proyect. Si hay dudas de schema, examinar el código de lambda-proyect en `cobranza/` (operador puede facilitar acceso).

### Security / threat model
- `.planning/phases/01-setup-infra/CONTEXT.md` §"PII redaction" — structlog scrubber, sentry scrub_event (ambos ya wirados, F2 NO necesita re-implementar)
- PROJECT.md §"Seguridad y mitigación de prompt injection" — items 7-9 aplican a F2 (HMAC webhook, idempotency, allowlist números cartera — aunque "cartera allowlist" es F4, ya el echo allowlist es la primera instancia de este pattern)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`app/config/settings.py`** — añadir `WhatsAppSettings(env_prefix="WA_")` (token, phone_id, business_account_id, webhook_secret, verify_token, echo_allowlist) + `SoftSegurosSettings(env_prefix="SOFTSEGUROS_")` (base_url, username, password). Misma estructura que los 7 settings actuales.
- **`app/integrations/openrouter.py`** — patrón de factory + `@lru_cache(maxsize=8)`. Replicable para `get_softseguros_client()` y `get_meta_client()`.
- **`app/main.py`** lifespan — añadir initialization de los clientes Meta + SoftSeguros (no son async-resource-heavy como checkpointer, basta crear instancia singleton).
- **`app/healthcheck.py`** — `/health` puede añadir probes opcionales (softseguros, meta_api). NO requirement F2 pero barato.
- **structlog + correlation_id** ya wireado — el webhook handler hereda `X-Request-ID` automáticamente, los logs salen correlacionados sin trabajo extra.

### Established Patterns

- **`SecretStr`** para credenciales (`OPENROUTER_API_KEY`, `SENTRY_DSN`, `LANGSMITH_API_KEY` ya lo usan). F2 sigue igual con `WA_TOKEN`, `WA_WEBHOOK_SECRET`, `WA_VERIFY_TOKEN`, `SOFTSEGUROS_PASSWORD`.
- **Vertical slice**: `app/integrations/<service>.py` para clientes externos, `app/webhooks/<source>.py` para receivers. F2 crea `app/integrations/{softseguros,meta_cloud}.py` + `app/webhooks/meta.py`.
- **`async def _probe(coro, timeout_s)`** en healthcheck — patrón para "fire-and-forget con timeout".
- **`# noqa: BLE001`** + `type(exc).__name__` en respuestas públicas — para no leakear conn strings/PII en errores HTTP (mitigación T-01-15).
- **Pydantic v2** con `model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)`.
- **`asyncio.gather`** para probes/calls paralelos cuando hay budget de latencia.

### Integration Points

- **Lifespan** (`app/main.py`): añadir construcción de cliente Meta + cliente SoftSeguros como singletons (no necesitan teardown asíncrono).
- **app.state**: `app.state.softseguros = create_softseguros_client(settings)` y `app.state.meta = create_meta_client(settings)`. Accessible via `request.app.state` en handlers.
- **Routers**: nuevo `app/webhooks/meta.py` con `router = APIRouter(prefix="/webhooks", tags=["meta"])`. Incluir en `main.py` después de `health_router`.
- **`/test/poliza/{poliza_id}`**: endpoint nuevo en `main.py` (mismo patrón que `/test/llm`, `/test/sentry`).
- **Pre-commit mypy `additional_dependencies`**: añadir `tenacity==X` y `pybreaker==X` (pinear versiones en pyproject primero). Mismo patrón que las 4 deps añadidas en plan 01-04.

</code_context>

<specifics>
## Specific Ideas

- **Echo behavior** debe ser explícita: solo responder con `"echo: <texto exacto del usuario>"` para mensajes en allowlist. Si el cliente envía algo no-texto (imagen, audio, sticker, location), responder con `"echo: [media type] received"` para que F4 tenga el handler stub listo.
- **Logging structured** del webhook handler debe incluir `from_phone` (hashed if PII concern), `message_type`, `message_id`, `result` (echo_sent | ignored_not_allowlisted | ignored_duplicate | error). Esto facilita debugging y métricas post-F2.
- **HMAC test fixture**: incluir un body realista de webhook Meta + signature pre-computada para test unitario. Operador puede capturar uno real usando ngrok local en dev si quiere validar contra Meta-firmado.

</specifics>

<deferred>
## Deferred Ideas

- **Cartera number allowlist** (D-04 del threat model) — Phase 4 (escalación bidir + payment validation). F2 solo allowlists para echo testers.
- **LangGraph state machine para Q&A** — Phase 3 (Bot Q&A inbound). F2 deja el handler tirando a un stub `_handle_inbound_text` que en F3 routea al graph.
- **Chatwoot mirror desde el primer mensaje** — Phase 3. F2 solo logea local, no postea a Chatwoot.
- **Output firewall** sobre el echo response — Phase 4/5. F2 acepta que el echo es "literally repeat user text" y no necesita firewall (echo no es generación LLM).
- **Audit log inmutable** — Phase 5. F2 usa structlog (en memoria), no la tabla `audit_log` con hash chain.
- **Rate limiting multi-nivel** — Phase 5. F2 acepta rate limit nativo de Meta + límite implícito de allowlist.
- **Refactor a `landa-shared` submodule** — Phase 6 (voice handoff). Adapter SoftSeguros queda hardcoded local hasta entonces (D-07).
- **Custom domain `agent.landatech.org`** — post-Phase 1 milestone close. F2 usa Railway-default URL.
- **Postgres `tenant_configs` table** — milestone futuro (cliente #2). F2 mantiene env vars (D-01).

</deferred>

---

*Phase: 02-Integración SoftSeguros + WhatsApp Cloud API*
*Context gathered: 2026-06-28*
