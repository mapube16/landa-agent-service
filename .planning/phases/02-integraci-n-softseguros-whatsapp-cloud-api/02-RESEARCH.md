# Phase 2: Integración SoftSeguros + WhatsApp Cloud API — Research

**Researched:** 2026-06-28
**Domain:** External integration (HTTP clients) + webhook receiver + Redis idempotency
**Confidence:** HIGH on Meta + httpx + redis-py + tenacity + DRF token auth; MEDIUM on pybreaker asyncio interaction; LOW on SoftSeguros response shapes (no public docs)

## Summary

Phase 2 lands two flat integration modules (`integrations/softseguros.py`, `integrations/meta_cloud.py`) and one webhook receiver (`webhooks/meta.py`), plus a singleton-cached Redis dedup key for inbound `message_id`. All four building blocks (httpx async + tenacity + pybreaker + redis-py) are already pinned in `pyproject.toml` (tenacity==9.1.4, pybreaker==1.4.1, httpx==0.28.1, redis==8.0.1) and verified current on PyPI as of 2026-02 / 2025-09 respectively.

The single non-trivial integration risk is **pybreaker 1.4.1's lack of native asyncio support**: the library only ships Tornado integration via `call_async`. Plain `breaker.call(async_func, ...)` returns an unawaited coroutine and pybreaker registers the call as "success" before the upstream actually runs. Plan must use a small wrapper: read `breaker.state` to fail-fast when open, await the coroutine, then call `breaker._on_success()` / `breaker._on_failure(exc)` to drive the state machine. Alternative is to switch to `aiobreaker` or `purgatory` — but D-11 locks pybreaker, so the wrapper approach is the prescribed path.

**Primary recommendation:** Write a 20-line `async_call(breaker, coro_fn, *args)` helper in `integrations/_circuit.py`, decorate SoftSeguros HTTP methods with `@retry` (tenacity 9.x detects coroutines automatically), and stack `tenacity outer / pybreaker inner` so a single breaker-open trip aborts the retry loop instantly. Webhook handler reads `await request.body()` BEFORE FastAPI tries to Pydantic-parse, runs HMAC `compare_digest` against the raw bytes, then `await redis.set(f"wa:msg:{mid}", 1, nx=True, ex=86400)` for idempotency — if `None`, skip side effects and return 200.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01 — Storage de credenciales SoftSeguros:** Env vars en Railway (`SOFTSEGUROS_USERNAME`, `SOFTSEGUROS_PASSWORD` como `SecretStr`), single-tenant DPG v1. Mismo patrón que las 5 credenciales existentes en `app/config/settings.py`. Refactor a tabla `tenant_configs` cuando llegue cliente #2.

**D-02 — Echo bot scope:** Allowlist hardcoded de números de prueba via env var `WA_ECHO_ALLOWLIST=+5491134567890,+...`. Solo esos números reciben `"echo: <texto>"`. Cualquier otro número: webhook responde HTTP 200 pero NO envía mensaje (status `ignored` en log). Implementación: function `is_echo_allowed(phone) -> bool` en `app/features/handoff/echo.py` (carpeta `handoff` porque es transitional pre-Phase 3).

**D-03 — Meta Cloud API directo** (NO Twilio). Migración completada 2026-06-28. WABA `LandaTech` (id `1451322196454283`) con `platform_type=CLOUD_API`, `quality_rating=GREEN`.

**D-04 — Webhook URL pública:** `https://landa-agent-service-production.up.railway.app/webhooks/meta`. Custom domain `agent.landatech.org` diferido al final del milestone.

**D-05 — Webhook subscriptions:** `messages` (mensajes entrantes), `message_status` (delivery/read receipts).

**D-06 — Token:** System User Token de la app `landa-messaging` (App ID `1909364299769910`), expiration `Never`, scopes `whatsapp_business_messaging` + `whatsapp_business_management` + `whatsapp_business_manage_events`. El token capturado el 2026-06-28 quedó en transcript → operador debe rotarlo y darme el nuevo antes del wire-up final.

**D-07 — Shared code entre landa-agent y lambda-proyect:** Hardcoded local en F2, refactor a `landa-shared` git submodule cuando F6 (voice handoff) lo demande. Cero coordinación con equipo lambda-proyect requerida en F2.

**D-08 — Meta Cloud API version:** Pin a `v21.0` (versión estable al 2026-06-28). Plan original del ROADMAP decía v18.0 pero está stale; v18 entra en deprecation Q1 2026. Constante en `app/integrations/meta_cloud.py:META_API_VERSION = "v21.0"`.

**D-09 — Endpoints F2 (vertical-slice en `app/webhooks/meta.py`):**
- `GET /webhooks/meta` — verification challenge de Meta (responde con `hub.challenge` si `hub.verify_token == settings.wa.verify_token`)
- `POST /webhooks/meta` — recibe mensajes + status updates, valida HMAC, idempotency check, despacha a echo o ignora

**D-10 — Endpoint test SoftSeguros:** `GET /test/poliza/{poliza_id}` — retorna JSON crudo (no LLM). Mismo patrón que `/test/llm` y `/test/sentry`, gateado/removido en Phase 5.

**D-11 — Cliente SoftSeguros patterns operacionales:** httpx async + tenacity (3 retries exponential backoff sobre `httpx.HTTPError` + `httpx.TimeoutException`) + pybreaker (5 failures → 30s open) + Redis cache `softseguros:{poliza_id}:{query_type}` con TTL 60s.

**D-12 — SoftSeguros auth:** token via `POST /api-token-auth/` al boot (cached en process memory + refresh on 401). Token vive en módulo singleton; no en Redis.

**D-13 — Endpoints SoftSeguros consumidos en F2:** `/api/poliza/{id}/`, `/api/cliente/{id}/`, `/api/estadopoliza/{poliza_id}/`, `/api/pagopoliza/?poliza_id=`.

**D-14 — Idempotencia:** Redis key `wa:msg:{message_id}` con TTL 24h. SET NX; si ya existe, log "duplicate, skipping" y responder 200 al webhook (Meta puede reentregar hasta 24h).

**D-15 — Idempotencia se valida ANTES de cualquier side effect** (echo response, log estructurado de turn, etc.).

**D-16 — Webhook HMAC SHA-256 validation:** `hmac.compare_digest(expected, header_signature)` donde `expected = hmac.new(WA_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()`. Comparison `==` queda prohibida — solo `compare_digest`. Si HMAC falla: HTTP 401, log structured (sin pegar el body crudo en log).

**D-17 — `verify_token` (challenge GET) y `webhook_secret` (HMAC POST) son dos strings distintos**, ambos generados por el operador en setup Meta. Capturados en CONTEXT.

### Claude's Discretion

- Webhook idempotency key shape y TTL exacto (sub-decisión de D-14 — ya está D-14 fijo, queda margen para namespace tweaks).
- Logging schema para inbound/outbound messages (sigue patron structlog ya wireado en plan 01-02).
- Test fixtures para HMAC validation (un body conocido + signature pre-computed).
- Error handling de Meta API: 429 rate-limit → log + retry-after; 4xx fatal → no retry, alert; 5xx → tenacity retry.
- Cómo organizar el código de Meta: cliente en `app/integrations/meta_cloud.py`, webhook handler en `app/webhooks/meta.py`, tipos Pydantic en `app/models/meta.py`.

### Deferred Ideas (OUT OF SCOPE)

