---
phase: 02-integraci-n-softseguros-whatsapp-cloud-api
plan: 03
subsystem: integrations/softseguros
tags: [integrations, softseguros, read-only, circuit-breaker, retry, cache, ci-guard]
requires:
  - 02-01-SUMMARY.md  # WhatsAppSettings + SoftSegurosSettings + Pydantic models + skeletons
provides:
  - SoftSegurosClient (READ-ONLY, only _get HTTP primitive)
  - async_call[T](breaker, coro_fn) wrapper for pybreaker asyncio compensation
  - get_softseguros_client factory (@lru_cache singleton)
  - softseguros_breaker module-level singleton (fail_max=5 reset_timeout=30)
  - _token_holder + _token_lock (thundering-herd-safe DRF token cache)
  - GET /test/poliza/{poliza_id} operational endpoint
  - tests/test_softseguros_readonly.py CI GUARD (operator special request)
  - tests/test_integrations_softseguros.py unit tests
  - CLAUDE.md Don't rule "No agregar métodos write en SoftSegurosClient"
affects:
  - app/main.py (lifespan step 5 + /test/poliza/{poliza_id})
  - CLAUDE.md (Don't section)
tech-stack:
  added: []  # tenacity + pybreaker already added in 02-01
  patterns:
    - "tenacity OUTER / pybreaker INNER (CircuitBreakerError NOT in retry_if list)"
    - "asyncio.Lock + double-check (thundering-herd-safe token refresh)"
    - "Redis read-through cache w/ bypass-on-down (cache MUST NOT break upstream)"
    - "Late-bind cache backend in lifespan (single owner of singletons)"
key-files:
  created:
    - tests/test_softseguros_readonly.py
    - tests/test_integrations_softseguros.py
  modified:
    - app/integrations/_circuit.py
    - app/integrations/softseguros.py
    - app/main.py
    - CLAUDE.md
decisions:
  - "READ-ONLY enforced via triple layer: architecture (no write methods exist) + CI guard (introspects + fails build) + CLAUDE.md rule (Don't with 4 prerequisites). Operator-mandated, non-negotiable."
  - "_cached_get parameter renamed from `poliza_id` to `cache_id` so get_pagos can pass `poliza_id=...` via **params without keyword collision."
  - "Auth POSTs live in TOP-LEVEL functions (_get_token, _refresh_token_on_401), NOT methods on SoftSegurosClient — CI guard introspects only the class, so auth bootstrap is correctly exempted."
  - "redis is late-bound in lifespan (`app.state.softseguros._redis = app.state.redis`) — single ownership site, no public setter API surface needed."
metrics:
  duration: "~25min"
  completed: "2026-06-28"
  tasks: 3
  files: 6 (2 created + 4 modified)
---

# Phase 02 Plan 02-03: SoftSeguros integration (READ-ONLY) Summary

**One-liner:** READ-ONLY SoftSegurosClient with tenacity-outer/pybreaker-inner stack, DRF-token thundering-herd-safe cache, Redis read-through 60s TTL, GET /test/poliza/{id} endpoint, and triple-layer READ-ONLY enforcement (architecture + CI guard + CLAUDE.md rule).

## Commits

| SHA       | Message                                                                       |
| --------- | ----------------------------------------------------------------------------- |
| `6652472` | feat(02-03): implement SoftSegurosClient READ-ONLY + async_call breaker wrapper |
| `a08ed45` | feat(02-03): wire app.state.softseguros + GET /test/poliza/{id} endpoint      |
| `ca0f133` | test(02-03): CI guard READ-ONLY invariant + softseguros unit tests + CLAUDE.md rule |

(Plan 02-02 also landed `538d3dc` in the parallel wave; its `app/main.py` edits + mine merged cleanly — both sets of imports + lifespan steps + endpoints coexist without conflict.)

## Tasks executed

### Task 1 — async_call wrapper + SoftSegurosClient real (READ-ONLY)
- `app/integrations/_circuit.py`: `async_call[T](breaker, coro_fn, *args, **kwargs)` — fail-fast on `breaker.current_state == 'open'`, await coro, `breaker.state.on_success()` / `on_failure(exc)` under `breaker._lock`. PEP 695 generic syntax (ruff UP047).
- `app/integrations/softseguros.py`: full client. `_get` (only HTTP primitive) decorated with tenacity OUTER (3 retries expo on `httpx.HTTPError`/`TimeoutException`) → `async_call(softseguros_breaker, _do)` INNER. `_do` adds `Authorization: Token <hex>` (DRF format, not Bearer), retries once on 401 with refreshed token. Read-through cache wrapper `_cached_get(cache_id, query_type, path, **params)` keyed `softseguros:{cache_id}:{type}` Redis TTL 60s, bypass-on-down. Module-level `softseguros_breaker = CircuitBreaker(fail_max=5, reset_timeout=30, name='softseguros')`, `_token_holder = {'v': None}` + `_token_lock = asyncio.Lock()` with double-check. Factory `@lru_cache(maxsize=1)` returns client with `redis=None` (late-bound).

### Task 2 — Lifespan wiring + /test/poliza/{poliza_id}
- `app/main.py`: imported `get_softseguros_client`; lifespan step 5 sets `app.state.softseguros = get_softseguros_client()` then `app.state.softseguros._redis = app.state.redis` (late-binding, lifespan single owner). Endpoint `GET /test/poliza/{poliza_id}` returns `{poliza, latency_ms}` for operational smoke (D-10), to be gated/removed in F5.
- Plan 02-02 ran in parallel and added `meta_router` + `app.state.meta` — git merged cleanly (additive, disjoint lines).

### Task 3 — CI guard + unit tests + CLAUDE.md
- `tests/test_softseguros_readonly.py` (3 tests):
  - `test_softseguros_client_has_no_write_methods` — introspects with `inspect.getmembers`, fails build if any method (case-insensitive) contains `post`/`put`/`patch`/`delete`/`create`/`update`/`set_`/`modify_` (except explicit `METHOD_ALLOWLIST`: `_get`, `_cached_get`, `get_poliza`, `get_cliente`, `get_estado`, `get_pagos`).
  - `test_softseguros_client_has_only_one_http_primitive` — asserts `methods & {_get,_post,_put,_patch,_delete,_head,_options} == {'_get'}`.
  - `test_softseguros_module_docstring_declares_readonly` — asserts `'READ-ONLY INVARIANT' in module __doc__`.
- `tests/test_integrations_softseguros.py` (13 tests):
  1. `test_get_softseguros_client_is_singleton`
  2. `test_get_softseguros_client_uses_settings` (base_url + UA header)
  3. `test_get_token_caches_in_holder` (2 calls → 1 post)
  4. `test_get_token_thundering_herd_protection` (10 concurrent gather → 1 post)
  5. `test_get_retries_on_transient_httpx_errors` (2 ConnectError → success on 3rd)
  6. `test_get_gives_up_after_3_attempts` (stop_after_attempt(3))
  7. `test_breaker_open_short_circuits_the_call` (CircuitBreakerError raised; `_http.get` never called)
  8. `test_cache_hit_bypasses_upstream`
  9. `test_cache_miss_writes_back` (key `b"softseguros:123:poliza"`, ex=60)
  10. `test_cache_failure_falls_through_to_upstream` (Redis down → bypass cleanly)
  11. `test_401_triggers_refresh_and_retry_once`
  12-15. `test_public_get_methods_route_to_correct_paths` (parametric × 4: poliza/cliente/estado/pagos)
- `CLAUDE.md` — under `## Reglas críticas → ### Don't` appended the new rule with the 4 prerequisites (ADR + threat model + PROJECT.md scope + operator approval) and reference to the CI guard.

## Verification

- Pre-commit ran ruff (legacy + format), black, mypy, trim-trailing, fix-EOF on every commit and **all 3 commits passed cleanly** (UP047 + mypy keyword-collision were the only issues, both fixed before Task 1 was accepted).
- `git log --oneline -3` shows the three plan commits chained correctly.
- Test execution (`pytest`/`uv run pytest`) was sandbox-denied in this executor session, so I could not produce a live pytest output. The test files compile (mypy via pre-commit would have caught syntax issues at type level) and follow the plan literally. Plan 02-04 (smoke) will exercise the live path against DPG's SoftSeguros.

### Confirmation of the invariants

- READ-ONLY: `app/integrations/softseguros.py` defines exactly the 4 public read methods (`get_poliza`, `get_cliente`, `get_estado`, `get_pagos`) + `_get` + `_cached_get`. Zero write verbs in `SoftSegurosClient`. The `_get_token` + `_refresh_token_on_401` POSTs live at module top level (auth bootstrap, not data writes) and are documented as exempt in the module docstring's READ-ONLY INVARIANT block.
- Tenacity OUTER / pybreaker INNER: `@retry(retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4), reraise=True)` decorates `_get`. `pybreaker.CircuitBreakerError` is NOT in the retry_if list — open breaker bubbles past tenacity instantly. The test `test_breaker_open_short_circuits_the_call` asserts `_http.get` is never called when the breaker is open.
- Thundering-herd: `_get_token` double-checks `_token_holder["v"]` inside `async with _token_lock:`. The test `test_get_token_thundering_herd_protection` launches 10 concurrent `asyncio.gather(_get_token, …)` and asserts exactly 1 upstream POST.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocker] PEP 695 generic syntax for ruff UP047**
- Found during: Task 1 pre-commit (first attempt)
- Issue: Ruff UP047 rejected `TypeVar('T')` + `async def async_call(...) -> T`.
- Fix: Switched to PEP 695: `async def async_call[T](...) -> T` (matches the original skeleton in 02-01).
- Files: `app/integrations/_circuit.py`
- Commit: `6652472`

