---
phase: 01-setup-infra
plan: 02
subsystem: infra
tags: [docker, settings, structlog, sentry, pii, observability]
requires:
  - 01-01  # pyproject.toml + uv.lock + repo skeleton
provides:
  - Dockerfile (FastAPI service image)
  - Dockerfile.worker (ARQ worker image)
  - .dockerignore
  - app.config.settings:Settings,settings
  - app.config.logging:configure_logging,redact_pii,PII_KEYS,PHONE_RE
  - app.config.observability:init_sentry,scrub_sentry_event
  - app.worker:WorkerSettings (stub)
affects:
  - .pre-commit-config.yaml (mypy additional_dependencies)
tech-stack:
  added:
    - "Annotated[list[str], NoDecode] pattern for CSV env vars"
    - "structlog.stdlib.ProcessorFormatter bridge"
  patterns:
    - "Multi-stage Dockerfile with uv 0.4.30 + BuildKit cache mount"
    - "Pydantic Settings: one class per domain + Field(default_factory=...) composition"
    - "structlog processor chain with redact_pii BEFORE renderer (CONTEXT.md D-07)"
    - "Sentry init with auto-detected FastAPI integrations + before_send PII scrubber"
key-files:
  created:
    - Dockerfile
    - Dockerfile.worker
    - .dockerignore
    - app/config/settings.py
    - app/config/logging.py
    - app/config/observability.py
    - app/worker.py
  modified:
    - .pre-commit-config.yaml
decisions:
  - "Annotated[list[str], NoDecode] for LLM_FALLBACKS_* env vars: pydantic-settings default behavior tries json.loads() on list-typed fields, which crashes on plain CSV. NoDecode disables that and lets our @field_validator(mode='before') split on comma."
  - "configure_logging uses ConsoleRenderer only when env='dev' AND sys.stdout.isatty(); JSONRenderer in all other cases (Railway log sink expects JSON)."
  - "scrub_sentry_event uses cast(dict[str, Any], event) instead of `# type: ignore`: keeps mypy --strict happy whether or not sentry_sdk.types stubs are loaded in the type-check environment."
  - "Pre-commit mypy hook needs structlog + sentry-sdk[fastapi] in additional_dependencies (Rule 3 auto-fix added in this plan)."
metrics:
  duration: "~25 minutes wall-clock (single executor, no human waits)"
  completed: "2026-06-27"
  tasks_completed: 3
  files_created: 7
  files_modified: 1
---

# Phase 1 Plan 02: Dockerfiles + central config (Settings, structlog, Sentry) Summary

**One-liner:** Multi-stage `python:3.12-slim` + uv Dockerfiles for FastAPI + ARQ worker, Pydantic Settings with `env_prefix` per domain + `SecretStr`, structlog routed through stdlib with hybrid PII redaction (key-name + phone regex) running before the renderer, and Sentry init with `send_default_pii=False` + `before_send` scrubber that walks request / breadcrumbs / exception frames.

## Tareas ejecutadas

### Tarea 1 — Dockerfiles + .dockerignore + worker stub (commit `22b047d`)

- **`.dockerignore`**: excluye `.git`, `.venv`, `.planning`, `tests`, `__pycache__`, `*.md` (excepto README), `.env*` (excepto `.env.example`), Dockerfiles, caches de mypy/pytest/ruff.
- **`Dockerfile`**: builder (`python:3.12-slim` + `uv==0.4.30`, BuildKit cache mount sobre `/root/.cache/uv`, `uv sync --frozen --no-dev --no-install-project`) + runtime (`libpq5` + `ca-certificates` + `curl`, `PATH=/app/.venv/bin`, `PYTHONUNBUFFERED=1`, `EXPOSE 8000`, `HEALTHCHECK` sobre `/health`, `CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2`).
- **`Dockerfile.worker`**: builder idéntico al principal (cache layer compartido por Railway/Docker), runtime mínimo sin `curl`, `CMD ["arq", "app.worker.WorkerSettings"]`.
- **`app/worker.py`**: stub `WorkerSettings.functions = []` — el `CMD` del worker es válido sintácticamente desde F1; los jobs reales aterrizan en F2.

### Tarea 2 — `app/config/settings.py` (commit `3d02ae0`)

Una `BaseSettings` por dominio con `env_prefix` + `extra="ignore"`, composición en `Settings(BaseSettings)` vía `Field(default_factory=...)`, instancia singleton `settings = Settings()` que falla al import si faltan vars requeridas.

