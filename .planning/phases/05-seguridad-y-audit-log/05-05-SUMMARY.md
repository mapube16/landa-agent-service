---
phase: "05-seguridad-y-audit-log"
plan: "05"
subsystem: "security / worker"
tags: ["audit", "worker", "cron", "ndjson", "sec-02", "sec-03", "sec-04"]
dependency_graph:
  requires:
    - "05-01 (audit_log.py: AuditLog ORM, emit_task, verify_chain)"
  provides:
    - "attachment_received audit capture in process_attachment (SEC-04)"
    - "verify_audit_chain daily cron (SEC-02)"
    - "sink_audit_log daily cron (SEC-03)"
    - "app/security/audit_sink.py: export_audit_ndjson"
  affects:
    - "app/worker.py"
    - "app/security/audit_sink.py"
tech_stack:
  added: []
  patterns:
    - "local imports inside ARQ cron functions (cold-start / circular-dep avoidance)"
    - "app.main.app.state session_factory resolution (Pitfall 5 pattern)"
    - "sync file I/O with noqa in async cron (intentional; documented in module)"
    - "orjson.dumps OPT_SORT_KEYS for deterministic NDJSON lines"
    - "cursor file for incremental export (.cursor tracks last exported id)"
key_files:
  created:
    - "app/security/audit_sink.py"
    - "tests/test_worker_audit.py"
  modified:
    - "app/worker.py"
decisions:
  - "sync file I/O in export_audit_ndjson is intentional: cron context, small batches, Railway local-disk; noqa ASYNC230/240 with inline comment"
  - "two new cron registrations at 03:00 and 03:30 UTC (off-peak, after 02:00 cleanup)"
  - "emit_task used (not emit) in process_attachment: fire-and-forget, worker already in async context but emit_task avoids awaiting in the hot path"
metrics:
  duration_minutes: 30
  completed_date: "2026-07-04"
  tasks_completed: 2
  files_created: 2
  files_modified: 1
---

# Phase 05 Plan 05: Worker Audit Machinery Summary

Worker-side audit machinery: `attachment_received` capture in `process_attachment`, daily hash-chain verification cron, and incremental NDJSON append-only sink on Railway volume.

## What Was Built

### Task 1: attachment_received capture + verify_audit_chain cron

**`app/worker.py` changes:**

- `process_attachment`: added `from app.security import audit_log` local import and `audit_log.emit_task(action="attachment_received", actor="worker", ...)` immediately before graph work. Captures the SEC-04 comprobante receipt event even if the graph subsequently fails.
- `verify_audit_chain(ctx)`: new ARQ job function. Resolves `session_factory` from `app.state` (Pitfall 5 pattern), calls `verify_chain(sf)`, logs `audit_log.chain_verified` on ok and `audit_log.chain_tampered` (error level, Sentry-visible) on mismatch. Fully fail-open.
- `sink_audit_log(ctx)`: new ARQ job function. Checks `settings.audit.sink_enabled` guard, resolves session_factory, calls `export_audit_ndjson`. Fully fail-open.
- `WorkerSettings.functions`: now 7 functions (added `verify_audit_chain`, `sink_audit_log`).
- `WorkerSettings.cron_jobs`: now 4 crons (added `verify_audit_chain` at 03:00 UTC, `sink_audit_log` at 03:30 UTC).

### Task 2: NDJSON secondary sink

**`app/security/audit_sink.py` (new file):**

- `export_audit_ndjson(session_factory, sink_dir)`: incremental NDJSON exporter.
  - Reads cursor from `{sink_dir}/.cursor` (tolerant parse, defaults to 0).
  - Queries `AuditLog WHERE id > cursor ORDER BY id ASC`.
  - Groups rows by UTC date, appends each as an orjson line to `{date}.ndjson` (binary append, never truncates).
  - Writes max exported id to cursor file at end.
  - Returns count of rows exported.
  - Fail-open: any exception logs `audit_sink.write_failed` and returns 0.

## Tests

`tests/test_worker_audit.py` — 8 tests, all green:

| Test | Covers |
|------|--------|
| `test_process_attachment_emits_audit_event` | emit_task called once with correct action/actor/payload |
| `test_verify_audit_chain_ok_logs_info` | ok path -> info log |
| `test_verify_audit_chain_tampered_logs_error` | tamper path -> error log with first_bad_id |
| `test_verify_audit_chain_no_session_factory_logs_and_returns` | fail-open on missing sf |
| `test_export_audit_ndjson_writes_partitioned_files` | 3 rows, 2 dates, 2 files, cursor=3 |
| `test_export_audit_ndjson_idempotent_second_run` | second run returns 0, no file modification |
| `test_export_audit_ndjson_unwritable_dir_returns_zero` | bad path -> 0, audit_sink.write_failed |
| `test_sink_audit_log_disabled_skips_export` | sink_enabled=False -> export never called |

## Verification Results

- `pytest tests/test_worker_audit.py -q -m "not integration"`: 8 passed
- Full suite `-m "not integration"`: 382 passed, 11 deselected, 3 warnings
- `ruff check app/security/audit_sink.py app/worker.py`: All checks passed
- `mypy --strict app/security/audit_sink.py`: 0 errors in audit_sink.py (pre-existing errors in other unrelated files)
- `WorkerSettings.functions`: 7 functions (`mirror_inbound`, `mirror_outbound`, `process_attachment`, `check_pending_cases`, `cleanup_attachments_90d`, `verify_audit_chain`, `sink_audit_log`)
- `WorkerSettings.cron_jobs`: 4 crons (`check_pending_cases`, `cleanup_attachments_90d`, `verify_audit_chain`, `sink_audit_log`)

## Deviations from Plan

None — plan executed exactly as written.

**ASYNC lint:** ruff ASYNC230/ASYNC240 rules flagged sync file I/O inside the async `export_audit_ndjson`. Suppressed with inline `# noqa` comments per the documented justification in the module docstring (cron context, small batches, Railway local-disk). This is the standard pattern for deliberate sync I/O in async cron jobs.

## Commits

| Hash | Message |
|------|---------|
| d83c4c4 | test(05-05): add failing tests for worker audit capture + verify_audit_chain cron |
| 88b6ff8 | feat(05-05): add attachment_received audit capture + verify_audit_chain cron |
| 51656d8 | feat(05-05): add NDJSON secondary audit sink (SEC-03) |

## Self-Check: PASSED

| Item | Status |
|------|--------|
| app/security/audit_sink.py | FOUND |
| tests/test_worker_audit.py | FOUND |
| commit d83c4c4 | FOUND |
| commit 88b6ff8 | FOUND |
| commit 51656d8 | FOUND |