**2. [Rule 1 - Bug] mypy keyword-arg collision in `_cached_get`**
- Found during: Task 1 pre-commit (first attempt)
- Issue: `get_pagos(poliza_id)` called `_cached_get(poliza_id, "pagos", "/api/pagopoliza/", poliza_id=poliza_id)` — mypy flagged "multiple values for keyword argument 'poliza_id'" because the first positional param was *also* named `poliza_id` and would shadow when `**params` merged.
- Fix: Renamed the first param to generic `cache_id`. Docstring updated to explain it's the identifier used in the cache key (poliza_id OR cliente_id) and that the rename avoids collision with HTTP query params passed via `**params`.
- Files: `app/integrations/softseguros.py`
- Commit: `6652472`

### Other notes

- No Rule 4 (architectural) deviations; plan executed as written.
- No auth gates hit.
- Test execution: bash sandbox in this executor session denied `uv run pytest` / `.venv/Scripts/pytest.exe`. I rely on pre-commit (which ran ruff + mypy + black cleanly on every commit) for static verification, and on Plan 02-04's smoke + the CI on push for runtime verification of the test suite.

## Self-Check: PASSED

- [x] `app/integrations/_circuit.py` exists (modified, commit `6652472`)
- [x] `app/integrations/softseguros.py` exists (modified, commit `6652472`)
- [x] `app/main.py` modified (commit `a08ed45`, also touched by `538d3dc` from 02-02 — merged cleanly)
- [x] `tests/test_softseguros_readonly.py` exists (created, commit `ca0f133`)
- [x] `tests/test_integrations_softseguros.py` exists (created, commit `ca0f133`)
- [x] `CLAUDE.md` modified (commit `ca0f133`)
- [x] Commit `6652472` exists in `git log`
- [x] Commit `a08ed45` exists in `git log`
- [x] Commit `ca0f133` exists in `git log`

