# Phase 2: Integración SoftSeguros + WhatsApp Cloud API — Pattern Map

**Mapped:** 2026-06-28
**Files analyzed:** 10 (4 new modules, 2 extensions, 4 new tests)
**Analogs found:** 6 strong / 2 partial / 2 no-analog (first webhook + first feature)

## File Classification & Analog Mapping

| New / Modified file | Role | Data flow | Closest analog | Match quality | Reuse summary |
|---|---|---|---|---|---|
| `app/integrations/softseguros.py` | integration client | request-response (REST) + cache | `app/integrations/openrouter.py` | role-match (LLM vs REST) — strong | `@lru_cache(maxsize=...)` factory, `SecretStr.get_secret_value()` at construction, `default_headers`, `timeout`, single-point-of-instantiation invariant |
| `app/integrations/meta_cloud.py` | integration client | request-response (HTTP POST `/messages`) | `app/integrations/openrouter.py` | role-match — strong | Same factory shape (`get_meta_client()` cached singleton), `META_API_VERSION = "v21.0"` module constant, async httpx, `SecretStr` for `WA_TOKEN` |
| `app/webhooks/meta.py` | webhook handler / router | request-response + idempotency + HMAC | `app/healthcheck.py` (router shape only) | partial — first webhook in repo, NO body-raw analog exists | `APIRouter(prefix="/webhooks", tags=["meta"])`, `structlog.get_logger("webhooks.meta")`, `request.app.state.<client>` access pattern, `# noqa: BLE001` + `type(exc).__name__` on errors |
| `app/features/handoff/echo.py` | feature module (pure function) | request-response (sync transform) | none — first feature module | scaffold-only | Follow CLAUDE.md vertical-slice rule; `from __future__ import annotations`, module docstring, `__all__`, type hints `mypy --strict` clean |
| `app/config/settings.py` (EXTEND) | settings | n/a | itself (existing 7 `*Settings` classes) | exact — self-analog | Append `WhatsAppSettings(env_prefix="WA_")` + `SoftSegurosSettings(env_prefix="SOFTSEGUROS_")`, register on `Settings` root via `Field(default_factory=...)`, add to `__all__` |
| `app/main.py` (EXTEND lifespan + routes) | composition root | startup/shutdown | itself | exact — self-analog | Acquire `app.state.softseguros` and `app.state.meta` in lifespan (singletons — NOT async-resource-heavy, no `__aexit__` needed), `app.include_router(meta_router)`, add `/test/poliza/{poliza_id}` mirroring `/test/llm` |
| `tests/test_integrations_softseguros.py` | unit test | n/a | `tests/test_llm_factory.py` | exact | `def test_factory_returns_X()`, `def test_factory_cached()` (asserts `a is b`), `def test_unknown_X_raises()`. NO `async` mark needed for factory-shape tests |
| `tests/test_integrations_meta_cloud.py` | unit test | n/a | `tests/test_llm_factory.py` | exact | Same factory + cache shape; assert `META_API_VERSION` constant + base_url composition |
| `tests/test_webhooks_meta.py` | integration test (FastAPI client) | request-response | `tests/test_health.py` | exact | `async def test_X(client: AsyncClient)`, autouse `monkeypatch` fixture to stub the integration clients (Meta send + Redis idempotency + SoftSeguros), use existing `client` fixture from `conftest.py` (`ASGITransport`, no lifespan) |
| `tests/test_features_handoff_echo.py` | unit test (pure function) | n/a | `tests/test_llm_factory.py` | role-match | Plain `def test_X():` calls, no fixtures, assert echo string formatting + allowlist `is_echo_allowed()` truth table |

---

## Pattern Assignments (concrete excerpts)

### `app/integrations/softseguros.py` ← `app/integrations/openrouter.py`

**Imports + module-level invariants** (openrouter.py:29-38):
```python
from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

import httpx  # NEW for softseguros — openrouter uses langchain_openai instead

from app.config.settings import settings
```

