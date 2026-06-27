# Phase 1: Setup infra — Research

**Researched:** 2026-06-27
**Domain:** Infrastructure scaffolding (FastAPI + LangGraph + OpenRouter + Railway + Chatwoot)
**Confidence:** HIGH on core stack and versions, MEDIUM on Chatwoot-on-Railway specifics and LangSmith free-tier PII patterns

## Summary

This phase scaffolds the entire microservice infrastructure for `landa-agent-service` on Railway. The deliverables span eight conceptual blocks: (1) FastAPI repo skeleton with vertical-slice layout, (2) Railway-provisioned Postgres + Redis, (3) LangGraph with Postgres checkpointer, (4) Chatwoot self-hosted as a sibling service group, (5) OpenRouter LLM factory, (6) LangSmith tracing, (7) Sentry + structlog with PII redaction, and (8) CI plus a health endpoint.

The stack is locked in CLAUDE.md (no re-litigation). The research focuses on **how** to wire it together cleanly in mid-2026, with version pins verified against PyPI today, and on the few decisions still open to Claude's discretion: build tool on Railway (Railpack vs Dockerfile), dependency management format (pyproject.toml vs requirements.txt), and the specific structlog/Sentry integration shape.

**Primary recommendation:** Use **pyproject.toml + uv.lock** (single source of truth for deps, fast and reproducible), **a custom Dockerfile per Railway service** (predictable builds and image size — Nixpacks is now in maintenance), **FastAPI lifespan() context manager** to own the AsyncPostgresSaver + asyncpg pool + Redis pool lifecycle, **structlog routed through stdlib's `ProcessorFormatter`** with a `bind_contextvars`-based correlation ID flowing through to Sentry, and **Chatwoot deployed via Railway's existing community template** (5 services: `chatwoot-rails`, `chatwoot-sidekiq`, `chatwoot-postgres`, `chatwoot-redis`, plus an `agent-service` group) — minimum 4GB RAM per Rails web service.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| FastAPI app (HTTP + webhooks) | API / Backend | — | Webhook receivers, health endpoint, internal REST endpoints |
| LangGraph state machine | API / Backend | Database (checkpointer) | Orchestration logic colocated with API; state persisted in Postgres |
| OpenRouter LLM gateway | API / Backend | External (OpenRouter) | All LLM calls flow through `get_llm(role)` factory; no client-side LLM |
| Chatwoot Rails + Sidekiq | API / Backend (sibling) | Database (own Postgres+Redis) | Separate Railway service group, communicates with agent over public HTTPS + webhook |
| Application Postgres | Database / Storage | — | LangGraph checkpoints + future audit_log + cases |
| Application Redis | Database / Storage | — | SoftSeguros cache (TTL 60s) + arq queue + rate limit tokens + idempotency keys |
| Static assets / docs | None this phase | — | No frontend in v1; Chatwoot ships its own admin UI |
| Observability (LangSmith, Sentry) | External | — | SaaS — tracing exits the infra; PII redacted before egress |
| TLS termination | CDN / Edge (Railway) | — | Railway auto-issues Let's Encrypt for `*.up.railway.app` and custom domains |

## Standard Stack

### Core (pinned)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `fastapi` | 0.138.1 | HTTP framework | [VERIFIED: PyPI] Latest stable, mature ASGI |
| `uvicorn[standard]` | 0.49.0 | ASGI server | [VERIFIED: PyPI] Default FastAPI runner with uvloop + httptools |
| `pydantic` | 2.13.4 | Validation | [VERIFIED: PyPI] v2 mandated by CLAUDE.md |
| `pydantic-settings` | 2.14.2 | Env var settings | [VERIFIED: PyPI] Standard Pydantic v2 settings (split out of pydantic itself) |
| `langgraph` | 1.2.6 | Agent orchestration | [VERIFIED: PyPI] 1.x stable line (1.0 released Oct 2025) |
| `langgraph-checkpoint-postgres` | 3.1.0 | Postgres checkpointer | [VERIFIED: PyPI] Provides AsyncPostgresSaver |
| `langchain` | 1.3.11 | LLM tooling | [VERIFIED: PyPI] 1.x stable |
| `langchain-openai` | 1.3.3 | ChatOpenAI for OpenRouter | [VERIFIED: PyPI] Cleanest path to OpenRouter via OpenAI-compatible base_url |
| `langsmith` | 0.9.3 | Tracing client | [VERIFIED: PyPI] Auto-detected by langchain when env vars set |
| `asyncpg` | 0.31.0 | Postgres async driver | [VERIFIED: PyPI] Fastest async driver; preferred for app DB |
| `psycopg[binary,pool]` | 3.3.4 | Postgres for LangGraph | [VERIFIED: PyPI] LangGraph's AsyncPostgresSaver depends on psycopg 3 |
| `sqlalchemy` | 2.0.51 | ORM | [VERIFIED: PyPI] 2.0 async-native API |
| `alembic` | 1.18.5 | Migrations | [VERIFIED: PyPI] Use `async` template |
| `redis` | 8.0.1 | Redis client | [VERIFIED: PyPI] redis-py supports async natively |
| `arq` | 0.28.0 | Redis queue | [VERIFIED: PyPI] LANDA stack |
| `httpx` | 0.28.1 | Async HTTP | [VERIFIED: PyPI] FastAPI ecosystem standard |
| `structlog` | 26.1.0 | Structured logs | [VERIFIED: PyPI] Routed through stdlib via ProcessorFormatter |
| `sentry-sdk[fastapi]` | 2.63.0 | Error tracking | [VERIFIED: PyPI] FastApiIntegration + StarletteIntegration auto-enable |
| `asgi-correlation-id` | 5.0.1 | Request correlation | [VERIFIED: PyPI] Bridges request_id → structlog + Sentry transaction_id |
| `orjson` | 3.11.9 | Fast JSON | [VERIFIED: PyPI] FastAPI `default_response_class=ORJSONResponse` |
| `python-multipart` | 0.0.32 | Form parsing | [VERIFIED: PyPI] Needed by FastAPI for webhook form/file uploads |
| `tenacity` | 9.1.4 | Retries (used by integrations from F2) | [VERIFIED: PyPI] Install now so import path is stable |
| `pybreaker` | 1.4.1 | Circuit breaker (F2) | [VERIFIED: PyPI] Install now, same reason |

