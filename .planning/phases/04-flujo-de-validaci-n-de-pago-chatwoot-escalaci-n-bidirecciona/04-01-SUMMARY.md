---
phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
plan: "01"
subsystem: payment-foundation
tags: [schema, settings, state, security, business-hours, wave-1]
dependency_graph:
  requires: []
  provides:
    - cases table (Postgres)
    - attachments table (Postgres)
    - PaymentSettings + LambdaProyectSettings (settings)
    - ChatwootSettings.webhook_secret (settings)
    - QAState payment fields (state)
    - is_business_time / next_business_window_after (pure functions)
    - check_outbound (output firewall)
    - payment graph skeleton (node-name constants)
  affects:
    - app/config/settings.py (composite Settings gains .payment + .lambda_proyect)
    - app/features/qa/state.py (QAState node Literal extended + 6 new fields)
    - alembic/env.py (case_store import for autogenerate)
tech_stack:
  added:
    - SQLAlchemy 2.0 declarative models (Case, Attachment) on existing Base
    - alembic migration 0002_payment_tables with 4 indexes (2 partial)
  patterns:
    - TDD RED/GREEN for all 3 tasks (5 commits: 2 RED, 3 GREEN)
    - Pure module pattern for business_hours (no app.* deps, no I/O)
    - Pydantic BaseSettings with alias for non-prefixed env vars (CARTERA_PHONE_ALLOWLIST)
    - frozenset property for immutable CSV env var (cartera_phone_allowlist)
key_files:
  created:
    - app/memory/case_store.py
    - alembic/versions/0002_payment_tables.py
    - app/features/payment/business_hours.py
    - app/features/payment/graph.py
    - app/features/payment/nodes.py
    - app/features/payment/tests/__init__.py
    - app/features/payment/tests/test_business_hours.py
    - app/security/output_firewall.py
    - app/security/tests/__init__.py
    - app/security/tests/test_output_firewall.py
    - tests/unit/test_case_store.py
    - tests/unit/test_settings_payment.py
  modified:
    - app/memory/__init__.py (re-exports Case, Attachment, case_store)
    - alembic/env.py (live import of case_store)
    - app/config/settings.py (PaymentSettings, LambdaProyectSettings, ChatwootSettings.webhook_secret)
    - app/features/qa/state.py (6 payment fields + 5 node names in Literal)
    - .env.example (Phase 4 env vars documented)
    - tests/conftest.py (Phase 4 placeholder env vars)
    - pyproject.toml (mypy exclude for app/.*/tests/)
decisions:
  - "UUID v4 (gen_random_uuid()) as case_id PK — consistent with PROJECT.md + simpler than ULID"
  - "cartera_phone_allowlist_raw stored as str + @property frozenset — avoids NoDecode complexity for non-prefixed alias"
  - "Regex extended beyond RESEARCH to also block 'pago fue confirmado' — plan behavior spec required it"
  - "app/.*/tests/ excluded from mypy (pre-commit mypy runs on app/ files directly)"
  - "Payment test files co-located with source per PLAN.md instruction (app/features/payment/tests/)"
metrics:
  duration_minutes: 27
  completed_date: "2026-06-30"
  tasks_completed: 3
  tasks_total: 3
  files_created: 12
  files_modified: 7
---

# Phase 04 Plan 01: Schema + Settings + Skeletons Summary

**One-liner:** Alembic migration 0002 with cases+attachments tables, PaymentSettings/LambdaProyectSettings, QAState payment fields, business-hours helper, and output_firewall regex gate — Wave 1 zero-runtime-change foundation.

## Tasks Completed

| Task | Name | Commit | Status |
|------|------|--------|--------|
| 1 | Schema migration + SQLAlchemy models + alembic registration | 2329e27 | Done |
| 2 | Settings + QAState extensions + env.example | 99799e3 | Done |
| 3 | business_hours.py + output_firewall.py + payment module skeletons | 833a105 | Done |

## TDD Gate Compliance

Each task followed RED/GREEN cycle:

| Task | RED Commit | GREEN Commit |
|------|-----------|-------------|
| 1 | 4beae90 (test: case_store models) | 2329e27 (feat: models + migration) |
| 2 | 2f365b2 (test: settings + QAState) | 99799e3 (feat: settings + state) |
| 3 | Merged with GREEN (pytest import stub) | 833a105 (feat: business_hours + firewall) |

Note for Task 3: The RED commit was attempted but pre-commit mypy failed because the implementation files didn't exist yet (expected). RED tests were committed together with implementation after resolving the mypy exclude.

## Verification Results

- `uv run pytest app/features/payment/tests/ app/security/tests/test_output_firewall.py -q`: 23 passed
- `uv run pytest` (full suite): 171 passed, 3 warnings (only FastAPI deprecation)
- `uv run ruff check app/ && uv run black --check app/`: clean
- `uv run mypy --strict app/features/payment/business_hours.py app/security/output_firewall.py app/memory/case_store.py`: 0 errors
- `grep -v '^#' app/features/qa/state.py | grep -c "case_id|payment_status|payment_approved"`: 6 matches

## Artifacts Delivered

### Migration (alembic/versions/0002_payment_tables.py)

Creates `cases` and `attachments` tables. Four indexes:
- `ix_cases_phone` — lookup by client E.164 phone
- `ix_cases_status_open` (partial) — only non-terminal rows
- `ix_cases_work_hours_due_at` (partial) — only `awaiting_cartera` rows
- `ix_attachments_case_id` — FK join acceleration

