---
phase: 05-seguridad-y-audit-log
plan: "03"
subsystem: security/rate_limiter
tags: [rate-limiting, redis, lua, sliding-window, sec-06, tdd]
dependency_graph:
  requires:
    - "05-01 (RateLimitSettings in settings.py)"
    - "app/config/redis.py (binary-safe client)"
  provides:
    - "check_rate_limit(redis, *, phone, poliza_id) -> RateLimitResult"
    - "RateLimitResult NamedTuple (allowed, scope)"
    - "T_RATE_LIMITED: str — client-facing Spanish rate-limit message"
    - "_SLIDING_WINDOW_LUA — Lua sorted-set script for Plan 05-06 reference"
  affects:
    - "Plan 05-06 (webhook wiring — imports check_rate_limit, T_RATE_LIMITED)"
tech_stack:
  added: []
  patterns:
    - "Redis sorted-set sliding window via Lua (ZREMRANGEBYSCORE + ZCARD + ZADD)"
    - "SHA-256[:16] key derivation for key-poisoning guard"
    - "Fail-open on Redis exception (limiter is a shield, not a gate)"
    - "structlog.warning for rate_limit.exceeded and rate_limit.approaching"
key_files:
  created:
    - app/security/rate_limiter.py
    - app/security/tests/test_rate_limiter.py
  modified: []
decisions:
  - "Lua script returns -1 on block (not 0) to distinguish 'blocked' from 'count=0', enabling clean int comparison without ambiguity"
  - "Alert threshold: math.ceil(limit * 0.8) — matches plan spec; no direct sentry_sdk call, structlog integration picks up warnings"
  - "Log mock patching (patch 'app.security.rate_limiter.log') used for warning-event tests instead of caplog — structlog does not route through stdlib logging in default test config"
metrics:
  duration_minutes: 22
  completed_date: "2026-07-04"
  tasks_completed: 2
  files_created: 2
  files_modified: 0
  tests_added: 13
  tests_baseline: 350
  tests_final: 382
---

# Phase 05 Plan 03: Rate Limiter Core Summary

**One-liner:** Multi-level Redis sorted-set sliding-window rate limiter with Lua atomicity, SHA-256 key hashing, and fail-open semantics at three concentric scopes (phone/poliza/global).

---

## Tasks Completed

| # | Task | Commit | Status |
|---|------|--------|--------|
| 1 (RED) | Failing tests for rate limiter | b6cbb2f | DONE |
| 2 (GREEN) | Sliding-window Lua limiter module + updated tests | 397fb65 | DONE |

---

## What Was Built

### app/security/rate_limiter.py

- `_SLIDING_WINDOW_LUA` — Lua script: ZREMRANGEBYSCORE + ZCARD + ZADD. Returns -1 when rate-limited, returns `count+1` when admitted (enables 80% approaching check in one round-trip).
- `RateLimitResult(NamedTuple)` — `allowed: bool`, `scope: str | None`.
- `T_RATE_LIMITED: str` — "Estas enviando muchos mensajes. Por favor espera un momento e intenta de nuevo."
- `_key(scope, raw)` — `f"rl:{scope}:{sha256(raw.encode()).hexdigest()[:16]}"` (Pitfall 2 guard).
- `check_rate_limit(redis, *, phone, poliza_id=None)` — evaluates phone -> poliza (optional) -> global; first failure short-circuits; Redis exceptions are caught + logged (fail-open).

### app/security/tests/test_rate_limiter.py

13 unit tests (all non-integration):

| Test | Behavior verified |
|------|-------------------|
| test_phone_only_two_evals | Phone-only path issues exactly 2 evals |
| test_poliza_id_three_evals_in_order | Poliza path issues 3 evals in phone/poliza/global order |
| test_keys_do_not_contain_raw_phone | Raw phone never appears in Redis key |
| test_phone_key_format | Key matches `rl:phone:[0-9a-f]{16}` |
| test_phone_blocked_short_circuits | -1 on phone level: no further evals |
| test_global_blocked_returns_global_scope | -1 on global: (False, "global") |
| test_connection_error_fail_open | ConnectionError on every eval -> (True, None) |
| test_disabled_returns_allowed_with_zero_evals | enabled=False: zero evals, (True, None) |
| test_approaching_threshold_allowed_but_warning | count >= 80% limit: allowed=True + warning logged |
| test_exceeded_logs_warning | -1 result: rate_limit.exceeded warning logged |
| test_t_rate_limited_is_string | T_RATE_LIMITED is non-empty string |
| test_lua_script_uses_sorted_set | Lua contains ZREMRANGEBYSCORE + ZCARD + ZADD |
| test_exports | __all__ has check_rate_limit, RateLimitResult, T_RATE_LIMITED |

Integration test (marked `pytest.mark.integration`, skipped without REDIS_URL): verifies phone_limit+1 calls with same phone blocks on the last call with scope="phone".

---

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test warning-log assertions used caplog which structlog bypasses**
- **Found during:** Task 2 (GREEN phase) — 2 tests failing after implementation
- **Issue:** `test_approaching_threshold_allowed_but_warning` and `test_exceeded_logs_warning` used pytest's `caplog` fixture, which captures stdlib logging. structlog in this project's test configuration renders to stdout rather than routing through stdlib logging, so `caplog.records` was always empty even though warnings were emitted.
- **Fix:** Replaced `caplog` with `unittest.mock.MagicMock` patching `app.security.rate_limiter.log` directly; `mock_log.warning.side_effect` appends `(event, kwargs)` to a list for assertion.
- **Files modified:** `app/security/tests/test_rate_limiter.py`
- **Commit:** 397fb65

---

## Verification

- `uv run pytest app/security/tests/test_rate_limiter.py -q -m "not integration"` — 13 passed
- `uv run ruff check app/security/rate_limiter.py app/security/tests/test_rate_limiter.py` — clean
- `uv run black --check app/security/rate_limiter.py app/security/tests/test_rate_limiter.py` — clean
- `uv run mypy --strict app/security/rate_limiter.py` — Success: no issues
- Full suite: 382 passed, 11 deselected (integration), 0 failures

---

## Self-Check

### Files exist
- `app/security/rate_limiter.py` — FOUND
- `app/security/tests/test_rate_limiter.py` — FOUND
- `.planning/phases/05-seguridad-y-audit-log/05-03-SUMMARY.md` — FOUND (this file)

### Commits exist
- b6cbb2f — `test(05-03): add failing tests for rate limiter sliding window` — RED commit
- 397fb65 — `feat(05-03): implement multi-level Redis sliding-window rate limiter` — GREEN commit

## Self-Check: PASSED