### Dev / CI
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `pytest` | 9.1.1 | Tests | [VERIFIED: PyPI] |
| `pytest-asyncio` | 1.4.0 | Async test support | [VERIFIED: PyPI] |
| `ruff` | 0.15.20 | Lint + format | [VERIFIED: PyPI] Replaces black/isort/flake8; 10-100x faster |
| `black` | 26.5.1 | Formatter | [VERIFIED: PyPI] CLAUDE.md mandates black; ruff format is black-compatible so both work — keep black per CLAUDE.md |
| `mypy` | 2.1.0 | Type checker | [VERIFIED: PyPI] CLAUDE.md says `mypy --strict` |
| `pre-commit` | 4.6.0 | Git hook framework | [VERIFIED: PyPI] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `asyncpg` for app DB | `psycopg 3` async | Could use psycopg everywhere (single driver), but asyncpg is ~3-5x faster for high-throughput. Acceptable to use psycopg for both if we want one fewer dep — but the LangGraph checkpointer **requires** psycopg, so we already have it. **Recommendation: stick with asyncpg for app DB (SQLAlchemy 2.0 async engine via `postgresql+asyncpg://`) and psycopg only for the LangGraph checkpointer.** |
| `requirements.txt` only | `pyproject.toml` + `uv.lock` | pyproject is the 2026 standard; uv gives reproducible lockfiles with sub-second installs. requirements.txt alone leaves transitive deps unpinned. **Recommendation: pyproject.toml + uv** |
| Nixpacks | Custom Dockerfile | Nixpacks is in maintenance mode (Railway replaced it with Railpack in March 2026). Railpack is newer/beta. A custom Dockerfile gives reproducible 15s cached builds vs ~90s Nixpacks builds and is portable. **Recommendation: custom Dockerfile** |
| Built-in `logging` | `structlog` routed via stdlib | CLAUDE.md says structlog. The current best practice is to use `structlog.stdlib.ProcessorFormatter` so uvicorn/FastAPI/SQLAlchemy logs flow through the same processor chain — single JSON output stream |
| `python-json-logger` | `structlog` | structlog is the modern choice per Dash0's 2026 Python logging survey |
| ChatAnthropic / OpenAI SDK direct | `ChatOpenAI(base_url=openrouter)` | LOCKED in CLAUDE.md — all LLMs via OpenRouter |
| `langchain-openrouter` | `langchain-openai` pointed at OpenRouter | There is a `langchain-openrouter` community package now, but `langchain-openai` with `base_url` is the canonical pattern OpenRouter itself documents and is what LangGraph examples assume. Less surface area to break. |

**Installation (`pyproject.toml` excerpt, then `uv sync`):**
```toml
[project]
name = "landa-agent-service"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi==0.138.1",
  "uvicorn[standard]==0.49.0",
  "pydantic==2.13.4",
  "pydantic-settings==2.14.2",
  "langgraph==1.2.6",
  "langgraph-checkpoint-postgres==3.1.0",
  "langchain==1.3.11",
  "langchain-openai==1.3.3",
  "langsmith==0.9.3",
  "asyncpg==0.31.0",
  "psycopg[binary,pool]==3.3.4",
  "sqlalchemy==2.0.51",
  "alembic==1.18.5",
  "redis==8.0.1",
  "arq==0.28.0",
  "httpx==0.28.1",
  "structlog==26.1.0",
  "sentry-sdk[fastapi]==2.63.0",
  "asgi-correlation-id==5.0.1",
  "orjson==3.11.9",
  "python-multipart==0.0.32",
  "tenacity==9.1.4",
  "pybreaker==1.4.1",
]

[dependency-groups]
dev = [
  "pytest==9.1.1",
  "pytest-asyncio==1.4.0",
  "ruff==0.15.20",
  "black==26.5.1",
  "mypy==2.1.0",
  "pre-commit==4.6.0",
]
```

## Package Legitimacy Audit

Ran `slopcheck scan` (v0.6.1) against all 28 dependencies on 2026-06-27. Versions verified against PyPI via `pip index versions`.

