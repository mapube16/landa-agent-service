---
phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
plan: "06"
subsystem: payment-scheduler
tags: [arq, cron, scheduler, business-hours, cleanup, tdd]
dependency_graph:
  requires:
    - 04-01  # business_hours.py + DB migration (cases/attachments tables)
    - 04-04  # process_attachment ARQ job + payment nodes
  provides:
    - check_pending_cases ARQ cron (every-minute, business-hours-aware)
    - cleanup_attachments_90d ARQ cron (daily 02:00 UTC)
    - business_minutes_between pure function
  affects:
    - app/worker.py (WorkerSettings.functions + cron_jobs)
tech_stack:
  added: []
  patterns:
    - ARQ cron jobs (minute=set(range(60)) for every-minute)
    - Monkeypatch _now_utc / _get_settings_payment for test isolation
    - anyio.Path.unlink() for async-safe file deletion
    - Day-by-day block overlap for business minute counting
key_files:
  created:
    - app/features/payment/scheduler.py
    - app/features/payment/tests/test_scheduler.py
  modified:
    - app/features/payment/business_hours.py
    - app/worker.py
decisions:
  - business_minutes_between uses day-by-day loop (max 60 days) over WORKDAY_BLOCKS to avoid float precision; returns int minutes via integer division
  - _now_utc and _get_settings_payment are module-level callables (monkeypatch targets) rather than ctx injection to keep job signatures clean
  - cleanup uses anyio.Path.unlink() (not pathlib.Path) to satisfy ASYNC240 ruff rule in async context
  - cron(check_pending_cases, minute=set(range(60))) is ARQ's "every minute" form; job bails immediately outside business hours (negligible overhead)
  - Off-hours anchored window (Friday→Monday) tested explicitly to confirm business_minutes_between crosses weekend correctly
metrics:
  duration_minutes: 9
  completed_date: "2026-07-04"
  tasks_completed: 2
  files_changed: 4
  tests_added: 15
---

# Phase 04 Plan 06: Business-Hours Scheduler + 90-Day Cleanup Summary

Business-hours-aware ARQ cron scheduler for stale payment cases (D-10, D-11, D-12, D-13, D-14) plus 90-day attachment cleanup (D-02) implemented as two registered cron jobs backed entirely by Postgres rows.

## What Was Built

### business_minutes_between (business_hours.py)

Pure function added to the existing pure module. Takes two tz-aware UTC datetimes and returns the count of business minutes in `[start, end)` using a day-by-day loop (60-day safety bound) that computes WORKDAY_BLOCKS overlap in integer minutes. Handles cross-weekend ranges, lunch gaps, and start-after-end (returns 0).

### scheduler.py — check_pending_cases

Every-minute ARQ cron body implementing D-11 (20-min reminder) and D-12 (90-min escalation):

1. **Off-hours gate**: bails immediately if `not is_business_time(now_co)` → `{"skipped": "off_hours"}`.
2. **DB poll**: `SELECT cases WHERE status='awaiting_cartera' AND work_hours_due_at <= NOW() AND escalated_at IS NULL`.
3. **Per-case logic**:
   - `elapsed = business_minutes_between(case.created_at, now_utc)`
   - If `reminder_sent_at IS NULL` and `elapsed >= 20`: send buttons reminder to cartera, `UPDATE reminder_sent_at = NOW()`, `continue` (no escalate same tick).
   - Elif `elapsed >= 90` and `reminder_sent_at IS NOT NULL`: post Chatwoot note, send D-12 text to client, `UPDATE status='escalated', escalated_at=NOW()`.
4. Idempotency is purely predicate-based (T-04-06-01): double-fire in same minute yields 0 matching rows for the already-processed case.

### scheduler.py — cleanup_attachments_90d

Daily ARQ cron body at 02:00 UTC:

1. Query `Attachment WHERE received_at < now() - 90 days`.
2. `await anyio.Path(att.path).unlink()` (async-safe, FileNotFoundError swallowed).
3. `DELETE FROM attachments WHERE id = att.id`.
4. Case rows untouched (audit retention, T-04-06-02).

### worker.py updates

- Added `from arq import cron` and imported both scheduler functions.
- Appended `check_pending_cases` and `cleanup_attachments_90d` to `WorkerSettings.functions` alongside the existing mirror + process_attachment jobs.
- Added `cron_jobs` class attribute: `[cron(check_pending_cases, minute=set(range(60))), cron(cleanup_attachments_90d, hour={2}, minute={0})]`.
- Added operator deploy note in module docstring.

## Tests (15 new, all green)

| Test | What it asserts |
|------|-----------------|
| test_same_instant_returns_zero | business_minutes_between edge: same instant = 0 |
| test_20_minutes_inside_morning_block | 10:00-10:20 = 20 min |
| test_spans_lunch_break | 10:00-15:00 = 180 min (skips 12-14 gap) |
| test_full_workday_is_360_minutes | 08:00-16:00 = 360 min |
| test_weekend_minutes_not_counted | Fri 15:00 - Mon 09:00 = 120 min |
| test_outside_hours_not_counted | Sat - Mon 08:20 = 20 min |
| test_start_after_end_returns_zero | reverse order = 0 |
| test_off_hours_skips_processing | Saturday → skipped, no Meta call |
| test_reminder_fires_at_20_business_minutes_and_only_once | Monday 10:21 → send_buttons called once |
| test_reminder_not_fired_if_already_sent | reminder_sent already = no second call |
| test_escalates_after_90_business_minutes | 11:31 Monday → Chatwoot + send_text |
| test_off_hours_anchored_window_reminder_fires | Fri created → Mon 08:21 reminder fires |
| test_no_cartera_configured_logs_error_and_returns | empty allowlist → early return |
| test_cleanup_unlinks_old_attachments_and_preserves_young | 91-day file deleted, 5-day kept |
| test_cleanup_returns_zero_when_nothing_to_delete | no old rows → deleted=0 |

## Deviations from Plan

None — plan executed exactly as written. Minor implementation detail: `anyio.Path.unlink()` used instead of `pathlib.Path.unlink(missing_ok=True)` to satisfy ruff ASYNC240 (async context must not use blocking pathlib).

## Operator Deploy Step

After merging to main, the Railway agent-worker service does NOT auto-deploy. Run:

```
railway up --service agent-worker --ci --detach
```

(Documented in app/worker.py module docstring.)

## Self-Check

Files created/modified:
- `app/features/payment/scheduler.py` — created
- `app/features/payment/tests/test_scheduler.py` — created
- `app/features/payment/business_hours.py` — modified (business_minutes_between added)
- `app/worker.py` — modified (cron_jobs + new imports)

Commits:
- `5cdadd1` feat(04-06): add scheduler.py and business_minutes_between
- `1f96e79` feat(04-06): register cron_jobs in WorkerSettings