**Factory + cache shape** (openrouter.py:96-118):
```python
@lru_cache(maxsize=8)
def _get_llm_resolved(role: LLMRole) -> ChatOpenAI:
    """Construct (and cache) a ChatOpenAI instance for the resolved role."""
    model = _model_for(role)
    ...
    kwargs: dict[str, Any] = {
        "model": model,
        "base_url": settings.openrouter.base_url,
        "api_key": settings.openrouter.api_key.get_secret_value(),
        "default_headers": {...},
        "temperature": _temperature_for(role),
        "timeout": 30,
        "max_retries": 2,
    }
    ...
    return ChatOpenAI(**kwargs)


def get_llm(role: str) -> ChatOpenAI:
    """Return the canonical ChatOpenAI instance for role (cached)."""
    resolved = _resolve_role(role)
    return _get_llm_resolved(resolved)
```

→ Translate to `get_softseguros_client() -> SoftSegurosClient` (no `role` param — single client). Use `@lru_cache(maxsize=1)`. Internal `httpx.AsyncClient(base_url=settings.softseguros.base_url, timeout=10.0, headers={"User-Agent": "landa-agent-service/0.1.0"})` constructed once; token refresh logic owned by the client class. Wrap calls with `tenacity.AsyncRetrying` (3 retries exponential backoff over `httpx.HTTPError`/`httpx.TimeoutException`) and `pybreaker.CircuitBreaker(fail_max=5, reset_timeout=30)` per CONTEXT D-11.

**Module-level singleton wisdom doc** (openrouter.py:24-26):
```python
"""NEVER instantiate ChatOpenAI directly elsewhere in the codebase; always go
through this factory so the OpenRouter gateway invariant + LangSmith tracing
remain enforced."""
```

→ Mirror with `"""NEVER instantiate httpx clients to SoftSeguros elsewhere; always go through get_softseguros_client() so cache + circuit breaker + token refresh stay coherent."""`

**Public-surface `__all__`** (openrouter.py:142-146): always export the public callable + any typed Literal/dict.

---

### `app/integrations/meta_cloud.py` ← `app/integrations/openrouter.py`

Same factory shape. Specifics:

**Module constant for pinned API version** (CONTEXT D-08):
```python
META_API_VERSION: Final[str] = "v21.0"
META_BASE_URL: Final[str] = f"https://graph.facebook.com/{META_API_VERSION}"
```

**Per-call URL composition pattern** (no analog in repo yet — derive from `openrouter.base_url` pattern):
```python
url = f"{META_BASE_URL}/{settings.whatsapp.phone_id}/messages"
```

**Headers from `SecretStr`** (mirror openrouter.py:104):
```python
headers = {"Authorization": f"Bearer {settings.whatsapp.token.get_secret_value()}"}
```

→ `get_meta_client() -> MetaCloudClient` cached singleton with `httpx.AsyncClient(timeout=10.0)` and helper methods `async send_text(to: str, body: str)`, `async send_media_ack(to: str, media_type: str)`. Pydantic v2 models for request/response live in `app/models/meta.py` (per CONTEXT "Claude's Discretion" bullet).

---

### `app/webhooks/meta.py` ← `app/healthcheck.py` (router shape) + brand-new HMAC logic

**Router shape + structlog logger** (healthcheck.py:36-37):
```python
router = APIRouter(tags=["meta"])
log = structlog.get_logger("healthcheck")
```

→ Translate to:
```python
router = APIRouter(prefix="/webhooks", tags=["meta"])
log = structlog.get_logger("webhooks.meta")
```

**Reading `app.state` from a handler** (healthcheck.py:59-68):
```python
async def _check_postgres(request: Request) -> None:
    session_factory = request.app.state.session_factory
    async with session_factory() as s:
        await s.execute(text("SELECT 1"))
```

→ Apply the same shape: `meta = request.app.state.meta` and `redis = request.app.state.redis` inside the POST handler.

**Error-surface pattern (do NOT leak internals)** (healthcheck.py:51-56):
```python
except Exception as exc:  # noqa: BLE001
    return {
        "ok": False,
        "error": type(exc).__name__,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
```