| Clase | env_prefix | REQUIRED vars | Defaults notables |
|---|---|---|---|
| `AppSettings` | `APP_` | — | `env=dev`, `public_url=http://localhost:8000`, `version=0.1.0` |
| `PostgresSettings` | `POSTGRES_` | `POSTGRES_URL` (`SecretStr`) | `.async_url` property convierte a `postgresql+asyncpg://...` |
| `RedisSettings` | `REDIS_` | `REDIS_URL` (`SecretStr`) | — |
| `LLMSettings` | `LLM_` | — | `model_conversation=google/gemini-2.0-pro`, `model_judge=google/gemini-2.0-flash`, `model_intent`/`model_summarizer=google/gemini-2.0-flash`; `fallbacks_conversation`/`fallbacks_judge` como `Annotated[list[str], NoDecode]` + `@field_validator(mode="before")` que parsea CSV |
| `OpenRouterSettings` | `OPENROUTER_` | `OPENROUTER_API_KEY` (`SecretStr`) | `base_url=https://openrouter.ai/api/v1` |
| `LangSmithSettings` | `LANGSMITH_` | — | `api_key: SecretStr \| None`, `project=landa-agent-dev`, `tracing=True`, `endpoint=https://api.smith.langchain.com` |
| `SentrySettings` | `SENTRY_` | — | `dsn: SecretStr \| None`, `traces_sample_rate=0.1`, `profiles_sample_rate=0.0` |

**Ajuste técnico (decisión documentada)**: `list[str]` en pydantic-settings 2.14 intenta `json.loads()` sobre el valor crudo, así que `LLM_FALLBACKS_CONVERSATION=anthropic/claude-3.5-sonnet,openai/gpt-4o-mini` revienta antes de llegar al validator. Solución: `Annotated[list[str], NoDecode]` (NoDecode es export público de `pydantic_settings`) que apaga el decoder default y deja que el `field_validator(mode="before")` haga el split por coma.

### Tarea 3 — `app/config/logging.py` + `app/config/observability.py` (commit `c7e554d`)

**`logging.py`**:

- `PII_KEYS` (frozenset, 30+ claves curadas): identificadores de teléfono (`phone`, `wa_token`, `wa_phone_id`, `wa_webhook_secret`, `wa_verify_token`), API keys (`openrouter_api_key`, `langsmith_api_key`, `sentry_dsn`, `chatwoot_api_key`, `softseguros_username`, `softseguros_password`, `lambda_proyect_internal_token`, `api_key`, `secret`, `token`, `password`, `authorization`), PII financiera (`saldo`, `saldo_pendiente`, `monto`, `documento`, `cedula`, `credit_card`, `cvv`, `ssn`).
- `PHONE_RE = re.compile(r"\+?\d[\d\s\-]{7,}\d")` — escrubea `+584141234567`, `+1-555-867-5309`, `5491134567890` en strings libres.
- `redact_pii` processor → wrapper sobre `_redact_dict` que muta in-place el `EventDict` (que es `MutableMapping[str, Any]`, no `dict[str, Any]`); recursivo en sub-dicts y lists-of-dicts para que tool outputs/request bodies queden cubiertos extremo a extremo.
- `configure_logging(log_level, env)`:
  - chain compartido: `merge_contextvars` (correlation_id de asgi-correlation-id) → `add_log_level` → `TimeStamper(fmt=iso, utc=True)` → `redact_pii` → `StackInfoRenderer` → `dict_tracebacks`.
  - renderer: `ConsoleRenderer` si `env=="dev"` y stdout es TTY; `JSONRenderer` en cualquier otro caso (Railway).
  - bridge stdlib via `ProcessorFormatter` con `foreign_pre_chain=shared_processors` para que uvicorn/SQLAlchemy/etc. compartan el chain (un solo stream JSON en stdout).
  - `root.handlers.clear()` antes de instalar el StreamHandler único (evita duplicación de líneas).
  - `uvicorn.access`, `httpx`, `httpcore` bajan a `WARNING` — mitigación T-01-07.

**`observability.py`**:

- `_scrub_value(v)`: recursivo, escrubea strings por `PHONE_RE`, dict-keys contra `PII_KEYS`, listas elemento a elemento.
- `scrub_sentry_event(event, hint)`: walks `request` / `contexts` / `breadcrumbs` / `exception`; segunda pasada agresiva sobre `event["exception"]["values"][i]["stacktrace"]["frames"][j]["vars"]` (Pitfall 7 — Sentry no escrubea frame locals con `send_default_pii=False`). Usa `cast(dict[str, Any], event)` para que mypy `--strict` acepte la firma `Event → Event | None`.
- `init_sentry()`: no-op si `settings.sentry.dsn is None` (tests, CI sin DSN). En caso contrario `sentry_sdk.init(...)` con `send_default_pii=False`, `before_send=scrub_sentry_event`, integraciones `StarletteIntegration + FastApiIntegration + AsyncPGIntegration + RedisIntegration` explícitas (auto-detection funciona sin listarlas pero "explícito > implícito" para código de seguridad).
- NO `SentryAsgiMiddleware` manual — auto-detection en sentry-sdk 2.x lo hace correctamente; manual wrap doble-envuelve y rompe `request.body()`.