### SQLAlchemy Models (app/memory/case_store.py)

- `Case`: UUID v4 PK, `status` with CheckConstraint (7 valid values), `attachment_count`, timer fields (`reminder_sent_at`, `escalated_at`, `work_hours_due_at`), ORM relationship to `Attachment`.
- `Attachment`: BigInteger PK autoincrement, FK to cases ON DELETE CASCADE, `sha256` for dedup, `meta_media_id` (30-day Meta CDN TTL noted).

### Settings (app/config/settings.py)

- `PaymentSettings`: `cartera_phone_allowlist` property returns `frozenset[str]` of E.164 numbers from CSV env var; `volume_path` (Path); `template_no_answer_name`.
- `LambdaProyectSettings`: `internal_token: SecretStr` required.
- `ChatwootSettings.webhook_secret: SecretStr` added.
- Composite `Settings` gains `.payment` and `.lambda_proyect`.

### QAState Extensions (app/features/qa/state.py)

6 new `NotRequired` fields: `case_id`, `attachment_count`, `attachment_idx`, `payment_status`, `cartera_message_wamid`, `payment_approved`.

`node` Literal extended with 5 payment node names: `node_receive_comprobante`, `node_forward_to_cartera`, `node_awaiting_cartera`, `node_confirming`, `node_payment_escalate`.

### Business Hours (app/features/payment/business_hours.py)

Pure module. `is_business_time(dt)` checks weekday (Mon-Fri) + block membership (8-12, 14-16 Bogota, start inclusive / end exclusive). `next_business_window_after(dt_utc)` returns input unchanged if already in block, otherwise walks forward up to 14 days with `datetime.timedelta`. Colombia UTC-5 fixed (no DST). Raises `ValueError` on naive datetime.

### Output Firewall (app/security/output_firewall.py)

`check_outbound(text, *, payment_approved)` returns `(bool, str | None)`. Regex blocks:
- `pago (confirmado|aprobado)`
- `pago fue (confirmado|aprobado|registrado|aceptado|recibido)`
- `tu pago fue (registrado|aceptado|recibido)`

Case-insensitive (`re.IGNORECASE`). Only passes when `payment_approved=True`.

### Skeletons (graph.py, nodes.py)

`graph.py`: 5 node-name string constants. `nodes.py`: empty `__all__`. Both importable; Plan 04-04 fills them.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Extended output_firewall regex to catch 'pago fue confirmado'**
- **Found during:** Task 3 GREEN phase test run
- **Issue:** Plan behavior spec required `check_outbound("Tu pago fue confirmado para POL-123", payment_approved=False)` to return `(False, reason)`. The RESEARCH regex `pago\s+(confirmado|aprobado)` doesn't match "pago **fue** confirmado" (non-adjacent). Test failed.
- **Fix:** Added `pago\s+fue\s+(confirmado|aprobado|registrado|aceptado|recibido)` as a third alternation in the regex. This is overly cautious per D-28 ("overly cautious is correct here").
- **Files modified:** `app/security/output_firewall.py`
- **Commit:** 833a105

**2. [Rule 2 - Missing] Added pytest type: ignore for mypy in co-located test**
- **Found during:** Task 3 pre-commit
- **Issue:** `app/features/payment/tests/test_business_hours.py` imports `pytest` but the pre-commit mypy hook runs on `^app/` files without pytest stubs in its isolated env.
- **Fix:** Added `# type: ignore[import-not-found]` on the pytest import line. Also added `app/.*/tests/` to mypy `exclude` in `pyproject.toml` for future test files.
- **Files modified:** `app/features/payment/tests/test_business_hours.py`, `pyproject.toml`
- **Commit:** 833a105

**3. [Rule 2 - Missing] Added Phase 4 env vars to conftest.py**
- **Found during:** Task 2 settings test run
- **Issue:** `CHATWOOT_WEBHOOK_SECRET` and `LAMBDA_PROYECT_INTERNAL_TOKEN` are required fields in the new settings classes. The test conftest didn't set them, causing `ValidationError` on import.
- **Fix:** Added both env vars as `setdefault` in `tests/conftest.py` (both at module level and in the session fixture).
- **Files modified:** `tests/conftest.py`
- **Commit:** 99799e3

## Known Stubs

None. All files created in this plan are either:
- Complete implementations (business_hours.py, output_firewall.py, case_store.py, settings.py)
- Intentional stubs documented as such (graph.py, nodes.py) — Wave 3 Plan 04-04 fills them

## Threat Flags

None. This plan introduces no new network endpoints, auth paths, or file access patterns. The `cases` and `attachments` tables are new schema but accessed only via ORM through the existing session factory.

T-04-01-02 accepted: `cases.phone` and `cases.cliente_doc` store PII for case correlation. Phase 5 audit log will hash these. No new threat surface beyond what was in the plan's threat model.

## Self-Check: PASSED

- `app/memory/case_store.py` EXISTS
- `alembic/versions/0002_payment_tables.py` EXISTS
- `app/features/payment/business_hours.py` EXISTS
- `app/security/output_firewall.py` EXISTS
- `app/features/qa/state.py` EXISTS (modified)
- `app/config/settings.py` EXISTS (modified)
- Commits 4beae90, 2329e27, 2f365b2, 99799e3, 833a105 all in git log
- 171 tests passing, 0 failures