→ Apply analogously: on HMAC fail → `HTTPException(status_code=401, detail="invalid signature")` with structured log (NEVER dump raw body or signature). On Meta payload decode error → log `type(exc).__name__` only.

**NEW patterns required (no analog — flag for planner):**
- **Raw body capture before Pydantic parse**: HMAC is computed over the raw bytes. Use `raw = await request.body()` BEFORE any `await request.json()` to avoid the body being consumed twice. No analog in the repo (Sentry init in `observability.py:5-7` even warns about `SentryAsgiMiddleware` double-wrap breaking `request.body()` — planner must verify Sentry's auto-detect FastAPI integration is benign here).
- **HMAC verification helper** (CONTEXT D-16): `hmac.compare_digest(...)` is mandatory; `==` is banned. New helper module-level function:
  ```python
  def _verify_signature(raw_body: bytes, header_signature: str, secret: str) -> bool:
      expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
      return hmac.compare_digest(expected, header_signature)
  ```
- **Idempotency via Redis `SET NX EX`** (CONTEXT D-14): `await redis.set(f"wa:msg:{message_id}", b"1", nx=True, ex=86400)`. Returns `True` on first-write, `None` on duplicate. Note: app.state.redis is binary-safe (`decode_responses=False` per `app/config/redis.py:53`) — pass `bytes` values.
- **GET verification challenge** (CONTEXT D-09): `if request.query_params.get("hub.verify_token") == settings.whatsapp.verify_token.get_secret_value(): return PlainTextResponse(request.query_params["hub.challenge"])`. Pure stdlib comparison is acceptable here (no timing-attack risk — verify_token is public knowledge during webhook setup).

---

### `app/features/handoff/echo.py` ← scaffold per CLAUDE.md vertical-slice (no analog)

**File-header convention** (mirror openrouter.py:1-27 docstring discipline):
- Module docstring explains the "why this is here" (transitional pre-Phase 3, removed when LangGraph entries land)
- `from __future__ import annotations`
- `__all__ = [...]` at the bottom

**Pure-function shape** (no analog exists, derive from CLAUDE.md):
```python
def is_echo_allowed(phone: str) -> bool:
    """Return True iff phone is in the comma-separated WA_ECHO_ALLOWLIST env var."""
    return phone in settings.whatsapp.echo_allowlist  # list[str] after _split_csv validator

def format_echo(text: str) -> str:
    return f"echo: {text}"

def format_media_echo(media_type: str) -> str:
    return f"echo: [{media_type}] received"
```

→ `echo_allowlist` in `WhatsAppSettings` should follow the existing CSV pattern from `LLMSettings.fallbacks_conversation` (`settings.py:102-113`) with `Annotated[list[str], NoDecode]` + `field_validator("echo_allowlist", mode="before") _split_csv` so a single env var `WA_ECHO_ALLOWLIST=+1...,+2...` parses correctly.

---

### `app/config/settings.py` (EXTEND) ← itself

**Per-domain class skeleton** (settings.py:43-66, mirror exactly):
```python
class WhatsAppSettings(BaseSettings):
    """Meta Cloud API credentials + echo allowlist (F2)."""

    model_config = SettingsConfigDict(
        env_prefix="WA_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    token: SecretStr                          # REQUIRED — Meta system user token
    phone_id: str                             # REQUIRED — WhatsApp business phone id
    business_account_id: str | None = None    # informational, not used per-call
    webhook_secret: SecretStr                 # REQUIRED — HMAC X-Hub-Signature-256
    verify_token: SecretStr                   # REQUIRED — GET challenge
    echo_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("echo_allowlist", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        # copy from LLMSettings._split_csv (settings.py:107-113)
        ...


class SoftSegurosSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOFTSEGUROS_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    base_url: str = "https://app.softseguros.com/"
    username: SecretStr           # REQUIRED — credential to /api-token-auth/
    password: SecretStr           # REQUIRED
```

**Registration on root `Settings`** (settings.py:184-190 pattern):
```python
class Settings(BaseSettings):
    ...
    app: AppSettings = Field(default_factory=AppSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    langsmith: LangSmithSettings = Field(default_factory=LangSmithSettings)
    sentry: SentrySettings = Field(default_factory=SentrySettings)
    whatsapp: WhatsAppSettings = Field(default_factory=WhatsAppSettings)        # NEW
    softseguros: SoftSegurosSettings = Field(default_factory=SoftSegurosSettings)  # NEW
```

Add both class names to `__all__` (settings.py:197-207).

---

### `app/main.py` (EXTEND) ← itself

**Lifespan extension pattern** (main.py:60-92): integration clients are NOT async-resource-heavy like the checkpointer. They don't need explicit `__aenter__`/`__aexit__`. The `lru_cache`-backed factory makes them effectively singletons — call them once in lifespan, stash on `app.state`, and let httpx own its connection pool.

```python
# Inside lifespan(), AFTER checkpointer setup, BEFORE yield:
app.state.softseguros = get_softseguros_client()
app.state.meta = get_meta_client()
log.info("lifespan.startup.complete")
```

No teardown required — `httpx.AsyncClient` instances live for the process lifetime and Python's GC + OS socket cleanup is sufficient at shutdown. (If circuit-breaker stats or an explicit `await client.aclose()` becomes valuable in F3+, add it then.)

**Router include** (main.py:146 pattern):
```python
from app.webhooks.meta import router as meta_router  # noqa: E402 (after init_sentry)
...
app.include_router(health_router)
app.include_router(meta_router)
```

**Test endpoint** (main.py:149-164 mirror for `/test/poliza/{poliza_id}`):
```python
@app.post("/test/poliza/{poliza_id}")  # CONTEXT D-10: same "test" pattern, gated/removed in F5
async def test_poliza(poliza_id: str, request: Request) -> dict[str, Any]:
    t0 = time.perf_counter()
    client = request.app.state.softseguros
    poliza = await client.get_poliza(poliza_id)
    return {"poliza": poliza, "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
```

---

### `tests/test_integrations_softseguros.py` ← `tests/test_llm_factory.py`

**Cache identity test** (test_llm_factory.py:16-21):
```python
def test_get_llm_cached_per_role() -> None:
    from app.integrations.openrouter import get_llm

    a = get_llm("conversation")
    b = get_llm("conversation")
    assert a is b
```

→ Translate to `test_get_softseguros_client_is_singleton`.

**Construction-attribute assert** (test_llm_factory.py:8-13):
```python
def test_get_llm_returns_chatopenai_for_conversation() -> None:
    from app.integrations.openrouter import get_llm
    llm = get_llm("conversation")
    assert llm.model_name == "google/gemini-2.5-pro"
    assert "openrouter.ai" in str(llm.openai_api_base)
```

→ Translate to assertions on `client._http.base_url`, `client._http.headers["User-Agent"]`, and confirm token is not memoized in plain text (no `softseguros.username.get_secret_value()` leak).

NOTE: factory tests do NOT need `pytest-asyncio` (`async def`) because instantiation is sync. Async tests are required only when calling `await client.get_poliza(...)` against a stubbed httpx transport.

---

### `tests/test_webhooks_meta.py` ← `tests/test_health.py`

**Stubbing app.state dependencies before request** (test_health.py:15-33):
```python
@pytest.fixture(autouse=True)
def _stub_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import healthcheck

    async def _ok_request(req: object) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(healthcheck, "_check_postgres", _ok_request)
    ...
```

→ Mirror for webhooks: stub `app.state.redis.set` to return `True` (first write) or `None` (duplicate), stub `app.state.meta.send_text` to return a synthetic message_id, stub `app.state.softseguros` to a `MagicMock`. Inject via `monkeypatch.setattr(app, "state", ...)` or via a small fixture that wraps the existing `client` fixture and pre-populates `app.state`.

**FastAPI client + assertion shape** (test_health.py:36-44):
```python
async def test_health_returns_200_and_healthy(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
```

→ Reuse the `client` fixture from `conftest.py` as-is. Tests to cover (planner derives from CONTEXT):
- `GET /webhooks/meta?hub.verify_token=...&hub.challenge=X` returns challenge as plain text
- `GET /webhooks/meta` with wrong verify_token returns 403
- `POST /webhooks/meta` with valid HMAC + new message → 200, echo sent (assert mock called)
- `POST /webhooks/meta` with valid HMAC + duplicate `message_id` → 200, echo NOT sent (assert mock NOT called)
- `POST /webhooks/meta` with bad HMAC → 401
- `POST /webhooks/meta` with non-allowlisted sender → 200, status=`ignored_not_allowlisted`, echo NOT sent

**HMAC fixture pattern** (no analog — derive per CONTEXT "Specifics" + D-16):
```python
@pytest.fixture
def meta_webhook_body_and_sig() -> tuple[bytes, str]:
    body = b'{"object":"whatsapp_business_account","entry":[...]}'
    secret = b"test-secret-set-via-conftest"
    sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return body, sig
```

Add `WA_TOKEN`, `WA_PHONE_ID`, `WA_WEBHOOK_SECRET`, `WA_VERIFY_TOKEN`, `SOFTSEGUROS_USERNAME`, `SOFTSEGUROS_PASSWORD`, `SOFTSEGUROS_BASE_URL` to `conftest.py:_test_env` (line 13-24) as placeholder values so `Settings()` instantiates clean.

---

### `tests/test_features_handoff_echo.py` ← `tests/test_llm_factory.py`

**Pure-function shape** (test_llm_factory.py:39-43 unknown-role pattern is closest analog):
```python
def test_is_echo_allowed_true_when_phone_in_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_ECHO_ALLOWLIST", "+11111,+22222")
    # reload settings or use a fresh WhatsAppSettings()
    ...
    assert is_echo_allowed("+11111") is True
    assert is_echo_allowed("+99999") is False


def test_format_echo_prefixes_string() -> None:
    assert format_echo("hola") == "echo: hola"


def test_format_media_echo_uses_brackets() -> None:
    assert format_media_echo("image") == "echo: [image] received"
```

---

## Cross-cutting Patterns

### structlog logger naming
Convention used in the repo: **dotted module name (no leading `app.`)** — short, matches mental model of the file.
- `app/main.py:48` → `structlog.get_logger("main")`
- `app/healthcheck.py:37` → `structlog.get_logger("healthcheck")`

For Phase 2:
- `app/integrations/softseguros.py` → `structlog.get_logger("integrations.softseguros")`
- `app/integrations/meta_cloud.py` → `structlog.get_logger("integrations.meta_cloud")`
- `app/webhooks/meta.py` → `structlog.get_logger("webhooks.meta")`
- `app/features/handoff/echo.py` → no logger needed (pure functions)

### correlation_id propagation
Already wired in `app/main.py:106-139`:
1. `CorrelationIdMiddleware(header_name="X-Request-ID")` (line 109) reads/generates the ID per request
2. `bind_correlation_to_structlog` middleware (line 112-139) pushes `correlation_id`, `path`, `method` into structlog contextvars and clears them in `finally`

**Phase 2 code does NOTHING.** Every `log.info(...)` call inside the webhook handler, integration client, etc. automatically carries `correlation_id` in the JSON output because of `merge_contextvars` in the processor chain (`app/config/logging.py:121`).

When dispatching to an ARQ background job (F2 hasn't promised any, but if needed): explicitly pass `correlation_id` in the job payload — contextvars don't cross process boundaries.

### SecretStr usage at boundaries
Rule from `app/config/settings.py:5-7` docstring: *"All credentials are wrapped in SecretStr (never plain str) so accidental repr/log dumps render `**********`."*

Established call-sites:
- `app/integrations/openrouter.py:104` — `api_key=settings.openrouter.api_key.get_secret_value()` (at httpx construction, never stored as plain str)
- `app/config/checkpointer.py:58` — `AsyncPostgresSaver.from_conn_string(settings.postgres.url.get_secret_value())` (at one-shot factory call)
- `app/config/observability.py:100` — `dsn=settings.sentry.dsn.get_secret_value()` (at sentry_sdk.init)

For Phase 2:
- `settings.whatsapp.token.get_secret_value()` — ONLY inside the Meta client builder when assembling `Authorization` header
- `settings.whatsapp.webhook_secret.get_secret_value()` — ONLY inside `_verify_signature()` helper
- `settings.whatsapp.verify_token.get_secret_value()` — ONLY inside GET-challenge handler
- `settings.softseguros.username.get_secret_value()` + `.password.get_secret_value()` — ONLY inside the `_refresh_token()` private method of `SoftSegurosClient`

**NEVER** log the result of `.get_secret_value()`. The structlog scrubber in `app/config/logging.py:28-60` has `wa_token`, `wa_webhook_secret`, `wa_verify_token`, `softseguros_username`, `softseguros_password` already in `PII_KEYS` (lines 33-44), but defense in depth: don't pass them to `log.info(...)` as kwargs in the first place.

### env_prefix per domain
Existing 7 prefixes already cover the in-tree Settings classes:
| Class | env_prefix | File line |
|---|---|---|
| `AppSettings` | `APP_` | settings.py:31 |
| `PostgresSettings` | `POSTGRES_` | settings.py:47 |
| `RedisSettings` | `REDIS_` | settings.py:72 |
| `LLMSettings` | `LLM_` | settings.py:85 |
| `OpenRouterSettings` | `OPENROUTER_` | settings.py:120 |
| `LangSmithSettings` | `LANGSMITH_` | settings.py:139 |
| `SentrySettings` | `SENTRY_` | settings.py:155 |

**Phase 2 adds two more (already reserved in `.env.example:37-45`):**
- `WhatsAppSettings` → `WA_` (NOT `WHATSAPP_` — matches `.env.example` + CONTEXT.md captured creds)
- `SoftSegurosSettings` → `SOFTSEGUROS_`

### Pre-commit mypy `additional_dependencies` requirement
Documented pattern in plans 01-02 and 01-04 and reinforced in the SUMMARY (`01-04-SUMMARY.md:127-131`): **every new library imported under `app/` must be pinned in `.pre-commit-config.yaml` under the `mypy` hook's `additional_dependencies`, with the version matching `pyproject.toml`.** Pre-commit's isolated env has no access to the project's `.venv` and `--strict` will fail on missing stubs/runtime.

Phase 2 imports already available (no pre-commit change needed): `httpx`, `pydantic`, `pydantic-settings`, `redis`, `structlog`, `fastapi`, `sentry-sdk`.

Phase 2 imports that require pinning (already in `pyproject.toml:29-30` but NOT yet in `.pre-commit-config.yaml`):
- `tenacity==9.1.4` — used by SoftSeguros client for retry policy
- `pybreaker==1.4.1` — used by SoftSeguros client for circuit breaker

**Auto-fix pattern (per Rule 3 in 01-04-SUMMARY deviations):** the planner should pre-emptively add both to `.pre-commit-config.yaml:17-38` (with version comments referencing CONTEXT D-11) in the same plan that lands `app/integrations/softseguros.py`. Otherwise the first commit-with-pre-commit will fail and require a fix-up commit.

---

## Pitfalls / Anti-patterns to AVOID (NOT to copy)

1. **DO NOT copy the explicit `__aenter__`/`__aexit__` lifespan pattern** from `main.py:76-89` for the Meta or SoftSeguros clients. That pattern exists because `AsyncPostgresSaver.from_conn_string()` returns an async context manager whose body holds a psycopg pool. The httpx-backed integration clients are plain singletons — over-applying that pattern adds boilerplate without value. Keep them as `app.state.X = get_X_client()`.

2. **DO NOT use `==` for HMAC comparison**, even "just for tests". CONTEXT D-16 bans it. Always `hmac.compare_digest(...)`. There is no existing crypto code in the repo to copy — write it cleanly from scratch.

3. **DO NOT read `request.body()` AFTER `request.json()`** inside the webhook handler. Pydantic-based dependency injection would do this implicitly. Read the raw bytes first, verify HMAC, THEN `json.loads(raw)` (or pass to a Pydantic model via `WebhookEnvelope.model_validate_json(raw)`). This is the opposite of every other handler in the codebase — no analog exists; flag it carefully in code comments.

4. **DO NOT mirror the `temperature_for(role)` / `_model_for(role)` role-dispatch pattern** from `openrouter.py:62-93` for the SoftSeguros or Meta client. Those exist because OpenRouter routes the same factory to N model slugs. SoftSeguros and Meta are single-tenant, single-purpose clients — `get_softseguros_client()` takes no arguments. Resist the temptation to add role/tenant parameters until the second tenant lands (CLAUDE.md D-07 + CONTEXT D-01 explicitly defer this).

5. **DO NOT instantiate `httpx.AsyncClient` per-request inside the webhook handler.** Re-use the cached client on `app.state.meta`. Per-request clients defeat connection pooling and add ~100ms of TLS handshake to every outbound message. The `_check_openrouter` probe in `healthcheck.py:71-80` opens a fresh httpx client *only because* health checks should not depend on a long-lived connection that might be unhealthy — that exception does NOT apply to production message-sending paths.

6. **DO NOT pass plain `str` values to `redis.set(...)`** for idempotency keys. `app/config/redis.py:53` sets `decode_responses=False` deliberately. Idempotency value should be bytes (`b"1"`) and key encoded explicitly (`f"wa:msg:{message_id}".encode()`).

7. **DO NOT add a Meta/SoftSeguros probe to `/health`** in this phase even though `app/healthcheck.py` makes it tempting (CONTEXT.md notes it's "barato"). Doing so:
   - couples `/health` to the upstream availability of two new providers (and `/health` is the Railway liveness target)
   - may surface degraded status during normal Meta API maintenance windows, triggering false alerts
   - is explicitly outside the F2 scope wall ("NOT requirement F2" in CONTEXT)
   Defer to F3+ behind an env-var gate if it becomes useful.

8. **DO NOT use `os.getenv(...)` anywhere.** Hard rule from `settings.py:12`: *"NEVER use os.getenv elsewhere in the codebase; import settings here."* All Phase 2 reads go through `settings.whatsapp.*` and `settings.softseguros.*`.

9. **DO NOT log the `from_phone` raw in webhook handler.** The structlog regex scrubber in `app/config/logging.py:67` (`PHONE_RE`) will replace it with `[REDACTED_PHONE]` defensively, but the explicit pattern is: log `phone_hash = hashlib.sha256(phone.encode()).hexdigest()[:8]` for correlation, never the raw phone. CONTEXT "Specifics" section 2 calls this out.

10. **DO NOT generate Pydantic models for the full Meta webhook envelope schema speculatively.** The webhook entry contains many shapes (text, image, audio, sticker, location, button, interactive, contacts, etc.). For F2, model only what the echo handler actually reads: `entry[0].changes[0].value.messages[0]` with `from`, `id`, `type`, and `text.body | image | audio | sticker | location` discriminator. Add more fields when F3 needs them. The repo has no other Pydantic-model-of-third-party-webhook to copy from — keep it minimal.

---

## Metadata

**Analog search scope:** `app/`, `tests/`, `.pre-commit-config.yaml`, `pyproject.toml`, `.env.example`, `.planning/phases/01-setup-infra/`
**Files scanned:** 22 (all of `app/` + all of `tests/` + 4 config files + 01-04-SUMMARY)
**Strong analogs:** `app/integrations/openrouter.py`, `app/healthcheck.py`, `app/config/settings.py`, `app/main.py`, `tests/test_llm_factory.py`, `tests/test_health.py`
**No-analog flags:** webhook raw-body capture, HMAC verification, first feature module (`features/handoff/echo.py`), Meta webhook Pydantic envelope
**Pattern extraction date:** 2026-06-28
