---
phase: 05-seguridad-y-audit-log
plan: "01"
subsystem: security/audit-log
tags: [audit-log, hash-chain, migration, settings, postgresql, pydantic-v2, orjson]
dependency_graph:
  requires: [04-01]
  provides: [emit, emit_task, verify_chain, verify_chain_rows, AuditLog, AuditPayload, AuditSettings, RateLimitSettings]
  affects: [05-02, 05-03, 05-04, 05-05, 05-06]
tech_stack:
  added: []
  patterns:
    - orjson OPT_SORT_KEYS for deterministic canonical JSON (already pinned, no new dep)
    - pg_advisory_xact_lock('audit_log_chain') for v1 chain-insert serialization
    - Pydantic v2 RootModel for flat-primitive payload validation
    - SQLAlchemy mapped_column("metadata", ...) to avoid reserved-attribute collision
key_files:
  created:
    - alembic/versions/0003_audit_log.py
    - app/security/audit_log.py
    - app/models/audit.py
    - app/security/tests/test_audit_log.py
    - tests/unit/test_settings_audit.py
  modified:
    - alembic/env.py
    - app/config/settings.py
    - .env.example
decisions:
  - metadata_json Python attribute maps SQL column 'metadata' to avoid DeclarativeBase.metadata collision
  - datetime.UTC used (UP017) throughout; timezone.utc replaced
  - AuditPayload as RootModel[dict[str, str | int | bool | None]] — no float, no nested
  - verify_chain_rows normalizes naive datetimes to UTC before isoformat() comparison
  - emit fail-open wraps entire body in try/except Exception (Pitfall 3)
  - emit_task does late import of app.main to avoid circular dependency at module load
  - Pre-existing mypy errors in app/features/payment/ are out of scope (cartera.py, nodes.py, graph.py)
metrics:
  duration_min: 23
  completed_date: "2026-07-04"
  tasks_completed: 3
  files_created: 5
  files_modified: 3
---

# Phase 05 Plan 01: Audit Log Foundations Summary

Append-only PostgreSQL audit log with SHA-256 hash chain + trigger guard, fail-open emit/emit_task, chain verifier, AuditPayload flat-primitive validation, AuditSettings and RateLimitSettings on composite Settings.

## What Was Built

### Task 1: Migration 0003 + AuditLog model + alembic registration

`alembic/versions/0003_audit_log.py` creates the `audit_log` table (10 columns), the `audit_log_immutable()` PL/pgSQL function, and `trg_audit_log_immutable` (BEFORE DELETE OR UPDATE — raises EXCEPTION unconditionally), plus 2 indexes (`ix_audit_log_created_at`, `ix_audit_log_conversation_id`). Downgrade drops in reverse order.

`app/security/audit_log.py` defines `AuditLog(Base)` with `metadata_json` Python attribute mapping the SQL `metadata` column (reserved attribute conflict avoided). `prev_hash` has `server_default=sa_text("''")` matching the chain sentinel.

`alembic/env.py` line 30 placeholder replaced with the live import `from app.security import audit_log`.

Integration tests in `app/security/tests/test_audit_log.py` (marked `@pytest.mark.integration`, skipped without `POSTGRES_URL`) assert that DELETE and UPDATE both raise `DBAPIError` — ROADMAP criterion "DELETE con role de aplicación falla a nivel DB".

### Task 2: Hash chain + emit + emit_task + verify_chain_rows + AuditPayload

All 8 public symbols implemented in `app/security/audit_log.py`:

- `canonical(entry)` — `orjson.dumps(..., option=OPT_SORT_KEYS | OPT_NON_STR_KEYS)`, key-order independent
- `compute_payload_hash(payload)` — `sha256(canonical(payload)).hexdigest()`
- `compute_entry_hash(prev_hash, entry)` — `sha256(prev_hash.encode() + canonical(entry)).hexdigest()`
- `emit(session_factory, ...)` — async, advisory lock serialized, fail-open (entire body in `try/except Exception`)
- `emit_task(...)` — sync fire-and-forget via `asyncio.create_task`, late-imports `app.main.app`, fail-open
- `verify_chain_rows(rows)` — pure, checks both prev_hash linkage and entry_hash recomputation; returns `(False, first_bad_id)` on tamper
- `verify_chain(session_factory)` — fetches all rows ordered by id ASC, delegates to `verify_chain_rows`, fail-open on DB error

`app/models/audit.py` provides `AuditPayload = RootModel[dict[str, str | int | bool | None]]` — rejects floats and nested dicts (Pitfall 4). Docstring notes to cast monetary amounts to int cents or str.

19 unit tests cover: table registration, column presence, metadata_json attribute, canonical determinism, compute_entry_hash reference vector, AuditPayload accept/reject, verify_chain_rows (valid chain, payload_hash tamper, prev_hash linkage break, empty), emit fail-open on DB error, emit fail-open on invalid payload, emit_task no-session-factory.

### Task 3: AuditSettings + RateLimitSettings + .env.example

`app/config/settings.py` extended with:
- `AuditSettings(BaseSettings)` — `env_prefix="AUDIT_"`, `sink_path: Path = Path("/data/comprobantes/audit")`, `sink_enabled: bool = True`
- `RateLimitSettings(BaseSettings)` — `env_prefix="RATE_LIMIT_"`, `enabled`, `phone_limit=20`, `poliza_limit=10`, `global_limit=500`, `window_s=60`
- Both registered on composite `Settings` via `Field(default_factory=...)` and added to `__all__`

`.env.example` has Phase 5 section with all 7 env vars documented.

## Test Results

- 323 passed, 8 deselected (integration), 3 warnings (pre-existing FastAPI deprecation)
- Previous baseline: 305 passed (+ 18 new tests from this plan)
- All ruff checks pass (UP017 fixed, import sort fixed)
- mypy --strict: 0 errors in `audit_log.py` and `audit.py`; pre-existing errors in `app/features/payment/` are out of scope

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Cleanup] UP017 datetime.UTC ruff lint fix**
- **Found during:** Task 3 (ruff check)
- **Issue:** `timezone.utc` usage triggers UP017 ("Use `datetime.UTC` alias")
- **Fix:** Replaced `from datetime import datetime, timezone` with `from datetime import UTC, datetime`; all `timezone.utc` replaced with `UTC` in both `audit_log.py` and test file
- **Files modified:** `app/security/audit_log.py`, `app/security/tests/test_audit_log.py`
- **Commit:** aae9d87

**2. [Rule 2 - Cleanup] `__import__("sqlalchemy").TIMESTAMP` refactored**
- **Found during:** Task 3 code review
- **Issue:** Dynamic `__import__` call for TIMESTAMP type was a code smell
- **Fix:** Added `from sqlalchemy import TIMESTAMP as _TIMESTAMP` to imports
- **Files modified:** `app/security/audit_log.py`
- **Commit:** aae9d87

**3. [Rule 2 - Cleanup] Import sort fix in test file**
- **Found during:** Task 3 ruff check
- **Issue:** I001 import block un-sorted (app.security before app.config)
- **Fix:** Reordered to app.config then app.security
- **Files modified:** `app/security/tests/test_audit_log.py`
- **Commit:** aae9d87

### Offline alembic DDL check

The plan's verification step `uv run alembic upgrade head --sql | grep -c "audit_log_immutable"` cannot execute because `alembic/env.py` intentionally raises `RuntimeError` in offline mode (line 74 — "Offline alembic mode is not configured for landa-agent-service"). The trigger DDL is confirmed present via `grep -c "audit_log_immutable" alembic/versions/0003_audit_log.py` → 6 matches. This is consistent with the existing project design (offline mode is unused per the existing comment).

## Self-Check: PASSED

All 5 created files found on disk. All 5 task commits verified in git log.
