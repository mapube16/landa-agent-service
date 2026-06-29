---
phase: "03"
plan: "02"
subsystem: integrations/softseguros
tags: [softseguros, read-only, cache, circuit-breaker, identification, D-01]
dependency_graph:
  requires: [03-01]
  provides: [get_clientes_by_documento, get_polizas_by_cliente]
  affects: [03-05-node_identify]
tech_stack:
  added: []
  patterns: [two-call pattern listar_cliente + poliza by cliente_id]
key_files:
  created: []
  modified:
    - app/integrations/softseguros.py
    - tests/test_softseguros_readonly.py
    - tests/test_integrations_softseguros.py
decisions:
  - "use_listar_endpoint_with_secondary_poliza_call (per 03-00-PROBE.md) — single-call fallback rejected (returns all 52898 polizas)"
  - "cache_id prefix doc:{numero_documento} to avoid collision with cliente/{id} keys in same namespace"
  - "get_polizas_by_cliente returns list[PolizaRaw] (unwrapped results[]) not the raw paginated dict"
metrics:
  duration: "15m"
  completed: "2026-06-29"
  tasks_completed: 1
  files_modified: 3
---

# Phase 03 Plan 02: SoftSeguros get_clientes_by_documento Summary

Two READ-ONLY methods added to `SoftSegurosClient` implementing the D-01 two-call identification pattern confirmed by 03-00-PROBE.md: `get_clientes_by_documento` (listar endpoint, returns `ClienteRaw`) + `get_polizas_by_cliente` (secondary poliza call, returns `list[PolizaRaw]`), with CI guard updated in the same commit.

## Method Details

| Method | Path | Query Param | Return | Cache Key |
|--------|------|-------------|--------|-----------|
| `get_clientes_by_documento(numero_documento)` | `/api/cliente/listar_cliente_por_documento/` | `numero_documento` | `ClienteRaw` | `softseguros:doc:{doc}:cliente` |
| `get_polizas_by_cliente(cliente_id)` | `/api/poliza/` | `cliente`, `limit=20` | `list[PolizaRaw]` | `softseguros:{id}:polizas_by_cliente` |

Both inherit `_cached_get` (60s TTL Redis) + `_get` (tenacity outer / pybreaker inner) invariant.

## Decision Tree

**DECISION = use_listar_endpoint_with_secondary_poliza_call** (from 03-00-PROBE.md):
- `/api/cliente/listar_cliente_por_documento/?numero_documento=<doc>` → returns single Cliente dict with `id` field
- `/api/poliza/?cliente_numero_documento=<doc>` fallback REJECTED — returns full 52 898-poliza universe (filter silently ignored by server)
- Two-call pattern is mandatory: listar_cliente → get_polizas_by_cliente(cliente.id)

## ClienteRaw

`ClienteRaw` TypedDict was already narrowed in Plan 03-01 (20 fields from the 122-field response). No further narrowing needed — shape confirmed by 03-00-PROBE.md. Import added to `softseguros.py`.

## CI Guard

`tests/test_softseguros_readonly.py` `METHOD_ALLOWLIST` updated in the same commit with both new method names. Pre-commit hook (mypy, ruff, black) passed clean.

## Tests

6 new tests added to `tests/test_integrations_softseguros.py`:

- `test_get_clientes_by_documento_cache_miss_calls_upstream` — verifies path + `numero_documento` param
- `test_get_clientes_by_documento_cache_hit_skips_upstream` — stub_http.get.call_count == 0
- `test_get_clientes_by_documento_breaker_open_raises_without_retry` — CircuitBreakerError, no httpx call
- `test_allowlist_includes_new_methods` — both names in METHOD_ALLOWLIST
- `test_get_polizas_by_cliente_returns_results_list` — unwraps paginated `results[]`, verifies path + params

Total suite: **111 passed**, mypy --strict clean, all pre-commit hooks green.

## Deviations from Plan

None — plan executed exactly as written. The probe confirmed `use_listar_endpoint_with_secondary_poliza_call`, both methods implemented accordingly. `ClienteRaw` was already narrowed in 03-01, no delta needed.

## Threat Flags

No new security-relevant surface beyond what the plan's threat model covers (T-03-02-01 through T-03-02-04). Both methods are GET-only, go through the existing breaker + token auth, and are scoped to the tenant's DRF credentials.

## Self-Check: PASSED

- `app/integrations/softseguros.py` — modified, contains `get_clientes_by_documento` and `get_polizas_by_cliente`
- `tests/test_softseguros_readonly.py` — METHOD_ALLOWLIST contains both new names
- `tests/test_integrations_softseguros.py` — 6 new tests added
- Commit `546e31c` exists: `git log --oneline -1` → `546e31c feat(03-02): ...`
- 111 tests green, mypy --strict clean