## Notes for Plan 02-04

- `GET /test/poliza/{poliza_id}` is wired and ready. Operator needs to set `SOFTSEGUROS_USERNAME` + `SOFTSEGUROS_PASSWORD` in Railway environment (Plan 02-04 Task X) before issuing a real `curl https://<railway-host>/test/poliza/12345`.
- First call against a real environment will exercise the full path: `_get_token` POST to `/api-token-auth/` → cache token → `GET /api/poliza/{id}/` with `Authorization: Token <hex>` → JSON → Redis SET `softseguros:{id}:poliza ex=60`.
- The `/api/pagopoliza/?poliza_id=` endpoint is documented in `SOFTSEGUROS_API_NOTES.md` as timing out at 504. The plan's `get_pagos` is kept for completeness; production callers should prefer `poliza.total_pagos_poliza` (embedded) until the upstream timeout is resolved by DPG.

## Notes for future developers

Any attempt to add `_post`, `_put`, `_patch`, `_delete`, `create_*`, `update_*`, `set_*`, or `modify_*` to `SoftSegurosClient` will be blocked by `tests/test_softseguros_readonly.py::test_softseguros_client_has_no_write_methods`. To legitimately add a write:

1. Write an ADR in `.planning/adr/` justifying the write semantics + idempotency story
2. Update threat model in `PROJECT.md §"Seguridad"` covering the new tampering surface
3. Update `PROJECT.md` scope to explicitly allow the write
4. Get operator approval
5. **Only then** update `METHOD_ALLOWLIST` in `tests/test_softseguros_readonly.py` to whitelist the new method name explicitly
