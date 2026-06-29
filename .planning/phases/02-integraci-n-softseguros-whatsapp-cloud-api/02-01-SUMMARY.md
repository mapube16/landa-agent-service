---
phase: 02-integraci-n-softseguros-whatsapp-cloud-api
plan: 01
subsystem: settings + models + integration scaffolding
tags: [settings, pydantic, skeletons, pre-commit, interface-first]
requires: [01-04, 01-05]
provides:
  - WhatsAppSettings (env_prefix=WA_)
  - SoftSegurosSettings (env_prefix=SOFTSEGUROS_)
  - LangSmithSettings.workspace_id (optional)
  - app.models.meta (InboundEnvelope / OutboundText / MetaError)
  - app.models.softseguros.PolizaRaw (passthrough alias)
  - app.integrations._circuit.async_call (skeleton)
  - app.integrations.meta_cloud.{MetaCloudClient,get_meta_client,META_API_VERSION,META_BASE_URL}
  - app.integrations.softseguros.{SoftSegurosClient,get_softseguros_client} (READ-ONLY)
  - app.webhooks.meta.router (skeleton)
  - app.features.handoff.echo.{is_echo_allowed,format_echo,format_media_echo,_normalize_e164}
affects: [.pre-commit-config.yaml (mypy additional_dependencies), tests/conftest.py (_test_env)]
tech-stack:
  added: []
  patterns: [SecretStr, env_prefix, @lru_cache(maxsize=1) singleton, NoDecode + _split_csv CSV env-var]
key-files:
  created:
    - app/models/meta.py
    - app/models/softseguros.py
    - app/integrations/_circuit.py
    - app/integrations/meta_cloud.py
    - app/integrations/softseguros.py
    - app/webhooks/meta.py
    - app/features/handoff/echo.py
  modified:
    - app/config/settings.py
    - tests/conftest.py
    - .pre-commit-config.yaml
decisions:
  - "PolizaRaw kept as dict[str,Any] passthrough — F3 narrows when real DPG responses captured"
  - "SoftSegurosClient skeleton declares ONLY _get HTTP primitive — _post/_put/_patch/_delete deliberately absent (READ-ONLY invariant baseline for CI guard in Plan 02-03)"
  - "Pre-commit mypy additional_dependencies for tenacity + pybreaker landed in SAME commit as the skeleton imports — avoids fix-up commit dance (lesson from plans 01-02, 01-04)"
metrics:
  duration: 18m
  completed: 2026-06-28
---

# Phase 2 Plan 01: Settings + Pydantic models + 5 module skeletons + pre-commit deps + conftest env-vars Summary

One-liner: Ground-truth interface contracts (settings + Pydantic models + skeleton modules with final signatures) so plans 02-02 (webhooks + echo) and 02-03 (SoftSeguros client) implement against fixed shapes, with pre-commit mypy deps pre-added to avoid the first-commit-fail dance.

## What Shipped

### `app/config/settings.py` (extended)

- **New class `WhatsAppSettings(env_prefix="WA_")`** — fields: `token: SecretStr` (REQUIRED), `phone_id: str` (REQUIRED), `business_account_id: str | None`, `webhook_secret: SecretStr` (REQUIRED), `verify_token: SecretStr` (REQUIRED), `echo_allowlist: Annotated[list[str], NoDecode]` parsed via `_split_csv` validator cloned verbatim from `LLMSettings._split_csv`.
- **New class `SoftSegurosSettings(env_prefix="SOFTSEGUROS_")`** — fields: `base_url: str = "https://app.softseguros.com/"`, `username: SecretStr`, `password: SecretStr`.
- **`LangSmithSettings`** — added `workspace_id: SecretStr | None = None` (Phase 1 follow-up; non-breaking).
- **Root `Settings`** — registered both new classes via `Field(default_factory=...)` immediately after `sentry`.
- **`__all__`** — added `"SoftSegurosSettings"` and `"WhatsAppSettings"` (alphabetical).

### `tests/conftest.py` (extended)

`_test_env` autouse fixture extended with placeholder env vars:

| Var | Value |
|---|---|
| `WA_TOKEN` | `wa-test-token` |
| `WA_PHONE_ID` | `1267241483129092` |
| `WA_BUSINESS_ACCOUNT_ID` | `1451322196454283` |
| `WA_WEBHOOK_SECRET` | `test-webhook-secret-do-not-use-in-prod` |
| `WA_VERIFY_TOKEN` | `test-verify-token-do-not-use-in-prod` |
| `WA_ECHO_ALLOWLIST` | `+15555550100,+15555550101` |
| `SOFTSEGUROS_BASE_URL` | `https://app.softseguros.com/` |
| `SOFTSEGUROS_USERNAME` | `test-user` |
| `SOFTSEGUROS_PASSWORD` | `test-pass` |
| `LANGSMITH_WORKSPACE_ID` | (unset — default `None` is valid) |

### `.pre-commit-config.yaml` (extended)

Added to `mypy` hook's `additional_dependencies`:

```yaml
          # Plan 02-01 introduces tenacity + pybreaker imports in
          # app/integrations/softseguros.py and app/integrations/_circuit.py.
          # mypy isolated env needs them for --strict (pattern from plans 01-02,
          # 01-04, 01-05). Versions match pyproject.toml.
          - tenacity==9.1.4
          - pybreaker==1.4.1
```

### Skeletons created (5 modules)

| File | Lines | Public surface |
|---|---|---|
| `app/integrations/_circuit.py` | ~37 | `async_call[T](breaker, coro_fn, *args, **kwargs) -> T` |
| `app/integrations/meta_cloud.py` | ~57 | `META_API_VERSION` / `META_BASE_URL` / `MetaCloudClient` / `get_meta_client()` |
| `app/integrations/softseguros.py` | ~100 | `SoftSegurosClient` (READ-ONLY: `_get`, `_get_token`, `_refresh_token_on_401`, `get_poliza`, `get_cliente`, `get_estado`, `get_pagos`) + `get_softseguros_client()` |
| `app/webhooks/meta.py` | ~45 | `router: APIRouter(prefix="/webhooks", tags=["meta"])` + `verify` (GET) / `receive` (POST) stubs |
| `app/features/handoff/echo.py` | ~40 | `_normalize_e164`, `is_echo_allowed`, `format_echo`, `format_media_echo` |

Every public function body is `raise NotImplementedError("Implemented in Plan 02-0X")`. Each module references `settings` to keep mypy `--strict` honest about the dependency wire even before bodies land.

### Pydantic models created (2 modules)

- **`app/models/meta.py`** — `InboundEnvelope` → `Entry` → `Change` → `ChangeValue` (with `messages: list[InboundMessage] | None` and `statuses: list[dict[str, Any]] | None`) → `InboundMessage` (`from_: str = Field(alias="from")`, `id`, `timestamp`, `type` Literal whitelist + `"unknown"` fallback, optional `text: MessageText`); `OutboundText` (+ `OutboundTextBody`); `MetaError` (+ `MetaErrorDetail`). All models set `model_config = ConfigDict(extra="ignore")` so Meta can add fields safely.
- **`app/models/softseguros.py`** — `PolizaRaw = dict[str, Any]` passthrough alias (documented; F3 narrows when real DPG response data captured).

## Verification

- `uv run pytest -q` — 12 passed (existing Phase 1 tests still green)
- `.venv/Scripts/mypy.exe --strict app/` — `Success: no issues found in 30 source files`
- `pre-commit` runs green on the commits (ruff, ruff-format, black, mypy, all `pre-commit-hooks`)
- READ-ONLY guard (AST introspection over `SoftSegurosClient`): no method name contains `post / put / patch / delete / create / update / set_ / modify_` — baseline that Plan 02-03's CI guard formalises
- Settings smoke: `settings.whatsapp.echo_allowlist == ["+15555550100", "+15555550101"]`, `settings.softseguros.base_url == "https://app.softseguros.com/"`, `settings.langsmith.workspace_id is None` — all confirmed
- Meta model parse smoke: `InboundEnvelope.model_validate_json(...)` on a canonical Meta payload returns `entry[0].changes[0].value.messages[0].from_ == "16505551234"`, `.text.body == "hola"` — alias `from` → `from_` works

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Ruff `UP047` on `async_call` generic function signature**