| Package | Registry | slopcheck | Notes |
|---------|----------|-----------|-------|
| fastapi 0.138.1 | PyPI | OK | — |
| uvicorn 0.49.0 | PyPI | OK | — |
| pydantic 2.13.4 | PyPI | OK | — |
| pydantic-settings 2.14.2 | PyPI | OK | — |
| langgraph 1.2.6 | PyPI | OK | — |
| langgraph-checkpoint-postgres 3.1.0 | PyPI | OK | — |
| langchain 1.3.11 | PyPI | OK | — |
| langchain-openai 1.3.3 | PyPI | OK | Flagged as `HALLUCINATION_PATTERN` (name starts with `langchain-`) but suppressed — package is established (langchain official) |
| langsmith 0.9.3 | PyPI | OK | — |
| asyncpg 0.31.0 | PyPI | OK | — |
| psycopg 3.3.4 | PyPI | OK | — |
| sqlalchemy 2.0.51 | PyPI | OK | — |
| alembic 1.18.5 | PyPI | OK | — |
| redis 8.0.1 | PyPI | OK | — |
| arq 0.28.0 | PyPI | OK | — |
| httpx 0.28.1 | PyPI | OK | — |
| structlog 26.1.0 | PyPI | OK | — |
| sentry-sdk 2.63.0 | PyPI | OK | Flagged as `HALLUCINATION_PATTERN` (ends in `-sdk`) but suppressed — official Sentry package |
| asgi-correlation-id 5.0.1 | PyPI | OK | — |
| orjson 3.11.9 | PyPI | OK | — |
| python-multipart 0.0.32 | PyPI | OK | Flagged as `HALLUCINATION_PATTERN` (starts `python-`) but suppressed — Starlette upstream dep |
| tenacity 9.1.4 | PyPI | OK | — |
| pybreaker 1.4.1 | PyPI | OK | — |
| pytest 9.1.1 | PyPI | OK | — |
| pytest-asyncio 1.4.0 | PyPI | OK | — |
| ruff 0.15.20 | PyPI | OK | — |
| black 26.5.1 | PyPI | OK | — |
| mypy 2.1.0 | PyPI | OK | — |
| pre-commit 4.6.0 | PyPI | OK | — |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none (the three `HALLUCINATION_PATTERN` flags are info-level only and slopcheck's verdict was OK for all)

## Architecture Patterns

### System Architecture (Phase 1 deliverable surface)

```
                        ┌──────────────────────────────────────────────────┐
                        │                    Railway Project                │
                        │                                                   │
   Internet ──TLS──────▶│  ┌─ agent-service group ─────┐                    │
                        │  │                            │                    │
                        │  │  ┌─ landa-agent (FastAPI)─┴──┐                  │
                        │  │  │  Dockerfile               │  webhooks/       │
                        │  │  │  uvicorn + lifespan       │  /health         │
                        │  │  │  - asyncpg pool           │                  │
                        │  │  │  - psycopg AsyncPg pool   │                  │
                        │  │  │  - Redis pool             │                  │
                        │  │  │  - AsyncPostgresSaver     │                  │
                        │  │  │  - get_llm(role) factory  │                  │
                        │  │  │      └──HTTPS──▶ OpenRouter (egress)        │
                        │  │  │      └──HTTPS──▶ LangSmith   (egress)       │
                        │  │  │      └──HTTPS──▶ Sentry     (egress)        │
                        │  │  └─────┬──────────────────┘                     │
                        │  │        │                                        │
                        │  │   railway.internal (Wireguard mesh, no egress) │
                        │  │        │                                        │
                        │  │  ┌─────▼──────┐    ┌──────────┐                 │
                        │  │  │ app-postgres│    │ app-redis│                │
                        │  │  │  (Railway)  │    │ (Railway)│                │
                        │  │  └─────────────┘    └──────────┘                │
                        │  └─────────────────────────────────────────────────┘
                        │                                                   │
                        │  ┌─ chatwoot group (idle in F1) ─┐                │
                        │  │  chatwoot-rails (web, 4GB+)   │                │
                        │  │  chatwoot-sidekiq (worker)    │                │
                        │  │  chatwoot-postgres (pgvector) │                │
                        │  │  chatwoot-redis               │                │
                        │  │  custom domain chat.landatech.org              │
                        │  └─────────────────────────────────────────────────┘
                        └──────────────────────────────────────────────────┘
```

Phase 1 wires only: FastAPI ↔ app-postgres, FastAPI ↔ app-redis, FastAPI ↔ OpenRouter (one dummy call), FastAPI ↔ LangSmith (auto), FastAPI ↔ Sentry, and Chatwoot stack idle. WhatsApp, SoftSeguros, and lambda-proyect plumb in F2+.

### Recommended Project Layout (locked by CLAUDE.md)

The CLAUDE.md folder map is final; this phase materializes it as empty Python packages with `__init__.py` files plus a few wired modules:

```
landa-agent-service/
├── app/
│   ├── __init__.py
│   ├── main.py                       # FastAPI app + lifespan + router includes
│   ├── features/{qa,payment,escalation,handoff}/__init__.py
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── openrouter.py             # ONLY integration wired in F1: get_llm(role)
│   │   └── (softseguros|chatwoot|meta_cloud|lambda_proyect).py  # F2+
│   ├── security/__init__.py          # placeholders OK in F1
│   ├── memory/__init__.py            # placeholders OK in F1
│   ├── models/__init__.py
│   ├── webhooks/__init__.py          # empty in F1; F2 adds meta.py, chatwoot.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py               # central BaseSettings (env_prefix=…)
│   │   ├── llm.py                    # Pydantic LLMSettings + get_llm(role)
│   │   ├── logging.py                # structlog setup
│   │   ├── observability.py          # Sentry init + LangSmith env-based init
│   │   ├── db.py                     # SQLAlchemy async engine + asyncpg pool
│   │   ├── checkpointer.py           # AsyncPostgresSaver lifespan helper
│   │   └── redis.py                  # Redis async client + pool
│   └── healthcheck.py                # /health router
├── alembic/
│   ├── env.py                        # async template
│   └── versions/
├── alembic.ini
├── knowledge/                        # empty in F1 (KB lives here from F3)
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   └── test_llm_factory.py
├── .github/workflows/ci.yml
├── .pre-commit-config.yaml
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── .dockerignore
├── .env.example
├── .gitignore
├── CLAUDE.md
└── README.md
```

### Pattern 1: FastAPI `lifespan` owns all async resources

**What:** All long-lived async resources (asyncpg pool, AsyncPostgresSaver, Redis pool) are created during the FastAPI `lifespan` startup phase and stored on `app.state`. Endpoints reach them via `Depends`.

**When to use:** Always for FastAPI in 2026. Replaces the deprecated `@app.on_event("startup")` decorators.

**Example:**
```python
# Source: https://fastapi.tiangolo.com/advanced/events/
# Source: https://docs.langchain.com/oss/python/langgraph/add-memory
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from redis.asyncio import Redis, ConnectionPool

from app.config.settings import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Postgres app engine (asyncpg)
    app.state.db_engine = create_async_engine(
        settings.postgres_url.replace("postgresql://", "postgresql+asyncpg://"),
        pool_size=10, max_overflow=5, pool_pre_ping=True,
    )
    app.state.SessionLocal = async_sessionmaker(app.state.db_engine, expire_on_commit=False)

    # Redis pool
    app.state.redis_pool = ConnectionPool.from_url(settings.redis_url, max_connections=20)
    app.state.redis = Redis(connection_pool=app.state.redis_pool)

    # LangGraph checkpointer (psycopg-backed)
    cp_cm = AsyncPostgresSaver.from_conn_string(settings.postgres_url)
    app.state.checkpointer = await cp_cm.__aenter__()
    app.state._cp_cm = cp_cm
    await app.state.checkpointer.setup()  # idempotent; creates checkpoint tables

    yield

    await app.state._cp_cm.__aexit__(None, None, None)
    await app.state.redis.aclose()
    await app.state.db_engine.dispose()

app = FastAPI(lifespan=lifespan, default_response_class=ORJSONResponse)
```

**Gotcha:** `AsyncPostgresSaver.from_conn_string` returns an async context manager — naive `async with` blocks at function level work in scripts but break in long-running servers. Use the explicit `__aenter__/__aexit__` pattern shown above (the "I Built a LangGraph + FastAPI Agent... and Spent Days Fighting Postgres" Medium write-up documents exactly this trap).

### Pattern 2: OpenRouter via `ChatOpenAI` with role-based factory

**What:** A single `get_llm(role: str)` function returns a configured `ChatOpenAI` pointed at OpenRouter, with role selecting the model from env vars (`LLM_MODEL_CONVERSATION`, `LLM_MODEL_JUDGE`, etc.).

**Example:**
```python
# Source: https://openrouter.ai/docs/guides/community/openai-sdk
# Source: https://markaicode.com/integrate/openrouter-with-langchain/
from functools import lru_cache
from langchain_openai import ChatOpenAI
from app.config.settings import settings

ROLE_MODEL_MAP = {
    "conversation": settings.llm_model_conversation,  # default google/gemini-2.0-pro
    "judge":        settings.llm_model_judge,         # default google/gemini-2.0-flash
    "intent":       settings.llm_model_intent,        # default google/gemini-2.0-flash
}

@lru_cache(maxsize=8)
def get_llm(role: str) -> ChatOpenAI:
    model = ROLE_MODEL_MAP[role]
    return ChatOpenAI(
        model=model,
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key.get_secret_value(),
        default_headers={
            "HTTP-Referer": settings.app_public_url,  # for OpenRouter analytics
            "X-Title": "landa-agent-service",
        },
        temperature=0 if role == "judge" else 0.7,
        # Fallback models (OpenRouter native feature) via model_kwargs:
        model_kwargs={"models": settings.llm_fallbacks(role)},
    )
```

**Notes:**
- The `models: [...]` extra param is OpenRouter's "automatic provider fallback" — if the primary model is overloaded, OpenRouter transparently retries one of the listed fallbacks before returning. Headers `HTTP-Referer` + `X-Title` are optional but surface this app in OpenRouter analytics.
- Costs are observable via `X-OpenRouter-Credits` response headers. To get token counts inside Python, request `include_usage=True` and read `response.response_metadata["token_usage"]`. For phase 1 we only need the dummy invoke; cost dashboards arrive in F7.

### Pattern 3: structlog routed through stdlib, with correlation ID + Sentry crosslink

**What:** Configure structlog to use `structlog.stdlib.ProcessorFormatter`. Add `asgi-correlation-id` middleware to FastAPI; bind the correlation ID to `structlog.contextvars` so every log line carries it. `sentry-sdk` picks up the same correlation ID as the transaction_id automatically.

**Example:**
```python
# Source: https://www.structlog.org/en/stable/logging-best-practices.html
# Source: https://github.com/snok/asgi-correlation-id
# Source: https://medium.com/@abhinav.dobhal/your-fastapi-service-is-leaking-pii-into-your-logs-...
import logging, structlog, re
from asgi_correlation_id import CorrelationIdMiddleware
from structlog.contextvars import merge_contextvars, bind_contextvars

PII_KEYS = {"phone", "wa_token", "openrouter_api_key", "saldo", "documento", "cedula"}
PHONE_RE = re.compile(r"\+?\d[\d\s\-]{7,}\d")

def redact_pii(logger, name, event_dict):
    for k in list(event_dict):
        if k.lower() in PII_KEYS:
            event_dict[k] = "[REDACTED]"
    # value-side scrub for free-text events
    for k, v in event_dict.items():
        if isinstance(v, str):
            event_dict[k] = PHONE_RE.sub("[REDACTED_PHONE]", v)
    return event_dict

structlog.configure(
    processors=[
        merge_contextvars,                                     # adds correlation_id, etc.
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        redact_pii,                                            # MUST come before renderer
        structlog.processors.dict_tracebacks,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)

# In main.py:
app.add_middleware(CorrelationIdMiddleware, header_name="X-Request-ID")
```

**Notes:**
- `redact_pii` is a **key-name + regex hybrid**. Key-name redaction is more reliable than pure regex, but free-text fields (e.g. `event="user sent message: ..."`) still need a regex sweep — hence both.
- Once `sentry-sdk` is initialized, the `asgi-correlation-id` middleware automatically writes `correlation_id` as the Sentry `transaction_id`, giving one-click correlation between a structlog JSON line and a Sentry issue.

### Pattern 4: Sentry init — let auto-detection do the work

**What:** `sentry_sdk.init(dsn=...)` early in `main.py`. The SDK detects FastAPI in `sys.modules` and **automatically** enables `StarletteIntegration` + `FastApiIntegration` — no manual middleware needed.

**Example:**
```python
# Source: https://docs.sentry.io/platforms/python/integrations/fastapi/
import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration

sentry_sdk.init(
    dsn=settings.sentry_dsn,
    environment=settings.env,                # dev|staging|prod
    traces_sample_rate=0.1,                  # 10% APM; tune in F7
    profiles_sample_rate=0.0,                # off in F1
    send_default_pii=False,                  # critical — don't send headers/bodies blindly
    integrations=[
        StarletteIntegration(transaction_style="endpoint"),
        FastApiIntegration(transaction_style="endpoint"),
    ],
    before_send=lambda event, hint: scrub_sentry_event(event),  # PII firewall on outbound
)
```

**Gotcha:** Old guides still tell you to call `sentry_sdk.integrations.asgi.SentryAsgiMiddleware(app)` manually. **Don't.** Sentry 2.x detects FastAPI automatically and that manual middleware now double-wraps and breaks `request.body()` (this is mentioned in Sentry's own ASGI docs).