- **Cartera number allowlist** (D-04 del threat model) — Phase 4 (escalación bidir + payment validation). F2 solo allowlists para echo testers.
- **LangGraph state machine para Q&A** — Phase 3. F2 deja el handler tirando a un stub `_handle_inbound_text` que en F3 routea al graph.
- **Chatwoot mirror desde el primer mensaje** — Phase 3. F2 solo logea local.
- **Output firewall** sobre el echo response — Phase 4/5. F2 acepta que el echo es "literally repeat user text".
- **Audit log inmutable** — Phase 5. F2 usa structlog (en memoria), no la tabla `audit_log` con hash chain.
- **Rate limiting multi-nivel** — Phase 5. F2 acepta rate limit nativo de Meta + límite implícito de allowlist.
- **Refactor a `landa-shared` submodule** — Phase 6. Adapter SoftSeguros queda hardcoded local hasta entonces.
- **Custom domain `agent.landatech.org`** — post-Phase 1 milestone close. F2 usa Railway-default URL.
- **Postgres `tenant_configs` table** — milestone futuro (cliente #2). F2 mantiene env vars (D-01).
</user_constraints>

## Project Constraints (from CLAUDE.md)

Locked directives that the planner MUST honor:

- **NO Twilio para WhatsApp** — Meta Cloud API directo. (CLAUDE.md Don't #5)
- **NO SDK directo de Anthropic/OpenAI** — el factor `get_llm()` ya cubre esto y F2 no toca LLMs igual.
- **Verifica HMAC `X-Hub-Signature-256` en CADA webhook entrante** de Meta. (CLAUDE.md Do #11)
- **Idempotencia por `message_id`** — Meta puede reentregar webhooks. (CLAUDE.md Do #12)
- **Cachea consultas SoftSeguros en Redis con TTL 60s** — clave `(poliza_id, query_type)`. (CLAUDE.md Do #8)
- **Circuit breaker en SoftSeguros**: tras N fallos consecutivos, el bot escala a humano. **Nunca devolver data stale.** (CLAUDE.md Do #9)
- **Pydantic v2 para todo I/O** — tools, webhooks, configs, mensajes entre módulos. (CLAUDE.md Do #2)
- **Async por default**: FastAPI + httpx async + asyncpg + arq. Nada bloqueante. (CLAUDE.md "Convenciones #1")
- **structlog para logs**: JSON estructurado, PII redactada por default. (CLAUDE.md "Convenciones #2")
- **Pydantic v2 settings**: `BaseSettings` con `env_prefix` por dominio. (CLAUDE.md "Convenciones #3")
- **Type hints estrictos**: `mypy --strict` en CI. (CLAUDE.md "Convenciones #4")
- **Sin ABCs/Ports prematuros** — usa clases concretas. Solo extrae ABC cuando exista segunda implementación real. (CLAUDE.md Don't #6)
- **No commitear** `venv/`, `__pycache__/`, `.env`, credenciales. (CLAUDE.md Don't #7)

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| WhatsApp inbound webhook reception | API / Backend (`app/webhooks/meta.py`) | — | Owns HMAC verify, idempotency, dispatch. Pure FastAPI route. |
| WhatsApp outbound message send | API / Backend (`app/integrations/meta_cloud.py`) | — | httpx call to `graph.facebook.com`; no other tier touches outbound. |
| SoftSeguros HTTP client | API / Backend (`app/integrations/softseguros.py`) | Cache (Redis) | Owns auth refresh, retry, circuit breaker. Redis is read-through cache layer. |
| Idempotency dedup | API / Backend (handler in `webhooks/meta.py`) | Storage (Redis) | Logic lives in handler; Redis holds the dedup key with TTL. |
| Allowlist check | API / Backend (`app/features/handoff/echo.py`) | — | Pure function over settings. No external tier. |
| Test endpoints (`/test/poliza/{id}`) | API / Backend (`app/main.py`) | — | Same vertical slice as `/test/llm`, `/test/sentry`. Gated/removed in F5. |
| Settings (`WhatsAppSettings`, `SoftSegurosSettings`) | API / Backend (`app/config/settings.py`) | — | Extends existing per-domain BaseSettings pattern. |

**Out of scope for F2:** Browser / Client tier (no UI). Frontend Server / SSR (none). CDN / Static (none).

## Standard Stack

### Core (already pinned in `pyproject.toml`)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | 0.28.1 | Async HTTP client for Meta + SoftSeguros | Only mature async HTTP client with HTTP/2 + connection pool + event hooks. `[VERIFIED: pyproject.toml]` |
| tenacity | 9.1.4 | Retry on httpx failures | Latest stable, auto-detects async functions via `is_coroutine_callable()` so plain `@retry` works on coroutines. Released 2026-02-07. `[VERIFIED: npm registry equivalent — PyPI pip index]` |
| pybreaker | 1.4.1 | Circuit breaker for SoftSeguros | Locked by D-11. Released 2025-09-21. ⚠️ No native asyncio — see Pitfalls. `[VERIFIED: PyPI pip index]` |
| redis (redis-py) | 8.0.1 | Idempotency dedup + SoftSeguros cache | `redis.asyncio.Redis.set(key, value, nx=True, ex=86400)` returns `True` on first SET, `None` on collision. `[VERIFIED: redis-py docs]` |
| pydantic | 2.13.4 | Inbound/outbound message models | CLAUDE.md Convenciones #3. `[VERIFIED: pyproject.toml]` |
| pydantic-settings | 2.14.2 | `WhatsAppSettings`, `SoftSegurosSettings` | Same pattern as 7 existing settings classes. `[VERIFIED: pyproject.toml]` |
| FastAPI | 0.138.1 | Webhook route + test endpoints | `[VERIFIED: pyproject.toml]` |
| structlog | 26.1.0 | Webhook + integration logging | Already wired with `correlation_id` (plan 01-04). `[VERIFIED: pyproject.toml]` |

### Standard library

| Module | Purpose |
|--------|---------|
| `hmac` | `hmac.new(...).hexdigest()` + `hmac.compare_digest()` (D-16) |
| `hashlib` | `hashlib.sha256` for HMAC algorithm |
| `asyncio` | `asyncio.Lock` for SoftSeguros token-refresh race (see Pitfall 5) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff | Verdict |
|------------|-----------|----------|---------|
| pybreaker 1.4.1 (no asyncio) | `aiobreaker` 1.2.0 (native asyncio) | aiobreaker last released 2021-05, Python `>=3.6` only, dormant. `[CITED: pypi.org/project/aiobreaker]` | REJECTED — D-11 locks pybreaker + dormancy is worse than wrapper code. |
| pybreaker 1.4.1 (no asyncio) | `purgatory` 3.0.1 (active, native async) | Different API surface from pybreaker, would require recoupling. `[CITED: pypi.org/project/purgatory]` | REJECTED — D-11 locks pybreaker. Document for F5+ if pybreaker becomes painful. |
| Module-singleton httpx client | Per-request httpx client | Per-request kills perf (no connection reuse, no pool warmup). Module-singleton: ~1 RTT savings per call on warm pool. `[CITED: python-httpx.org/async/]` | Module-singleton — created in lifespan, attached to `app.state.meta_http` and `app.state.softseguros_http`. |
| Verify HMAC before Pydantic | Pydantic-parse first then verify HMAC against re-serialized JSON | Re-serialization changes whitespace/key-order → HMAC fails 100%. `[CITED: svix.com/guides/receiving/receive-webhooks-with-python-fastapi/]` | REJECTED — always raw-body-first. |

**Verified versions on PyPI 2026-06-28:**
```bash
pip index versions tenacity   # 9.1.4 (latest, installed)
pip index versions pybreaker  # 1.4.1 (latest, installed, released 2025-09-21)
pip index versions httpx      # — (httpx==0.28.1 pinned)
pip index versions redis      # — (redis==8.0.1 pinned, asyncio module since 4.2.0)
```

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| tenacity 9.1.4 | PyPI | First release 2013 | ~80M/mo | github.com/jd/tenacity (Julien Danjou, OpenStack contributor) | not run (graceful degrade) | Approved — used in millions of projects. |
| pybreaker 1.4.1 | PyPI | First release 2011 | ~1M/mo | github.com/danielfm/pybreaker (Daniel Fernandes Martins) | not run | Approved — locked by D-11, widely used circuit breaker. |
| httpx 0.28.1 | PyPI | First release 2019 | ~150M/mo | github.com/encode/httpx (Encode org — same as Starlette/Uvicorn) | not run | Approved — already pinned + verified in Phase 1. |
| redis 8.0.1 | PyPI | First release 2009 (originally `redis-py`) | ~300M/mo | github.com/redis/redis-py (Redis Inc) | not run | Approved — already pinned + verified in Phase 1. |

**slopcheck:** Not executed in this research session (`pip install slopcheck` would need verification; environment Windows + bash mixed). All packages above are pre-pinned in `pyproject.toml` and already validated through Phase 1's pre-commit `additional_dependencies` round — no new packages introduced. Risk profile: minimal.

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
              ┌──────────────────────────┐
              │  WhatsApp user / Meta    │
              │  Cloud (graph.facebook)  │
              └──────────────────────────┘
                  │                  ▲
        POST WH   │                  │  POST /messages (echo)
                  ▼                  │
       ┌─────────────────────────────────────────┐
       │  FastAPI: app/webhooks/meta.py          │
       │  • GET  /webhooks/meta  (challenge)     │
       │  • POST /webhooks/meta  (events)        │
       │     1. raw body bytes  ◄── request.body()│
       │     2. HMAC compare_digest              │
       │     3. parse Pydantic InboundEvent      │
       │     4. dispatch each message:           │
       │        a. SET NX wa:msg:{id} → dedup    │
       │        b. allowlist(from_phone)?        │
       │        c. yes → echo                    │
       │        d. no  → log 'ignored', 200      │
       └─────────────────────────────────────────┘
            │                          │
   echo via │                          │ idempotency dedup
   app.state.meta                      │
            ▼                          ▼
   ┌──────────────────┐        ┌──────────────────┐
   │ integrations/    │        │ Redis            │
   │ meta_cloud.py    │        │ (app.state.redis)│
   │  • httpx.Async   │        │ wa:msg:{id} TTL  │
   │    Client        │        │ 24h              │
   │  • POST          │        │ softseguros:{p}: │
   │    /{id}/messages│        │ {q} TTL 60s      │
   └──────────────────┘        └──────────────────┘
            │                          ▲
            ▼                          │ cache miss
   ┌──────────────────────────────────────┐
   │ integrations/softseguros.py          │
   │  • tenacity @retry (outer)           │
   │     • async_call(breaker, fn) (inner)│
   │        • httpx.AsyncClient.post/get  │
   │        • on 401 → asyncio.Lock'd     │
   │          token refresh + retry once  │
   │  • cache read-through on Redis       │
   └──────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────────────────┐
   │ SoftSeguros REST API             │
   │ https://app.softseguros.com/     │
   │  • POST /api-token-auth/         │
   │  • GET  /api/poliza/{id}/        │
   │  • GET  /api/cliente/{id}/       │
   │  • GET  /api/estadopoliza/{id}/  │
   │  • GET  /api/pagopoliza/?poliza_id│
   └──────────────────────────────────┘

   Test endpoint (mounted directly on app/main.py, mirrors /test/llm pattern):
   GET /test/poliza/{poliza_id} → softseguros.get_poliza() → raw JSON
```

Flow notes:
- The webhook handler is single-purpose: verify → dedup → dispatch. All side effects (Meta echo send, structlog line, future graph invocation in F3) happen AFTER `SET NX` succeeds.
- SoftSeguros calls have a Redis-cache hop BEFORE the httpx call (read-through). The 60s TTL means second consumer of same `(poliza_id, query_type)` within window hits Redis, not the upstream.
- Token refresh is gated by `asyncio.Lock` to prevent thundering-herd on 401.

### Recommended Project Structure

```
app/
├── config/
│   └── settings.py                 # ADD: WhatsAppSettings, SoftSegurosSettings
├── integrations/
│   ├── _circuit.py                 # NEW: async_call(breaker, fn, *args) wrapper
│   ├── meta_cloud.py               # NEW: MetaClient + get_meta_client() factory
│   ├── softseguros.py              # NEW: SoftSegurosClient + factory
│   └── openrouter.py               # (unchanged)
├── models/
│   ├── meta.py                     # NEW: InboundEvent, OutboundText, MetaError (Pydantic v2)
│   └── softseguros.py              # NEW: PolizaRaw (loose dict-passthrough OK for F2)
├── features/
│   └── handoff/
│       └── echo.py                 # NEW: is_echo_allowed(phone), build_echo_reply(text)
├── webhooks/
│   ├── __init__.py
│   └── meta.py                     # NEW: GET /webhooks/meta (verify) + POST (events)
├── healthcheck.py                  # (optional: add softseguros + meta probes)
├── main.py                         # ADD: lifespan creates meta + softseguros singletons; include webhooks router; /test/poliza/{id}
└── ...

tests/
├── conftest.py                     # ADD: WA_* + SOFTSEGUROS_* placeholder env vars
├── test_meta_webhook_hmac.py       # NEW: HMAC valid/invalid/missing/wrong-secret
├── test_meta_webhook_dispatch.py   # NEW: allowlist + idempotency + ignored paths
├── test_meta_client.py             # NEW: outbound payload shape, error mapping
├── test_softseguros_client.py      # NEW: cache hit/miss, token refresh, breaker open
├── test_echo_allowlist.py          # NEW: E.164 normalization
└── test_test_poliza_endpoint.py    # NEW: /test/poliza/{id} happy path + 404
```

### Pattern 1: Module-Singleton httpx Client in Lifespan

**What:** One `httpx.AsyncClient` per upstream service, created in lifespan, attached to `app.state.meta_http` and `app.state.softseguros_http`. Released in lifespan's `finally` via `await client.aclose()`.

**When to use:** All long-running HTTP integrations. `[CITED: python-httpx.org/async/]`

**Example:**
```python
# In app/main.py lifespan, after Redis/Postgres:
limits = httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30.0)
timeout = httpx.Timeout(5.0, connect=3.0, read=5.0, write=3.0, pool=2.0)

app.state.meta_http = httpx.AsyncClient(
    base_url=f"https://graph.facebook.com/{settings.wa.api_version}",
    headers={"Authorization": f"Bearer {settings.wa.token.get_secret_value()}"},
    timeout=timeout,
    limits=limits,
)
app.state.softseguros_http = httpx.AsyncClient(
    base_url=settings.softseguros.base_url,
    timeout=httpx.Timeout(10.0, connect=3.0, read=10.0, write=3.0, pool=2.0),
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=30.0),
)
# ... yield ...
await app.state.meta_http.aclose()
await app.state.softseguros_http.aclose()
```

Sources: [HTTPX Async Support](https://www.python-httpx.org/async/), [HTTPX Resource Limits](https://www.python-httpx.org/advanced/resource-limits/), [HTTPX Timeouts](https://www.python-httpx.org/advanced/timeouts/).

### Pattern 2: tenacity (outer) + pybreaker (inner) for SoftSeguros

**What:** tenacity's `@retry` decorator wraps the SoftSeguros method. Inside the method, an `async_call(breaker, fn)` helper enforces the circuit breaker. When the breaker is open, `CircuitBreakerError` is raised — tenacity's `retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))` does NOT match `CircuitBreakerError`, so tenacity stops retrying immediately. This is the correct order.

**When to use:** Any external HTTP service that needs both transient-failure retry AND fail-fast on sustained outage.

**Example:**
```python
# app/integrations/_circuit.py
import asyncio
from typing import Any, Awaitable, Callable, TypeVar
import pybreaker

T = TypeVar("T")

async def async_call(
    breaker: pybreaker.CircuitBreaker,
    coro_fn: Callable[..., Awaitable[T]],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Run an async function under a (sync-API) pybreaker.

    pybreaker 1.4.1 has no native asyncio support — `breaker.call(coro_fn, *args)`
    would call the function and store the *coroutine object* as the return value
    without ever awaiting it, marking the call as success even when the
    upstream fails. This wrapper checks breaker state, awaits the coroutine,
    and manually updates the breaker on success/failure.
    """
    # 1. fail-fast if circuit is open
    if breaker.current_state == "open":
        raise pybreaker.CircuitBreakerError(
            f"Circuit '{breaker.name}' is open"
        )
    try:
        result = await coro_fn(*args, **kwargs)
    except Exception as exc:
        # _on_failure is the public-ish entrypoint used internally by call().
        # We forward the exception so pybreaker's exclude-rules still apply.
        with breaker._lock:
            breaker.state.on_failure(exc)
        raise
    else:
        with breaker._lock:
            breaker.state.on_success()
        return result
```

```python
# app/integrations/softseguros.py
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
import httpx, pybreaker

softseguros_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=30,
    name="softseguros",
)

@retry(
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    reraise=True,
)
async def _http_get(client: httpx.AsyncClient, path: str, **params: Any) -> httpx.Response:
    async def _do() -> httpx.Response:
        r = await client.get(path, params=params)
        r.raise_for_status()
        return r
    # async_call raises CircuitBreakerError if breaker open — NOT in tenacity's
    # retry_if list → tenacity bubbles it instantly.
    return await async_call(softseguros_breaker, _do)
```

Sources: [tenacity async support](https://tenacity.readthedocs.io/), [pybreaker README](https://github.com/danielfm/pybreaker/blob/main/README.rst), [Building a Robust Redis Client w/ Retry + CB](https://dev.to/akarshan/building-a-robust-redis-client-with-retry-logic-in-python-jeg).

### Pattern 3: FastAPI Raw-Body Webhook with HMAC

**What:** Read `await request.body()` before any Pydantic parsing. HMAC the raw bytes. THEN parse JSON. Per D-16 + standard webhook hygiene.

**When to use:** Every signed webhook (Meta, GitHub, Stripe, etc.).

**Example:**
```python
# app/webhooks/meta.py
import hmac, hashlib, structlog
from fastapi import APIRouter, Request, Response, HTTPException
from app.config.settings import settings
from app.models.meta import InboundEnvelope

router = APIRouter(prefix="/webhooks", tags=["meta"])
log = structlog.get_logger("webhook.meta")


@router.get("/meta")
async def verify(hub_mode: str, hub_verify_token: str, hub_challenge: str) -> Response:
    """GET challenge per Meta's webhook subscription flow (D-09).

    Meta expects the raw `hub_challenge` value as plain text body with HTTP 200.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.wa.verify_token.get_secret_value():
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="forbidden")


@router.post("/meta")
async def receive(request: Request) -> Response:
    # 1. raw body — MUST come before any .json() / Pydantic parsing.
    raw = await request.body()

    # 2. HMAC verify
    header = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(
        settings.wa.webhook_secret.get_secret_value().encode(),
        raw,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, header):
        log.warning("webhook.hmac.invalid", header_present=bool(header))
        raise HTTPException(status_code=401, detail="invalid signature")

    # 3. Parse Pydantic model (now safe — HMAC already verified)
    envelope = InboundEnvelope.model_validate_json(raw)

    # 4. Dispatch — see Pattern 4 for ordering
    await dispatch(request.app, envelope)
    return Response(status_code=200)
```

Sources: [Svix FastAPI webhook guide](https://www.svix.com/guides/receiving/receive-webhooks-with-python-fastapi/), [How to Implement SHA256 Webhook Signature Verification (Hookdeck)](https://hookdeck.com/webhooks/guides/how-to-implement-sha256-webhook-signature-verification), [FastAPI issue #4321 X-Signature](https://github.com/fastapi/fastapi/issues/4321).

### Pattern 4: Idempotency Check Ordering

**Order (D-15 enforced):** HMAC → parse → dedup → allowlist → side effect.

```python
async def dispatch(app: FastAPI, envelope: InboundEnvelope) -> None:
    redis = app.state.redis
    meta = app.state.meta
    for entry in envelope.entry:
        for change in entry.changes:
            for msg in change.value.messages or []:
                # a) DEDUP — must precede side effects (D-15)
                key = f"wa:msg:{msg.id}"
                first_see = await redis.set(key, 1, nx=True, ex=86400)
                if first_see is None:
                    log.info("webhook.dedup.skip", message_id=msg.id, from_phone=msg.from_)
                    continue
                # b) ALLOWLIST
                if not is_echo_allowed(msg.from_):
                    log.info("webhook.ignored.not_allowlisted",
                             message_id=msg.id, message_type=msg.type)
                    continue
                # c) ECHO (side effect)
                reply = build_echo_reply(msg)
                try:
                    await meta.send_text(to=msg.from_, body=reply)
                    log.info("webhook.echo.sent", message_id=msg.id, reply_len=len(reply))
                except Exception as exc:
                    log.exception("webhook.echo.error",
                                  message_id=msg.id, error_type=type(exc).__name__)
                    # NB: dedup key was already set — re-delivery from Meta
                    # will be skipped. F4 may reverse this for transient failures.
```

**Why this order:**
1. **HMAC first** — every other check is meaningless if the payload is spoofed.
2. **Parse second** — narrows surface for further logic; raises early on malformed JSON (which `model_validate_json` returns as HTTP 422 by default; we may want to return 200 + log warning to avoid Meta retry storms — see Pitfall 7).
3. **Dedup third** — must precede any side effect. If Meta retries within 24h, we MUST NOT echo twice. (D-14, D-15.)
4. **Allowlist fourth** — cheaper than the echo, gates the side effect.
5. **Side effect last** — only run for first-seen + allowlisted messages.

### Pattern 5: SoftSeguros Token Refresh with asyncio.Lock

**What:** Cache token in module-level mutable holder. On 401, acquire `asyncio.Lock`, re-check token (it may have been refreshed by a parallel task while waiting), refresh, release. Retry the original call once.

**Why:** Multiple concurrent SoftSeguros calls all see the 401 at the same time. Without a lock, all of them stampede the `/api-token-auth/` endpoint, getting multiple new tokens and possibly hitting upstream rate limits.

**Example:**
```python
# app/integrations/softseguros.py
_token_lock = asyncio.Lock()
_token_holder: dict[str, str | None] = {"v": None}

async def _get_token(client: httpx.AsyncClient) -> str:
    if _token_holder["v"]:
        return _token_holder["v"]
    async with _token_lock:
        # Re-check inside the lock — another task may have refreshed.
        if _token_holder["v"]:
            return _token_holder["v"]
        r = await client.post(
            "/api-token-auth/",
            json={
                "username": settings.softseguros.username,
                "password": settings.softseguros.password.get_secret_value(),
            },
        )
        r.raise_for_status()
        token = r.json()["token"]
        _token_holder["v"] = token
        return token

async def _refresh_token_on_401(client: httpx.AsyncClient) -> str:
    async with _token_lock:
        # The just-failed call held an old token; we always refresh.
        _token_holder["v"] = None
    return await _get_token(client)
```

Source: [DRF TokenAuthentication](https://www.django-rest-framework.org/api-guide/authentication/) — auth flow shape; lock pattern is standard asyncio idiom.

### Anti-Patterns to Avoid

- **HMAC over Pydantic-parsed body** — re-serialization changes bytes → 100% HMAC failure.
- **`hashlib.sha256(...).hexdigest() == header`** — timing-attack vulnerable. ALWAYS `hmac.compare_digest`.
- **Per-request `httpx.AsyncClient(...)`** — kills connection pool warmth, ~1 extra RTT per call.
- **`@breaker` decorator on async function (pybreaker 1.4.1)** — silently mis-records success.
- **Skip allowlist check before echo** — D-02 violation; LANDA could echo into customer chats.
- **Side effect before dedup** — Meta retries 5xx for ~24h; double-confirmations are possible.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry with exponential backoff | Custom `for attempt in range(3): await asyncio.sleep(2**attempt)` | `tenacity.retry` | Jitter, retry-condition predicates, async-aware sleep, telemetry hooks. |
| Circuit breaker | Custom `failure_count > N → raise` | `pybreaker.CircuitBreaker` (with `async_call` wrapper) | Half-open state, listeners, thread-safety, exclude rules. |
| HMAC computation + compare | Custom hash + `==` compare | `hmac.new(...).hexdigest()` + `hmac.compare_digest()` | Timing-safe compare is the whole point. |
| Idempotency key store | Custom in-memory dict | Redis `SET NX EX` | Atomic check-and-set, cross-worker dedup, automatic TTL eviction. |
| Connection pooling | Custom session/connection reuse | `httpx.AsyncClient` w/ `Limits` | Connection lifecycle, keepalive, HTTP/2 multiplexing. |
| Pydantic-parsed inbound message | Custom dict-walking | `InboundEnvelope` Pydantic model w/ `model_validate_json` | Type-safety, validation errors, mypy support. |
| Token refresh race | Custom `if token: ... else: refresh()` | `asyncio.Lock` + double-check inside lock | Race-condition-free, idiomatic asyncio. |

**Key insight:** F2 is largely **plumbing libraries together**, not writing logic. The single piece of original code is the `async_call(breaker, fn)` wrapper (≈20 lines) that compensates for pybreaker's missing asyncio integration. Every other concern has a battle-tested library.

## Common Pitfalls

### Pitfall 1: Pydantic-parse before HMAC — silent 100% failure

**What goes wrong:** FastAPI routes that declare a Pydantic body (e.g. `async def receive(event: InboundEnvelope)`) automatically parse the JSON. By the time the handler runs, `request.body()` may still work but in some middleware stacks the bytes are gone or re-serialized.

**Why:** HMAC is computed over the EXACT bytes Meta sent. Even a re-serialized JSON with reordered keys (Python dict ordering vs JS JSON.stringify) produces different bytes → different HMAC → mismatch.

**How to avoid:** Declare the route as `async def receive(request: Request)` (NO Pydantic body param), call `raw = await request.body()` FIRST, HMAC the raw bytes, THEN `InboundEnvelope.model_validate_json(raw)`.

**Warning signs:** `webhook.hmac.invalid` log spam in dev with no clear cause; works in curl but fails from Meta.

### Pitfall 2: pybreaker silently registers async-call success

**What goes wrong:** `breaker.call(async_fn, ...)` calls `async_fn` synchronously, which returns a coroutine object. pybreaker doesn't await it, doesn't see the exception, records "success", returns the coroutine to the caller. The caller awaits the coroutine, sees the failure, but pybreaker's counters are already wrong.

**Why:** pybreaker 1.4.1 has no asyncio support (`HAS_TORNADO_SUPPORT` flag is the only async branch; `call_async` requires Tornado's `@gen.coroutine`). Confirmed by reading [source](https://github.com/danielfm/pybreaker/blob/main/src/pybreaker/__init__.py).

**How to avoid:** Use the `async_call(breaker, coro_fn, *args)` wrapper in Pattern 2. Never use `@breaker` as a decorator on async functions.

**Warning signs:** Circuit breaker never opens despite repeated SoftSeguros 503s; or worse, opens at random because of internal state corruption.

### Pitfall 3: `httpx.AsyncClient` per-request kills pool

**What goes wrong:** Calling `async with httpx.AsyncClient() as c:` inside the handler creates a new connection pool for every request, no keepalive reuse, no HTTP/2 multiplexing. Latency jumps by one full RTT per call.

**Why:** Connection pooling only pays off if the client outlives the request. `[CITED: python-httpx.org/async/]`

**How to avoid:** Create `app.state.meta_http` and `app.state.softseguros_http` in lifespan. Close them in lifespan's `finally`. Inside handlers/integrations, read from `app.state`.

**Warning signs:** p50 latency above ~150ms for cached SoftSeguros calls; observable connection churn in upstream logs.

### Pitfall 4: Bare `==` for HMAC comparison

**What goes wrong:** `if expected == header: ...` is vulnerable to timing attacks — Python's str equality short-circuits on the first mismatch, leaking the prefix-match length via response time.

**Why:** HMAC is a security primitive; constant-time compare is non-negotiable. D-16 explicitly bans `==`.

**How to avoid:** Always `hmac.compare_digest(expected, header)`.

**Warning signs:** ruff/bandit lint warning S324 / B105 (depending on tooling). Code-review catch.

### Pitfall 5: SoftSeguros token-refresh thundering herd

**What goes wrong:** 50 concurrent SoftSeguros calls all see 401 at once. Without coordination, they all POST `/api-token-auth/` in parallel, generating 50 tokens, and possibly burning DPG's auth-endpoint rate quota.

**Why:** Single-process asyncio doesn't auto-serialize external HTTP calls — they all suspend on the network at the same moment.

**How to avoid:** Pattern 5 — `asyncio.Lock` around refresh with double-check inside the lock.

**Warning signs:** Spike in `/api-token-auth/` calls in SoftSeguros logs; transient 429s from SoftSeguros during deploys.

### Pitfall 6: pybreaker state is per-process, not cross-worker

**What goes wrong:** When uvicorn runs >1 worker (or Railway scales horizontally), each process has its OWN `CircuitBreaker` instance with independent counters. A breaker opens in worker 1, but workers 2-4 keep hammering SoftSeguros.

**Why:** pybreaker stores state in process memory (`_state_storage` default = `CircuitMemoryStorage`).

**How to avoid (F2):** Acknowledge as known limitation. v1 traffic is ~50 msg/day → 1 worker is sufficient.

**How to avoid (F5+):** pybreaker supports `CircuitRedisStorage` for shared state. Plan a migration card in F5 if horizontal scaling becomes necessary.

**Warning signs:** Inconsistent breaker behavior under load; observable when worker count >1 and SoftSeguros is genuinely down.

### Pitfall 7: Meta re-sends on 5xx for ~24h → log levels matter

**What goes wrong:** If the handler returns 500 (uncaught exception), Meta retries. If retries succeed but `raw body` differs (unlikely but possible), HMAC fails on retry, we 401, Meta retries again, loop.

**Why:** Meta's webhook delivery is at-least-once with exponential backoff.

**How to avoid:**
- Pydantic `ValidationError` from `model_validate_json` → catch, log `webhook.malformed`, return 200 (not 422). A malformed body from Meta is exceptional and won't fix itself on retry.
- Side-effect errors (echo send fails) → still return 200 (dedup key already set; F4 may re-architect with task queue if reliability matters).
- HMAC failure → 401 (correct: do NOT mask this).

**Warning signs:** Repeated identical webhook deliveries for the same `message_id` in logs.

### Pitfall 8: E.164 normalization mismatch (Meta vs env var)

**What goes wrong:** Meta sends `from: "16505551234"` (no leading `+`). Operator stores allowlist as `WA_ECHO_ALLOWLIST=+16505551234,+5491134567890`. Direct `phone in allowlist` returns `False`.

**Why:** Meta omits `+` from E.164 in webhook payload; humans write with `+`.

**How to avoid:** Normalize both sides to "always with `+`" before comparison:
```python
def _normalize_e164(raw: str) -> str:
    raw = raw.strip()
    return raw if raw.startswith("+") else "+" + raw

def is_echo_allowed(phone: str) -> bool:
    normalized = _normalize_e164(phone)
    allowed = {_normalize_e164(p) for p in settings.wa.echo_allowlist}
    return normalized in allowed
```

**Warning signs:** Echo never fires for any number; `webhook.ignored.not_allowlisted` log line for every test.

### Pitfall 9: `LANGSMITH_WORKSPACE_ID` env var (deferred from Phase 1)

**What goes wrong:** Phase 1 noted this as a follow-up. F2 itself doesn't need it, but the operator setup adds new env vars (`WA_*`, `SOFTSEGUROS_*`) — a natural moment to also wire `LANGSMITH_WORKSPACE_ID` into `LangSmithSettings` and Railway.

**How to avoid:** Add a small task in F2 plan: "extend `LangSmithSettings` with optional `workspace_id: str | None = None`, set Railway env var, no breaking change."

**Warning signs:** Phase 1 follow-up note never gets addressed.

### Pitfall 10: `request.body()` consumed twice

**What goes wrong:** Calling `await request.body()` returns bytes, but if you then declare a Pydantic body param on the same route, FastAPI tries to read the body again and gets empty bytes → 422.

**Why:** ASGI bodies are streamed; reading consumes the stream. FastAPI caches once read, but the cache is per-request and order-dependent.

**How to avoid:** Use Pattern 3 — handler takes `Request` only, no Pydantic body parameter. Parse manually via `model_validate_json(raw)`.

**Warning signs:** Random 422 on a route that worked in unit tests.

## Code Examples

### Meta inbound webhook payload — verified shape

`[CITED: developers.facebook.com/docs/whatsapp/cloud-api/webhooks via search result]`

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "1451322196454283",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "16415416615",
              "phone_number_id": "1267241483129092"
            },
            "contacts": [
              {
                "profile": {"name": "Sheena Nelson"},
                "wa_id": "16505551234"
              }
            ],
            "messages": [
              {
                "from": "16505551234",
                "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA=",
                "timestamp": "1749416383",
                "type": "text",
                "text": {"body": "Does it come in another color?"}
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

Status update event:

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "1451322196454283",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "16415416615", "phone_number_id": "1267241483129092"},
        "statuses": [{
          "id": "wamid.HBgL...",
          "status": "delivered",
          "timestamp": "1749416400",
          "recipient_id": "16505551234"
        }]
      },
      "field": "messages"
    }]
  }]
}
```

### Outbound text message — POST /v21.0/{PHONE_NUMBER_ID}/messages

`[CITED: developers.facebook.com/docs/whatsapp/cloud-api/reference/messages]`

```bash
curl -X POST https://graph.facebook.com/v21.0/1267241483129092/messages \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "messaging_product": "whatsapp",
    "recipient_type": "individual",
    "to": "16505551234",
    "type": "text",
    "text": {"body": "echo: Does it come in another color?"}
  }'
```

Success response:
```json
{
  "messaging_product": "whatsapp",
  "contacts": [{"input": "16505551234", "wa_id": "16505551234"}],
  "messages": [{"id": "wamid.HBg...", "message_status": "accepted"}]
}
```

Common error response shape:
```json
{
  "error": {
    "message": "Invalid parameter",
    "type": "OAuthException",
    "code": 100,
    "error_subcode": 2494010,
    "fbtrace_id": "..."
  }
}
```

Error code mapping for F2:

| HTTP | Meta code | Meaning | Plan response |
|------|-----------|---------|---------------|
| 400  | 100, 131000 | Invalid recipient / param | Log + alert; no retry (4xx fatal) |
| 401  | 190 | Invalid/expired token | Log + alert; ops must rotate token. No auto-retry. |
| 429  | 130429 | Rate limit | Log + tenacity backoff; honor `Retry-After` header if present |
| 5xx  | any | Meta server error | tenacity retries 3x with exp backoff |

### GET challenge — verification flow

`[CITED: medium.com/@zainzulfiqarmaknojia/how-to-configure-and-validate-whatsapp-webhooks...]`

```python
@router.get("/meta")
async def verify(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
) -> Response:
    if hub_mode == "subscribe" and hub_verify_token == settings.wa.verify_token.get_secret_value():
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="forbidden")
```

Note Meta sends query params as `hub.mode`, `hub.verify_token`, `hub.challenge` — Python identifiers can't contain `.`, so use `alias=` to bind.

### HMAC verification — Python reference

`[CITED: hookdeck.com/webhooks/guides/how-to-implement-sha256-webhook-signature-verification]`

```python
import hmac, hashlib

def verify_meta_signature(raw_body: bytes, header_value: str, app_secret: str) -> bool:
    """Constant-time HMAC SHA-256 verification for Meta X-Hub-Signature-256."""
    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, header_value)
```

### Redis SET NX EX for idempotency

`[CITED: redis.io/blog/what-is-idempotency-in-redis/]`

```python
async def claim_message_id(redis: Redis, message_id: str) -> bool:
    """Return True if this is the first time we've seen this message_id; False if duplicate."""
    result = await redis.set(f"wa:msg:{message_id}", "1", nx=True, ex=86400)
    return result is True  # redis-py returns True on success, None on collision
```

### SoftSeguros token request — DRF shape

`[CITED: django-rest-framework.org/api-guide/authentication/]`

```bash
POST https://app.softseguros.com/api-token-auth/
Content-Type: application/json

{"username": "dpg_user", "password": "secret"}
```

Response:
```json
{"token": "9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b"}
```

Subsequent calls:
```
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

Note: `Token <space> <value>` — NOT `Bearer`. This is DRF's `TokenAuthentication`, not OAuth.

### tenacity for async httpx

`[CITED: tenacity.readthedocs.io/]`

```python
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
import httpx

@retry(
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    reraise=True,
)
async def call_softseguros(client: httpx.AsyncClient, path: str) -> dict:
    r = await client.get(path)
    r.raise_for_status()
    return r.json()
```

`@retry` auto-detects coroutine functions (since tenacity 8.x; verified for 9.1.4) and switches to `AsyncRetrying` internally — no special syntax needed.

## State of the Art

| Old approach (CONTEXT-era / ROADMAP draft) | Current approach (F2 plan) | When changed | Impact |
|--------------|-----------------|--------------|--------|
| Meta Graph API `v18.0` (ROADMAP wording) | `v21.0` | CONTEXT D-08 | v18 enters deprecation Q1 2026. v25 is latest but v21 is the stable target. |
| Twilio as BSP | Meta Cloud API directo | PROJECT decision pre-F1 | Removes vendor layer; Meta BM restriction lifted. |
| `requirements.txt` | `pyproject.toml + uv.lock` | F1 plan 01-01 | F2 just extends pyproject. |
| pybreaker `@breaker` decorator | `async_call(breaker, fn)` wrapper | F2 (this research) | pybreaker 1.4.1 has no asyncio; the decorator silently mis-records. |
| Per-request `httpx.AsyncClient` | `app.state.X_http` singleton from lifespan | F2 (this research) | Matches Phase 1's `app.state.redis` / `app.state.session_factory` pattern. |

**Deprecated/outdated:**
- Graph API v18.0 — entering deprecation Q1 2026 per Meta versioning policy.
- `pybreaker` Tornado integration — works but unrelated to asyncio; misleading documentation suggests it is async.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | SoftSeguros endpoints `/api/poliza/{id}/`, `/api/cliente/{id}/`, `/api/estadopoliza/{poliza_id}/`, `/api/pagopoliza/?poliza_id=` follow Django REST framework defaults (single object on detail, list on collection, JSON over the wire). | SoftSeguros section | If schema is XML or uses non-DRF auth, the client wrapper will need refactoring. Operator (Maxi) can confirm by sharing one real response or `lambda-proyect/cobranza/` HTTP code. |
| A2 | SoftSeguros token has no documented expiration; refresh-on-401 is the only signal. | D-12, Pattern 5 | If token has a fixed TTL we should proactively refresh, not lazy-on-401. Low cost to add. |
| A3 | Meta v21.0 webhook payload shape matches the v18-v25 examples I cited (no breaking changes to `messages`/`statuses` arrays). | Code Examples section | Cited Meta changelog for v21 says new auth template params + local storage at registration — nothing about webhook payload changes. |
| A4 | pybreaker 1.4.1's `breaker.state.on_success()` and `breaker.state.on_failure(exc)` are stable enough to call from external code via `async_call`. | Pattern 2 | These are pseudo-public (no leading `_` on the methods themselves; the source uses them as the in-state callbacks). If they break in a future minor, plan a migration to `purgatory` (3.0.1, native async). |
| A5 | Module-singleton httpx client with `keepalive_expiry=30s` and `max_keepalive_connections=20` is sized correctly for ~50 msg/day. | Pattern 1 | Conservatively over-provisioned; only risk is wasted RAM (~1KB per connection). |
| A6 | Meta retries 5xx for ~24h before giving up. | Pitfall 7 | Documented as "extended retry" by Meta — exact ceiling varies. 24h is the right TTL for idempotency keys regardless. |
| A7 | E.164 normalization rule: env var stores `+1...`, Meta sends `1...` (no `+`). | Pitfall 8 | Confirmed from sample payload (`from: "16505551234"`). Operator can test with one real send to verify. |
| A8 | DRF token format `Authorization: Token <hex>` matches SoftSeguros (it's Django, but not verified directly). | SoftSeguros section | Strongly inferred from `/api-token-auth/` endpoint URL (DRF convention). Could be JWT or `Bearer` — operator can verify in lambda-proyect/cobranza. |
| A9 | `httpx.AsyncClient.aclose()` is the correct shutdown call (vs `close()` or context manager). | Pattern 1 | Documented; aclose() is the only async-safe close. |
| A10 | tenacity 9.x's auto async-detection works for our wrapped function (since the function awaits the pybreaker `async_call`, it's a coroutine). | Pattern 2 | Documented as auto-detection via `is_coroutine_callable()`. Tested in many production codebases. |

**Risk profile:** A1 + A8 are the highest-risk because they're about SoftSeguros (no public docs). Mitigation: F2 plan adds a single early task to call `POST /api-token-auth/` with one DPG cred set and capture/store the response shape, treating it as the contract reference. Everything else has docs.

## Open Questions

1. **SoftSeguros JSON response shape for `/api/poliza/{id}/`** — what fields, what nullability, what nested shapes? Operator's next step.
   - What we know: it's DRF (Token auth at `/api-token-auth/`); DPG already uses it from lambda-proyect.
   - What's unclear: exact response keys. Saldo could be `saldo`, `monto_pendiente`, `saldo_pendiente`, etc.
   - Recommendation: F2 plan includes Task 0 "spike: call /api/poliza/{known_id}/ once with curl, paste sanitized JSON in plan-summary." Build `PolizaRaw` Pydantic model as `Dict[str, Any]` passthrough for F2 (don't try to model fields we haven't seen). F3 can add field-by-field models when QA tools are built.

2. **Operator's actual echo allowlist** — D-02 leaves the list pending.
   - What we know: there will be a small list of internal test numbers.
   - What's unclear: how many, who. Doesn't block research, just settings wire-up.
   - Recommendation: F2 plan accepts the list at wire-up time, includes `WA_ECHO_ALLOWLIST` empty-default fail-closed (no number echoed → safer than fail-open).

3. **SOFTSEGUROS_USERNAME / PASSWORD** — D-01 pending operator.
   - Doesn't block plan creation; blocks wire-up. Same as #2.

4. **Should breaker open trigger Sentry alert?** — D-11 says "tras N fallos consecutivos, el bot escala a humano" but in F2 there's no Chatwoot wire-up yet.
   - What we know: F2 has no human escalation channel; that lands in F3.
   - Recommendation: F2 plan emits `structlog.error("softseguros.breaker.open", reset_in=30)` which Sentry captures via the structlog→Sentry bridge wired in plan 01-02. No new code path needed.

5. **Worker process count on Railway** — Pitfall 6 is moot if we stay at 1 worker.
   - What we know: Hobby plan, low v1 traffic.
   - Recommendation: F2 plan documents `--workers 1` as the assumed deploy command. F5 revisits.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Redis 8 (`redis.asyncio`) | Idempotency + cache | ✓ | `redis==8.0.1` pinned + lifespan wires `app.state.redis` (Phase 1) | — |
| Postgres (asyncpg) | Not directly needed in F2 | ✓ | `asyncpg==0.31.0` (Phase 1) | — |
| httpx 0.28.1 | Meta + SoftSeguros HTTP | ✓ | pyproject.toml pin | — |
| tenacity 9.1.4 | Retry on SoftSeguros | ✓ | pyproject.toml pin (verified PyPI latest) | — |
| pybreaker 1.4.1 | Circuit breaker | ✓ | pyproject.toml pin (verified PyPI latest) | — |
| `hmac`, `hashlib` | HMAC verify | ✓ | Python stdlib | — |
| Public webhook URL | Meta to deliver events | ✓ | `landa-agent-service-production.up.railway.app` (D-04) | Custom domain deferred. |
| WhatsApp test number | Echo round-trip smoke test | ✗ | TBD by operator (D-02) | Cannot run smoke; unit tests substitute. |
| SoftSeguros credentials | `/test/poliza/{id}` smoke | ✗ | TBD by operator (D-01) | Unit tests with mocked httpx substitute. |

**Missing dependencies with no fallback:**
- Real SoftSeguros credentials — blocks `/test/poliza/{id}` smoke verification (but not the code itself).
- Real test number on the allowlist — blocks echo round-trip smoke (but not the code itself).

**Missing dependencies with fallback:**
- HMAC test fixtures — synthetic body + signature triple (operator can replace with real captured Meta payload via ngrok if desired).

## Validation Architecture

> Per `.planning/config.json`: `nyquist_validation: false` → **section skipped per agent contract.**

## Security Domain

`.planning/config.json` has `security_enforcement: true`, `security_asvs_level: 1`. Phase 2's surface is webhook ingress + outbound HTTP + Redis dedup. ASVS Level 1 categories that apply:

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V1 Architecture | yes | Module isolation: `webhooks/` ≠ `integrations/` ≠ `features/`. Vertical-slice. |
| V2 Authentication | yes | SoftSeguros: DRF Token auth (D-12); Meta: System User Token (D-06). Both stored as `SecretStr` (D-01 pattern). |
| V3 Session Management | no | No user sessions in F2. |
| V4 Access Control | yes | Echo allowlist (D-02) = function-level access control. `is_echo_allowed(phone)` gate. |
| V5 Input Validation | yes | Pydantic v2 on `InboundEnvelope`; only AFTER HMAC verify. |
| V6 Cryptography | yes | HMAC SHA-256 via `hmac.new + compare_digest` (D-16). No hand-rolled crypto. |
| V7 Error Handling | yes | `# noqa: BLE001` + `type(exc).__name__` in responses (Phase 1 pattern T-01-15). No conn-string leaks. |
| V8 Data Protection | yes | `SecretStr` for all 4 new secrets (`WA_TOKEN`, `WA_WEBHOOK_SECRET`, `WA_VERIFY_TOKEN`, `SOFTSEGUROS_PASSWORD`). |
| V9 Communication | yes | HTTPS-only (httpx default). Verified TLS cert via httpx's default `verify=True`. |
| V10 Malicious Code | n/a | No code execution from inputs. |
| V11 Business Logic | yes | Idempotency (D-14) prevents replay-driven double-confirm. |
| V12 Files & Resources | no | No file upload in F2 (deferred to F4 attachments). |
| V13 API & Web Service | yes | Webhook endpoint hardened: HMAC, rate-limit (deferred F5), allowlist. |
| V14 Configuration | yes | Pydantic Settings with `env_prefix` + `extra="ignore"`. Defaults are dev-safe. |

### Known Threat Patterns for FastAPI + WhatsApp + Redis

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| HMAC bypass via spoofed signature | Tampering, Spoofing | `hmac.compare_digest`, 401 on mismatch (D-16) |
| Webhook replay | Tampering | Idempotency key in Redis with TTL 24h (D-14) |
| Echo amplification (LANDA echoing to attacker-controlled number) | Spoofing, Information disclosure | Echo allowlist (D-02), fail-closed |
| Token leak via log | Information disclosure | `SecretStr` + structlog scrubber (Phase 1) |
| Connection-string leak in `/health` error response | Information disclosure | Exception type-name only (T-01-15 — Phase 1 pattern reused) |
| SoftSeguros credential leak via 500 response | Information disclosure | httpx response body NOT included in raised exception; tenacity preserves only exception type |
| Timing attack on HMAC | Information disclosure | `hmac.compare_digest` (constant-time) |
| Stale data from SoftSeguros under outage | Tampering (of business logic via stale fact) | Circuit breaker open → escalate to human (F3+); F2 surfaces `degraded` |
| Token-refresh storm | Denial of service (against SoftSeguros) | `asyncio.Lock` + double-check (Pattern 5) |

## Sources

### Primary (HIGH confidence)

- [HTTPX Async Support](https://www.python-httpx.org/async/) — module-singleton client pattern, aclose()
- [HTTPX Timeouts](https://www.python-httpx.org/advanced/timeouts/) — connect/read/write/pool defaults
- [HTTPX Resource Limits](https://www.python-httpx.org/advanced/resource-limits/) — Limits(max_keepalive_connections, max_connections, keepalive_expiry)
- [Tenacity ReadTheDocs](https://tenacity.readthedocs.io/) — auto async detection, retry_if_exception_type, stop_after_attempt, wait_exponential
- [Tenacity async-support page (DeepWiki summary)](https://deepwiki.com/jd/tenacity/4-asynchronous-support) — `is_coroutine_callable` + AsyncRetrying
- [pybreaker README](https://github.com/danielfm/pybreaker/blob/main/README.rst) — CircuitBreaker constructor, states, listeners
- [pybreaker source __init__.py](https://raw.githubusercontent.com/danielfm/pybreaker/main/src/pybreaker/__init__.py) — confirmed NO asyncio support
- [Django REST framework Authentication](https://www.django-rest-framework.org/api-guide/authentication/) — `obtain_auth_token`, `Authorization: Token <hex>`
- [Meta Graph API v21.0 changelog](https://developers.facebook.com/docs/graph-api/changelog/version21.0) — release Oct 2024
- [Meta Graph API versions table](https://developers.facebook.com/docs/graph-api/changelog/versions/) — v21 still active in 2026; v18 deprecating
- [Meta WhatsApp Cloud API reference: messages](https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages) — POST /{phone_id}/messages body shape
- [Redis idempotency tutorial](https://redis.io/tutorials/data-deduplication-with-redis/) — SET NX EX pattern
- [Redis idempotency blog (Redis Inc)](https://redis.io/blog/what-is-idempotency-in-redis/) — True/None return semantics
- [redis-py asyncio examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html) — ConnectionPool.from_url shared client

### Secondary (MEDIUM confidence)

- [Svix FastAPI webhook guide](https://www.svix.com/guides/receiving/receive-webhooks-with-python-fastapi/) — raw body before Pydantic
- [Hookdeck SHA256 webhook signature verification](https://hookdeck.com/webhooks/guides/how-to-implement-sha256-webhook-signature-verification) — timing-safe compare, prefix `sha256=`
- [Hookdeck WhatsApp webhooks guide](https://hookdeck.com/webhooks/platforms/guide-to-whatsapp-webhooks-features-and-best-practices) — escaped Unicode in signed body
- [Meta releases Graph API v21.0 (ppc.land)](https://ppc.land/meta-releases-graph-api-v21-0-and-marketing-api-v21-0/) — release date + WhatsApp v21 changes
- [WhatsApp Cloud API setup blog (Akash Tyagi 2026)](https://medium.com/@aktyagihp/whatsapp-cloud-api-integration-in-2026-0493dd05d644) — current-state confirmation
- [WhatsApp throughput limits (Helo.ai 2026 guide)](https://helo.ai/resources/blog/whatsapp-api-rate-limits) — 80 mps default, 1000 mps after tier upgrade
- [How to Configure WhatsApp Webhooks (Zain Zulfiqar Medium)](https://medium.com/@zainzulfiqarmaknojia/how-to-configure-and-validate-whatsapp-webhooks-for-real-time-notifications-using-power-automate-e1f5ecd7ab99) — challenge GET response shape
- [Implementing Webhooks WhatsApp blog](https://whatsappbusiness.com/blog/how-to-use-webhooks-from-whatsapp-business-api/) — webhook payload structure
- [WhatsApp Service messages (24h window)](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages) — freeform within 24h CSW
- [Building a Robust Redis Client (akarshan dev.to)](https://dev.to/akarshan/building-a-robust-redis-client-with-retry-logic-in-python-jeg) — circuit breaker outer / tenacity inner discussion

### Tertiary (LOW confidence — flagged for operator validation)

- SoftSeguros API contract — no public docs. Inferred from `/api-token-auth/` URL = DRF convention. Operator can validate against lambda-proyect.
- pybreaker `state.on_success()` / `state.on_failure(exc)` as quasi-public hooks — read from source, not in README. Stable across 1.x but not contractually guaranteed.

## Metadata

**Confidence breakdown:**

- Standard stack (versions, packages): **HIGH** — all pinned in pyproject.toml, all verified via `pip index versions` + PyPI pages today.
- Meta Cloud API webhook + outbound shape: **HIGH** — multiple official-and-derivative sources agree on payload schema; cited Meta changelog confirms v21 is current.
- HMAC + idempotency pattern: **HIGH** — standard webhook hygiene, cross-validated by 4+ sources.
- httpx + tenacity integration: **HIGH** — tenacity auto-detection of coroutines is documented behavior since v8.
- pybreaker asyncio interaction: **MEDIUM-HIGH** — confirmed by reading source that there is NO asyncio support; the `async_call` wrapper pattern is derived from how `breaker.call()` works internally. Stable but uses semi-public methods.
- SoftSeguros API contract: **LOW** — no public docs, inferred from URL conventions only. Operator must validate.
- Allowlist + echo design: **HIGH** — pure function over settings, well-specified by D-02.
- Pitfalls: **HIGH** — every pitfall has documented evidence (source, prior incident, or library source code).

**Research date:** 2026-06-28
**Valid until:** 2026-07-28 (30 days — stack is stable, no fast-moving frontier components)
**Validity assumption:** Meta does not deprecate v21.0 within 30 days; pybreaker does not release breaking 1.5.0 within 30 days.