- **Found during:** Task 3 pre-commit run on `app/integrations/_circuit.py`
- **Issue:** Ruff config requires PEP 695 type-parameter syntax (Python 3.12+) for generic functions; the planned `from typing import TypeVar; T = TypeVar("T"); async def async_call(...) -> T` form triggered `UP047`
- **Fix:** Switched to PEP 695: `async def async_call[T](breaker, coro_fn, *args, **kwargs) -> T`. Dropped the unused `TypeVar` import. Same runtime semantics, project-conformant style.
- **Files modified:** `app/integrations/_circuit.py`
- **Commit:** `871f7d0` (in-task auto-fix, no separate commit)

## Commits

| Hash | Task | Message |
|---|---|---|
| `35f8dd1` | 1 | `feat(02-01): add WhatsAppSettings + SoftSegurosSettings + LangSmith workspace_id` |
| `2f8076f` | 2 | `feat(02-01): add Pydantic v2 models for Meta + SoftSeguros` |
| `871f7d0` | 3 | `feat(02-01): add 5 module skeletons + pre-commit tenacity/pybreaker deps` |

## Contract Notes for Plans 02-02 and 02-03

Plans 02-02 and 02-03 must respect these signatures. Changing them breaks the dependency contract this plan establishes.

**Plan 02-02 (webhook + echo + Meta sender) must implement:**

```python
# app/integrations/meta_cloud.py
async def send_text(self, to: str, body: str) -> str: ...
async def send_media_ack(self, to: str, media_type: str) -> str: ...
def get_meta_client() -> MetaCloudClient: ...

# app/webhooks/meta.py — endpoints fixed (DO NOT change signatures or path)
@router.get("/meta")
async def verify(request: Request) -> Response: ...
@router.post("/meta")
async def receive(request: Request) -> Response: ...

# app/features/handoff/echo.py — pure functions, no I/O
def _normalize_e164(raw: str) -> str: ...
def is_echo_allowed(phone: str) -> bool: ...
def format_echo(text: str) -> str: ...
def format_media_echo(media_type: str) -> str: ...
```

**Plan 02-03 (SoftSeguros + circuit breaker) must implement:**

```python
# app/integrations/_circuit.py
async def async_call[T](
    breaker: pybreaker.CircuitBreaker,
    coro_fn: Callable[..., Awaitable[T]],
    *args: Any,
    **kwargs: Any,
) -> T: ...

# app/integrations/softseguros.py — READ-ONLY invariant locked
async def _get(self, path: str, **params: Any) -> dict[str, Any]: ...
async def _get_token(self) -> str: ...
async def _refresh_token_on_401(self) -> str: ...
async def get_poliza(self, poliza_id: str) -> PolizaRaw: ...
async def get_cliente(self, cliente_id: str) -> PolizaRaw: ...
async def get_estado(self, poliza_id: str) -> PolizaRaw: ...
async def get_pagos(self, poliza_id: str) -> PolizaRaw: ...
def get_softseguros_client() -> SoftSegurosClient: ...
```

**Hard constants (DO NOT mutate):**

- `META_API_VERSION: Final[str] = "v21.0"` (D-08)
- `META_BASE_URL: Final[str] = f"https://graph.facebook.com/{META_API_VERSION}"`

## Self-Check: PASSED

- `app/config/settings.py` — exists, contains `WhatsAppSettings` + `SoftSegurosSettings` + `workspace_id`
- `tests/conftest.py` — extended with 9 new env-var placeholders
- `.pre-commit-config.yaml` — `tenacity==9.1.4` + `pybreaker==1.4.1` present
- `app/models/meta.py` — exports `InboundEnvelope`, `OutboundText`, `MetaError` (+ 7 supporting)
- `app/models/softseguros.py` — exports `PolizaRaw`
- `app/integrations/_circuit.py` — exists with `async_call[T]` skeleton
- `app/integrations/meta_cloud.py` — exists with `META_API_VERSION`/`META_BASE_URL`/`MetaCloudClient`/`get_meta_client`
- `app/integrations/softseguros.py` — exists, READ-ONLY (zero write-verb method names)
- `app/webhooks/meta.py` — exists with `router` + `verify` + `receive`
- `app/features/handoff/echo.py` — exists with 4 functions
- Commits `35f8dd1`, `2f8076f`, `871f7d0` present in `git log`
- `pytest -q` 12 passed, `mypy --strict app/` clean