### Pattern 5: Health endpoint with parallel async probes

**What:** `GET /health` runs Postgres, Redis, OpenRouter, and LangSmith env checks **concurrently** with `asyncio.gather`, each wrapped in a 1-second timeout. Returns `healthy`/`degraded`/`unhealthy` with a JSON breakdown.

**Example:**
```python
# Source: https://nurbak.com/en/blog/health-check-endpoint/
# Source: https://kludex.github.io/fastapi-health/
import asyncio, time
from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()

async def _probe(coro, timeout=1.0):
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(coro, timeout=timeout)
        return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}

async def _check_pg(req: Request):
    async with req.app.state.SessionLocal() as s:
        await s.execute(text("SELECT 1"))

async def _check_redis(req: Request):
    await req.app.state.redis.ping()

async def _check_openrouter():
    # Cheap reachability check — HEAD /api/v1 returns 200/404 either way (proves DNS+TLS)
    async with httpx.AsyncClient(timeout=1.0) as c:
        await c.head("https://openrouter.ai/api/v1")

@router.get("/health")
async def health(request: Request):
    pg, redis_, openrouter = await asyncio.gather(
        _probe(_check_pg(request)),
        _probe(_check_redis(request)),
        _probe(_check_openrouter()),
    )
    langsmith_env = {"ok": bool(settings.langsmith_api_key and settings.langsmith_project)}
    components = {"postgres": pg, "redis": redis_, "openrouter": openrouter, "langsmith_env": langsmith_env}
    status = "healthy" if all(c["ok"] for c in components.values()) else "degraded"
    return {"status": status, "components": components, "version": settings.app_version}
```