## Verificación

| Check | Resultado |
|---|---|
| `uv run ruff check .` | PASSED (19 archivos) |
| `uv run black --check .` | PASSED (19 archivos sin cambios) |
| `uv run mypy --strict app/` | PASSED (16 source files, 0 errores) |
| `Settings()` import con env mínimas | OK — `settings.app.env == "dev"`, `model_conversation == "google/gemini-2.0-pro"`, `type(openrouter.api_key) == SecretStr` |
| `Settings()` fail-fast sin `POSTGRES_URL` | OK — `ValidationError` levantado al import time (1 missing field) |
| `LLM_FALLBACKS_CONVERSATION="a,b"` CSV parsing | OK — `["a", "b"]` |
| `configure_logging("INFO", "dev")` smoke | OK |
| `redact_pii` por key | `{"phone": "+1-555-867-5309"}` → `[REDACTED]` |
| `redact_pii` por regex en string libre | `"user 5491134567890 called"` → `"user [REDACTED_PHONE] called"` |
| `redact_pii` recursivo en dict anidado | `{"request": {"headers": {"wa_token": "EAA...", "X-Other": "+584..."}}}` → `wa_token=[REDACTED]`, `X-Other=[REDACTED_PHONE]` |
| `redact_pii` list-of-dicts en breadcrumbs | `[{"data": {"cedula": "V-..."}}]` → cedula redactada |
| `scrub_sentry_event` headers | `Phone: +584141234567` → `[REDACTED]`, `X-Other: safe` → `safe` |
| `scrub_sentry_event` frame locals | `vars={"phone": "+1...", "safe": "ok"}` → phone=`[REDACTED]`, safe=`ok` |
| `init_sentry()` sin DSN | No-op silencioso (path tests/CI sin SENTRY_DSN) |
| Pre-commit hooks en cada commit | PASSED (ruff, ruff-format, black, mypy, trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-added-large-files, detect-private-key) |

### Verificación NO ejecutada localmente

- **`docker build`** (Dockerfile y Dockerfile.worker, tamaño < 120MB, build cacheado < 15s): **Docker no está instalado en la máquina local del executor**. Los Dockerfiles fueron escritos siguiendo literalmente el patrón documentado en RESEARCH.md (verificado contra docs Railway + uv) y los smoke-tests de Python (que importan los módulos que el contenedor ejecuta) pasan. **Verificación de build queda diferida al primer push a Railway** (plan 01-08 ya wireó GitHub Actions; Railway hará pull del Dockerfile en el deploy de F1). Si el build falla en Railway, se trata como deviación de F1 y se ajusta en hotfix.

## Decisiones registradas

1. **`Annotated[list[str], NoDecode]` para fallbacks LLM**: documentada en el código y arriba. Alternativa rechazada: parsear como `str` y exponer `.fallbacks_conversation_list` property — añade indirección sin pagarse.
2. **`AsyncPGIntegration` + `RedisIntegration` listadas explícitamente en `sentry_sdk.init`**: auto-detection sentry 2.x funciona, pero para código de seguridad "explícito > implícito" — alguien leyendo `observability.py` ve la lista completa de integraciones que pueden inyectar datos al evento.
3. **`init_sentry()` no-op si DSN ausente**: en lugar de fallar duro. Razón: el módulo se importa en tests y CI donde SENTRY_DSN está vacío; bloquear el import allí mata el pipeline. La rule "fail-fast" la guardamos para `POSTGRES_URL`/`REDIS_URL`/`OPENROUTER_API_KEY` que sí son indispensables para arrancar.
4. **`cast(dict[str, Any], event)` en `scrub_sentry_event`**: en vez de `# type: ignore`. Resiste mypy `--strict` con o sin los stubs de `sentry_sdk.types` cargados, lo cual importa para el hook mypy de pre-commit que corre en environment aislado.
5. **Pre-commit `additional_dependencies` extendidas con `structlog==26.1.0` y `sentry-sdk[fastapi]==2.63.0`**: descubierto al primer intento de commit de Task 3 (mypy hook falló porque su env aislado no tenía esas libs). Rule 3 auto-fix aplicada en el mismo commit.

## Desviaciones del plan