**Notes:**
- Phase 1 explicitly excludes Meta/SoftSeguros health checks (out of scope).
- `langsmith_env` is just a presence check on env vars — no API call needed. Langsmith doesn't expose a "ping" endpoint and we don't want to burn traces on a noop.
- Don't return 503 unless something Critical is down — Railway uses HTTP 200 from /health to keep the service routed, and a noisy 503 will pull the service out of rotation. For F1 the recommended posture is: always 200, status field tells the truth.

### Anti-Patterns to Avoid

- **Don't** put DB pool / checkpointer setup inside individual endpoint dependencies — they re-create connections per request. Use lifespan + app.state.
- **Don't** use `@app.on_event("startup")` — deprecated in Starlette 0.36+.
- **Don't** call `await checkpointer.setup()` on every request — it's idempotent but does a `SELECT` against the migrations table.
- **Don't** initialize Sentry **after** importing FastAPI routers — auto-detection of integrations runs at `init()` time and looks at currently imported modules.
- **Don't** mix `python-dotenv` + `pydantic-settings` — `pydantic-settings` already reads `.env` natively (`model_config = SettingsConfigDict(env_file=".env")`).
- **Don't** hardcode the model name in `get_llm` — must come from env (CLAUDE.md rule).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Request correlation IDs | DIY middleware + contextvar wiring | `asgi-correlation-id` | 5 lines vs 50, plus auto Sentry transaction_id linkage |
| Structured logs | `print(json.dumps(...))` | `structlog` + `ProcessorFormatter` | Context propagation, processors, stdlib integration — all built in |
| Connection pooling | Manual asyncpg connection management | SQLAlchemy 2.0 `create_async_engine` with `AsyncAdaptedQueuePool` | Async-safe pool with overflow + pre-ping |
| LangGraph state storage | DIY Postgres tables | `AsyncPostgresSaver` from `langgraph-checkpoint-postgres` | Schema versioning, migrations, replay — all handled |
| HMAC validation (F2) | DIY `hmac.compare_digest` wrapper | Use stdlib `hmac` directly; don't add a third-party validator | stdlib is already constant-time |
| Chatwoot Postgres+Redis | Reuse the agent's Postgres/Redis | Separate Chatwoot data stores | Isolation, independent scaling, Chatwoot's migrations don't touch agent's tables |
| Retries / circuit breaker (F2) | Custom retry loops | `tenacity` + `pybreaker` | Battle-tested patterns, decorators |
| PII detection | Custom regex zoo | structlog processor for **keys** + minimal regex for known patterns (phone, email) only | Aim for a tight redaction policy on phase 1; comprehensive PII detection (Presidio etc.) is F5/F7 territory |
| Health endpoint plumbing | `fastapi-healthchecks` lib | Roll a 30-line endpoint with `asyncio.gather` | The library adds a layer without solving the actual question "is my pool alive". Hand-roll the four probes — they're trivial and explicit. (Listed in "don't hand-roll" inverted: do hand-roll this one because the lib doesn't pay off at this scale.) |

## Runtime State Inventory

This is a greenfield phase — no existing infrastructure to migrate. The repo is empty; `.planning/` is the only seeded content. Section intentionally minimal.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None — no existing DB | None |
| Live service config | None — no existing Railway project | This phase creates it |
| OS-registered state | None | None |
| Secrets/env vars | `.env.example` does not yet exist; all variable names defined in CLAUDE.md "Quick start" | Author `.env.example` from the CLAUDE.md list |
| Build artifacts | None | None |

## Common Pitfalls

### Pitfall 1: AsyncPostgresSaver setup() called inside request handlers
**What goes wrong:** Stray "table already exists" errors, slow first request, or worse — concurrent `setup()` calls racing on migrations.
**Why:** Developers copy the docs example (`async with AsyncPostgresSaver.from_conn_string(...) as cp: await cp.setup()`) into per-request code.
**How to avoid:** Call `setup()` exactly once during lifespan startup. The function is idempotent but doing it per-request adds 50ms latency and a Postgres round-trip.
**Warning signs:** Log lines like `relation "checkpoints" already exists` on cold start.

### Pitfall 2: Mixing psycopg and asyncpg drivers without thought
**What goes wrong:** Two connection pools to the same Postgres, double the connection count, hard to debug pool exhaustion.
**Why:** LangGraph's checkpointer requires psycopg 3; the rest of the app wants asyncpg for speed.
**How to avoid:** Be explicit. The agent's app data uses `asyncpg` via SQLAlchemy. The LangGraph checkpointer uses `psycopg` directly. Each has its own pool. Document max connections per pool so the sum stays under Postgres's `max_connections`.
**Warning signs:** `FATAL: too many connections for role` from Postgres under load.

### Pitfall 3: Nixpacks build picks wrong Python or skips uv
**What goes wrong:** Builds take 90+ seconds, image is 2GB+, mypy/ruff aren't installed in the runner.
**Why:** Nixpacks tries to autodetect; it sometimes picks Python 3.11 or skips dev deps.
**How to avoid:** Use a custom Dockerfile with explicit `python:3.12-slim` base, `uv` for installs, and a two-stage build. Pin everything.
**Warning signs:** Railway build logs show `Detected Python 3.x` where x ≠ 12; deploy size > 500MB.

### Pitfall 4: LangSmith free tier silently drops traces over 5K/month
**What goes wrong:** Traces stop appearing mid-month. No alert.
**Why:** Free Developer tier caps at 5,000 traces with 14-day retention. With judge + intent + conversation calls per turn, ~3 LLM calls per WhatsApp message = ~1,600 messages/month before hitting the cap.
**How to avoid:** (a) Sample LangSmith traces at startup (`LANGSMITH_SAMPLING_RATE=0.5` if cost is a concern), (b) put DPG volume estimate in front of Maxi before F7, (c) the audit log (F5) is the compliance-grade record — LangSmith is for debugging only.
**Warning signs:** Sudden drop in trace count in LangSmith dashboard around mid-month.

### Pitfall 5: Chatwoot Sidekiq OOM on Railway free/hobby plan
**What goes wrong:** Sidekiq worker crashes repeatedly with OOM; webhook deliveries stack up.
**Why:** Sidekiq under load can use 1GB+ RAM; Chatwoot's official minimum is 4GB total. Railway's smallest plan instance is 512MB and the next is 8GB — there's no middle tier.
**How to avoid:** Provision the chatwoot-rails and chatwoot-sidekiq services with ≥4GB each (Railway's "Pro" plan or `Hobby` with raised limits). Plan accordingly in F1 budget conversation with Maxi.
**Warning signs:** `Sidekiq exited with signal=9` in Chatwoot logs.

### Pitfall 6: Custom domain TLS cert stuck in "pending" on Railway
**What goes wrong:** Setting up `chat.landatech.org` returns the Railway *.up.railway.app certificate; browsers complain.
**Why:** Both the CNAME **and** the TXT `_acme-challenge` records must be set. Cloudflare in front of Railway must be on `Full` (not `Full (Strict)`) until cert issues, then can be upgraded. Repeated add/remove cycles can hit Let's Encrypt rate limits (5 failures/hour, then a multi-hour lockout).
**How to avoid:** Set both records, leave Cloudflare proxy **off** (DNS only / grey cloud) until Railway shows "Certificate issued", then flip on if desired. Don't retry rapidly.
**Warning signs:** Railway domain panel shows "Certificate pending" for >15 minutes; Let's Encrypt rate-limit errors in Railway logs.

### Pitfall 7: structlog PII leak via uncaptured exceptions
**What goes wrong:** A traceback contains a phone number in a variable repr, logged in JSON, exfiltrated to Sentry or stdout.
**Why:** Sentry's `send_default_pii=False` only stops headers/cookies — local variables in tracebacks still appear.
**How to avoid:** Sentry `before_send` hook scrubs `event["exception"]["values"][i]["stacktrace"]["frames"][j]["vars"]`. structlog `dict_tracebacks` processor + a `redact_pii` processor that recursively walks the dict.
**Warning signs:** Discovery rather than prevention — audit a sample of Sentry events monthly for phone-like strings.

### Pitfall 8: pydantic-settings + env_prefix collision
**What goes wrong:** Two BaseSettings classes both default-read `WA_TOKEN` because of overlapping prefixes.
**Why:** CLAUDE.md says "env_prefix per domain": `LLM_`, `WA_`, `SOFTSEGUROS_`. Without `extra="ignore"`, settings with different prefixes raise on unknown env vars.
**How to avoid:** Each settings class declares `model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")`. Compose them in a top-level `Settings` container.

## Code Examples

### Dockerfile (production-ready, ~80MB final image)
```dockerfile
# Source: https://docs.railway.com/guides/fastapi + uv docs
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

### Alembic async env.py (key lines)
```python
# Source: https://github.com/sqlalchemy/alembic/blob/main/alembic/templates/async/env.py
# Generated by `alembic init -t async alembic`
from sqlalchemy.ext.asyncio import async_engine_from_config
from app.config.settings import settings
from app.models import metadata as target_metadata  # MUST import models BEFORE this line resolves

config.set_main_option(
    "sqlalchemy.url",
    settings.postgres_url.replace("postgresql://", "postgresql+asyncpg://"),
)

async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()
```

### `.github/workflows/ci.yml` (minimum viable)
```yaml
# Source: https://docs.github.com/actions/guides/building-and-testing-python
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install uv
        run: pip install uv==0.4.30
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run black --check .
      - run: uv run mypy app
      - run: uv run pytest -q
```

### `.pre-commit-config.yaml`
```yaml
# Source: https://github.com/astral-sh/ruff-pre-commit
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.20
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/psf/black
    rev: 26.5.1
    hooks:
      - id: black
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v2.1.0
    hooks:
      - id: mypy
        additional_dependencies: [pydantic, types-redis]
```

### `pyproject.toml` ruff + black + mypy config
```toml
[tool.ruff]
line-length = 100
target-version = "py312"
[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "C90", "N", "UP", "S", "ASYNC"]
ignore = ["S101"]   # allow asserts in tests
[tool.black]
line-length = 100
target-version = ["py312"]
[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `@app.on_event("startup")` | `lifespan=` context manager | FastAPI 0.93 (2023); fully deprecated 2024 | Old form will be removed |
| `SentryAsgiMiddleware(app)` manual | Auto-detected `FastApiIntegration` | sentry-sdk 2.0 (2024) | Manual middleware now double-wraps |
| Nixpacks default builder on Railway | Railpack (beta) or custom Dockerfile | Mar 2026 (Railpack launch) | Nixpacks now in maintenance |
| Pydantic v1 `BaseSettings` | `pydantic-settings.BaseSettings` | Pydantic 2.0 (2023) | Settings split into own package |
| black + isort + flake8 | ruff (lint + format) | Ruff 0.4 / late 2024 | 10-100x faster; CLAUDE.md still requires black so we run both |
| `requirements.txt` flat | `pyproject.toml` + `uv.lock` | uv 0.4+ (2025) | uv now the de-facto tool |
| LangGraph 0.x checkpointer | langgraph 1.x + langgraph-checkpoint-postgres 3.x | LangGraph 1.0 (Oct 2025) | Schema-versioned checkpoints, official multi-tenant guidance |
| psycopg2 | psycopg 3 (and psycopg[binary,pool]) | LangGraph 1.x requires psycopg 3 | One-driver requirement |

**Deprecated/outdated:**
- LangChain `OpenAI` (the old completion class) — long deprecated; use `ChatOpenAI`
- `databases` async wrapper — superseded by SQLAlchemy 2.0 async
- `aiocache` — Redis 8.x async client and arq cover the use cases

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Railway's "Hobby" plan provides ≥4GB RAM per service (needed for Chatwoot Rails + Sidekiq) | Common Pitfalls #5 | If Maxi's Railway plan is smaller, Chatwoot needs upgrade or external hosting |
| A2 | DPG's expected WhatsApp volume in v1 is <1.5K messages/month, keeping LangSmith free tier viable | Pitfalls #4 | Could need to upgrade LangSmith to $39 Plus tier early |
| A3 | LandaTech owns `landatech.org` DNS and can add `chat.landatech.org` CNAME pointing at Railway | Pitfalls #6 | If DNS is managed elsewhere or behind change control, allocate time |
| A4 | `requirements.txt` is acceptable to drop in favor of `pyproject.toml + uv.lock` | Alternatives Considered | CLAUDE.md mentions `pip install -r requirements.txt` in Quick start — if Maxi prefers requirements.txt, keep it (uv supports it natively) |
| A5 | Custom Dockerfile is preferred over Railpack on Railway | Alternatives Considered | If Maxi wants zero-config deploys, switch to Railpack — costs ~30s extra per build, gains ~zero config files |
| A6 | Chatwoot's official community Railway template covers F1 needs without modification | Architecture Patterns | Template might bundle features (pgvector) we don't need; could swap for a slimmer custom docker-compose |
| A7 | LangSmith tracing should run from F1 (not deferred to F7) | Phase scope | Roadmap says yes — flagged for confirmation only |
| A8 | OpenRouter's `models: [fallback1, fallback2]` parameter is the right fallback path (vs LangChain's `with_fallbacks()`) | Pattern 2 | OpenRouter-native is faster (one request) but uses non-standard OpenAI field — if a future SDK strips it, fallback breaks. LangChain's `with_fallbacks()` is more portable |

## Open Questions

1. **What is Maxi's Railway plan tier?**
   - What we know: Chatwoot needs ≥4GB RAM total; Railway's free/hobby tiers may not provide this per-service.
   - What's unclear: Current plan and budget.
   - Recommendation: Confirm with Maxi during discuss-phase or before plan execution.

2. **Single `landa-agent-service` Railway project, or separate projects for agent vs Chatwoot?**
   - What we know: Same project = free private networking, simpler env management. Separate = isolation, independent billing.
   - What's unclear: Maxi's preference on isolation vs simplicity.
   - Recommendation: Default to **single Railway project, two service groups** (agent + chatwoot). Easy to split later if needed.

3. **`requirements.txt` (CLAUDE.md quick-start) vs `pyproject.toml + uv.lock`?**
   - What we know: pyproject is 2026 standard; uv is fast and reproducible.
   - What's unclear: Whether Maxi has tool preference or constraints (CI image availability).
   - Recommendation: **pyproject.toml + uv.lock as source of truth; ship a `requirements.txt` generated by `uv pip compile` for CLAUDE.md quick-start compatibility.**

4. **Sentry environment naming?**
   - What we know: Standard is `dev|staging|prod`.
   - What's unclear: Whether DPG/staging environment exists separately.
   - Recommendation: Use `dev|prod` for F1; add `staging` only when DPG sandbox materializes.

5. **GitHub Actions: free runners or self-hosted on Railway?**
   - What we know: free public-repo Ubuntu runners are fine for pytest + ruff.
   - What's unclear: Whether repo is private (free private repo runners have 2000 min/mo limit).
   - Recommendation: Free runners for now; revisit if CI minutes become a bottleneck.

6. **LangSmith project name and tracing scope in F1?**
   - What we know: env vars `LANGSMITH_API_KEY` + `LANGSMITH_PROJECT` enable auto-tracing of `langchain` calls.
   - What's unclear: Project name convention — `landa-agent-dev`? `landa-dpg-prod`?
   - Recommendation: `landa-agent-{env}` (e.g. `landa-agent-dev`).

7. **Should `.env.example` redact known DPG credentials or list every variable?**
   - What we know: CLAUDE.md "Quick start" lists ~10 critical env vars but isn't exhaustive (no LANGSMITH_PROJECT for example).
   - What's unclear: Whether F1 should produce a "complete" .env.example or "minimum to boot".
   - Recommendation: Complete: every env var the code references, with placeholder values like `<your-openrouter-key>` and a comment explaining each. The lambda-proyect integration vars can be empty until F5.

## Environment Availability

Skipped — Phase 1 deliverables are infrastructure-as-code (Railway template / Dockerfile / pyproject.toml). The dev-side requirements (Python 3.12, git, optional uv/docker) are assumed present on Maxi's machine and not part of this phase's verifiable output. Local-dev environment probing is irrelevant to "infra running on Railway".

## Validation Architecture

`workflow.nyquist_validation` is `false` in `.planning/config.json`. Section omitted per protocol.

## Security Domain

`security_enforcement` is `true` and ASVS Level 1 in `.planning/config.json`. Phase 1 establishes the foundation; full 13-layer pipeline lands in F3/F5.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Partial | Webhooks (Meta, Chatwoot) use HMAC — implemented F2 onwards. F1 establishes infra. |
| V3 Session Management | No | No user sessions in v1 — WhatsApp identity = phone number, conversation_id |
| V4 Access Control | No (phase 1) | Internal endpoints (`POST /case/handoff` from lambda-proyect) protected by `LAMBDA_PROYECT_INTERNAL_TOKEN` shared secret — F5 |
| V5 Input Validation | Yes | **Pydantic v2** on every webhook payload; per CLAUDE.md mandatory |
| V6 Cryptography | Yes (foundation) | F1 deliverables for crypto: TLS via Railway (auto Let's Encrypt). HMAC validation lives in `app/security/hmac_validator.py` (F2). Secrets via Railway env vars, not committed. **Never hand-roll crypto.** |
| V7 Errors & Logging | Yes | structlog redaction + Sentry `send_default_pii=False` + `before_send` scrubber — F1 implements |
| V8 Data Protection | Yes | `.env` excluded from git; pyproject doesn't pin secrets; Railway secrets injected at runtime; no PII persisted (per project Constraints) |
| V14 Configuration | Yes | F1 produces `.env.example` documenting every var; settings classes validate at startup (fail fast if a required var is missing) |

### Known Threat Patterns for Python FastAPI + LangGraph + Railway

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Secrets committed to git | Information disclosure | `.gitignore` covers `.env*`, `*.pem`, `*.key`; pre-commit hook (`detect-secrets` or `gitleaks` — out of scope F1 but recommended F5) |
| Slopsquatted dep | Tampering | `slopcheck scan` (run in F1 — done above; integrate into CI in F5) |
| SQL injection | Tampering | SQLAlchemy 2.0 parameterized queries always; never f-string SQL |
| Prompt injection | Tampering | 13-layer pipeline (F3+); F1 only provides placeholders |
| PII in logs | Info disclosure | structlog `redact_pii` processor (F1) |
| PII in traces | Info disclosure | Sentry `send_default_pii=False` + `before_send`; LangSmith client-side masking (F1 stubs, F5 hardens) |
| Open Postgres port | Info disclosure | Use Railway **private** networking; never expose Postgres on a public domain |
| Excess CORS | Various | F1 sets `allow_origins=[<chatwoot-domain>]` only — no wildcard |

### F1-Specific Security Deliverables

- [ ] `.gitignore` covers `.env`, `.env.*`, `*.pem`, `*.key`, `venv/`, `__pycache__/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`
- [ ] `.env.example` documents all required + optional env vars; **no real values**
- [ ] `app/config/settings.py` declares all vars via pydantic-settings with proper `SecretStr` for tokens/keys
- [ ] Sentry init with `send_default_pii=False` + scrubber
- [ ] structlog with PII redaction processor
- [ ] All Postgres/Redis connections via `railway.internal` private domain (never public)
- [ ] Slopcheck CI gate (deferred to F5 but allowlist file committed in F1)
- [ ] Health endpoint does **not** echo connection strings or env values

## Sources

### Primary (HIGH confidence — used to assert facts)
- LangGraph PostgresSaver / AsyncPostgresSaver — https://reference.langchain.com/python/langgraph.checkpoint.postgres/
- LangGraph add-memory guide — https://docs.langchain.com/oss/python/langgraph/add-memory
- FastAPI Lifespan — https://fastapi.tiangolo.com/advanced/events/
- Sentry FastAPI integration — https://docs.sentry.io/platforms/python/integrations/fastapi/
- Sentry Python integrations changelog (auto-detection) — https://docs.sentry.io/platforms/python/integrations/asgi/
- OpenRouter quickstart + OpenAI SDK integration — https://openrouter.ai/docs/guides/community/openai-sdk
- OpenRouter API reference (HTTP-Referer, X-Title, models fallback) — https://openrouter.ai/docs/api/reference/overview
- LangSmith billing & PII redaction — https://docs.langchain.com/langsmith/billing and https://docs.langchain.com/langsmith/llm-gateway-redaction
- Chatwoot self-hosted requirements — https://developers.chatwoot.com/self-hosted/deployment/requirements
- Chatwoot Railway template — https://railway.com/deploy/chatwoot-all-in-one-pgvector
- Railway FastAPI guide — https://docs.railway.com/guides/fastapi
- Railway private networking — https://docs.railway.com/networking/private-networking/how-it-works
- Railway SSL troubleshooting — https://docs.railway.com/networking/troubleshooting/ssl
- Railway build methods (Railpack/Nixpacks/Docker) — https://blog.railway.com/p/comparing-deployment-methods-in-railway
- Alembic async template — https://github.com/sqlalchemy/alembic/blob/main/alembic/templates/async/env.py
- SQLAlchemy 2.0 async pooling — https://docs.sqlalchemy.org/en/20/core/pooling.html
- structlog stdlib integration — https://www.structlog.org/en/stable/logging-best-practices.html
- asgi-correlation-id — https://github.com/snok/asgi-correlation-id
- ruff-pre-commit — https://github.com/astral-sh/ruff-pre-commit
- slopcheck — https://github.com/0xToxSec/slopcheck

### Secondary (MEDIUM confidence — guides and write-ups verified against primary)
- "I Built a LangGraph + FastAPI Agent... and Spent Days Fighting Postgres" — Medium write-up cross-referenced with LangGraph docs on the lifespan pattern
- markaicode OpenRouter + LangChain guide — cross-referenced with OpenRouter docs
- Apitally FastAPI logging guide
- nurbak.com 2026 health check guide
- "Your FastAPI Service Is Leaking PII Into Your Logs" — Medium write-up on key-based redaction
- Railway blog on connection pooling

### Tertiary (LOW — used for ecosystem signal only, never as sole basis for a claim)
- Various Medium / DEV.to write-ups on FastAPI vertical slice patterns

## Metadata

**Confidence breakdown:**
- Standard stack & versions: **HIGH** — every package version verified directly against PyPI via `pip index versions` on 2026-06-27, slopcheck passed for all 28 deps.
- Architecture patterns (lifespan, structlog, OpenRouter factory): **HIGH** — patterns verified against official docs (FastAPI, LangChain, OpenRouter, Sentry).
- LangGraph PostgresSaver setup specifics: **HIGH** — official LangChain reference + multiple recent guides agree on the `setup()` call + `dict_row` + autocommit pattern.
- Railway-specific tactics (Railpack vs Dockerfile, custom domain TLS, private networking): **MEDIUM** — Railway docs are the source; Railpack is beta as of March 2026 so recommending Dockerfile reduces beta risk.
- Chatwoot-on-Railway exact resource sizing: **MEDIUM** — official Chatwoot minimum is 4GB; Railway's plan tiers vary; assumed Hobby/Pro.
- LangSmith free-tier volume planning: **MEDIUM** — 5K traces/month is documented but per-message LangSmith trace count depends on graph structure not yet implemented.
- PII redaction completeness: **MEDIUM** — F1 establishes the pattern; comprehensive redaction (Presidio-class) is F5.

**Research date:** 2026-06-27
**Valid until:** 2026-07-27 for fast-moving items (langgraph, langchain, sentry-sdk versions); 2026-09-27 for stable items (asyncpg, SQLAlchemy, FastAPI patterns).