1. **[Rule 3 — Auto-fix blocking issue] `Annotated[list[str], NoDecode]` para fallbacks LLM**
   - **Encontrado en:** Tarea 2 (smoke test de CSV parsing).
   - **Problema:** El plan especificaba `list[str] = []` + `field_validator`, pero pydantic-settings 2.14 intenta `json.loads()` sobre cualquier campo list-typed antes de invocar los validators, lo que crashea con un valor CSV como `"a,b,c"`.
   - **Fix:** `Annotated[list[str], NoDecode]` (export público de `pydantic_settings`) en los dos campos `fallbacks_*`. Comentario en el código explica por qué.
   - **Commit:** `3d02ae0`.

2. **[Rule 3 — Auto-fix blocking issue] Pre-commit mypy hook necesita `structlog` y `sentry-sdk[fastapi]`**
   - **Encontrado en:** Tarea 3 (primer intento de commit).
   - **Problema:** El hook `mypy` corre en un virtualenv aislado y solo trae las libs listadas en `additional_dependencies`. Con `--strict`, las importaciones de structlog y sentry-sdk fallaban con `import-not-found`.
   - **Fix:** Añadidos `structlog==26.1.0` y `sentry-sdk[fastapi]==2.63.0` a `.pre-commit-config.yaml`.
   - **Commit:** `c7e554d`.

3. **[Limitación de entorno — diferido a CI] `docker build` no ejecutado localmente**
   - **Encontrado en:** Tarea 1 (verificación).
   - **Problema:** Docker no está instalado en la máquina del executor (`docker: command not found`).
   - **Mitigación:** Los Dockerfiles siguen literalmente RESEARCH.md (verificado contra docs Railway + uv). Smoke tests de Python (que importan los módulos que el contenedor termina ejecutando: `app.worker`, `app.config.*`) pasan.
   - **Acción de seguimiento:** Verificación de `docker build` ocurre en el primer push a Railway en plan 01-08 (CI ya configurada) o cuando se ejecute la build remota. Si falla allí, se trata como hotfix dentro de F1.

## Notas para plan 01-04 (main.py wiring)

El plan 04 (FastAPI app + `/health` + lifespan) debe seguir este orden estricto en `app/main.py`:

```python
# 1. Imports mínimos que no toquen el grafo / routers todavía
from app.config.observability import init_sentry
from app.config.logging import configure_logging
from app.config.settings import settings

# 2. Configurar observability ANTES de cualquier import de routers
#    (sentry-sdk inspecciona sys.modules en init y la auto-detection de
#    FastApiIntegration/StarletteIntegration depende de que estén cargados)
configure_logging(settings.app.log_level, settings.app.env)
init_sentry()

# 3. Ahora sí: imports de routers y armado del FastAPI app
from fastapi import FastAPI
from asgi_correlation_id import CorrelationIdMiddleware
from app.healthcheck import router as health_router
# ... etc

app = FastAPI(lifespan=lifespan)
app.add_middleware(CorrelationIdMiddleware, header_name="X-Request-ID")
app.include_router(health_router)
```

`CorrelationIdMiddleware` inyecta el `X-Request-ID` en el contextvar de structlog (vía `merge_contextvars` que ya está en el chain), y simultáneamente sentry-sdk lo lee como `transaction_id` — un solo correlation flow sin wiring manual.

## PII keys finales

Confirmadas y listadas en `app/config/logging.py:PII_KEYS`. Para auditoría rápida:

```
phone, phone_number, wa_token, wa_phone_id, wa_webhook_secret, wa_verify_token,
openrouter_api_key, langsmith_api_key, sentry_dsn, chatwoot_api_key,
softseguros_username, softseguros_password, lambda_proyect_internal_token,
api_key, secret, token, password, authorization,
saldo, saldo_pendiente, monto, documento, cedula,
credit_card, cvv, ssn
```

Total: 26 keys (case-insensitive). Phase 5 expande hacia Presidio-class si auditoría lo exige.

## Self-Check: PASSED

- [x] `Dockerfile` existe → encontrado
- [x] `Dockerfile.worker` existe → encontrado
- [x] `.dockerignore` existe → encontrado
- [x] `app/config/settings.py` existe → encontrado
- [x] `app/config/logging.py` existe → encontrado
- [x] `app/config/observability.py` existe → encontrado
- [x] `app/worker.py` existe → encontrado
- [x] `.pre-commit-config.yaml` modificado → encontrado
- [x] Commit `22b047d` (Tarea 1) presente en `git log` → encontrado
- [x] Commit `3d02ae0` (Tarea 2) presente en `git log` → encontrado
- [x] Commit `c7e554d` (Tarea 3) presente en `git log` → encontrado
