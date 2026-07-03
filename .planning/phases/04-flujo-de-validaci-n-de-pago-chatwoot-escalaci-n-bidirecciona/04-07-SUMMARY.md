---
phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
plan: 07
subsystem: webhooks
tags: [lambda-proyect, handoff, bearer-auth, meta-template, idempotency, fastapi]

# Dependency graph
requires:
  - phase: 04-01
    provides: Case model (cases table), LambdaProyectSettings.internal_token, PaymentSettings.template_no_answer_name
  - phase: 04-02
    provides: MetaCloudClient.send_template (body params + quick-reply payloads)
provides:
  - POST /case/handoff/no_answer — bearer-authed voice-agent handoff (D-19..D-23)
  - Case UPSERT-by-PK idempotency pattern for cross-service retransmits
affects: [04-08, lambda-proyect integration contract]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Bearer-token dependency with hmac.compare_digest (constant time) as APIRouter Depends gate"
    - "Fake session object (execute/add/commit) for endpoint DB tests — no sqlite dep, no live Postgres"

key-files:
  created:
    - app/webhooks/handoff.py
    - app/webhooks/tests/test_handoff_no_answer.py
  modified:
    - app/main.py

key-decisions:
  - "settings singleton (repo convention per settings.py docstring) instead of plan's per-request Settings() instantiation"
  - "app.state.session_factory used (actual main.py attribute; plan's db_session_factory renamed per plan's own confirm-and-match instruction)"
  - "E.164 regex ^\\+\\d{8,15}$ wins over the plan's 6-digit behavior example phone (security constraint T-04-07-04)"
  - "SELECT-then-INSERT per plan spec, not ON CONFLICT — lambda retries are sequential; ponytail comment marks the upgrade path"

patterns-established:
  - "Cross-service internal auth: Authorization: Bearer <shared-token> verified via hmac.compare_digest in a FastAPI dependency"

requirements-completed: [D-19, D-20, D-21, D-22, D-23]

# Metrics
duration: 20min
completed: 2026-07-03
---

# Phase 4 Plan 07: Lambda no-answer handoff endpoint Summary

**POST /case/handoff/no_answer authenticates lambda-proyect's voice agent with a constant-time bearer check, opens an awaiting_receipt Case keyed by case_id, and sends the voice_no_answer_followup Meta template with D-20 body params + D-21 quick replies — retransmits return sent=false without a second send**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-03T15:54:05Z
- **Completed:** 2026-07-03T16:14:35Z
- **Tasks:** 1 (TDD: RED + GREEN commits)
- **Files modified:** 3 (2 created, 1 modified)

## Accomplishments

- New router `app/webhooks/handoff.py` (`prefix="/case"`): `_verify_bearer` dependency raises 401 on missing/malformed/wrong bearer using `hmac.compare_digest` against `LAMBDA_PROYECT_INTERNAL_TOKEN` (T-04-07-01, constant time).
- `NoAnswerHandoff` Pydantic model: E.164 phone (`^\+\d{8,15}$`), bounded `cliente_nombre` (80) / `numero_poliza` (40), `uuid.UUID` case_id — 422 on any violation (T-04-07-04).
- Happy path inserts `Case(case_id, phone, cliente_nombre, poliza_id, status="awaiting_receipt")` then calls `meta.send_template(phone, "voice_no_answer_followup", "es", [nombre, poliza], ["si_ayudenme", "mas_tarde"])`; returns `{"case_id": ..., "sent": true}`.
- Retransmit with the same case_id finds the existing row and returns `sent: false` with zero template sends (T-04-07-02) — verified by test (`send_template.call_count == 1` across two POSTs).
- Logging discipline: only `_hash_phone(phone)` logged, `cliente_nombre` never (T-04-07-03).
- Router registered in `app/main.py` next to meta + chatwoot routers; `/case/handoff/no_answer` confirmed in `app.openapi()["paths"]`.
- Template tap routing needs no new code: quick-reply taps arrive as `interactive.button_reply` through the existing `webhooks/meta.py` interactive path.
- 6 new tests green; full suite 233 passed, zero regressions.

## Task Commits

Task followed TDD (test RED -> feat GREEN):

1. **Task 1: POST /case/handoff/no_answer — bearer auth + UPSERT + template send** — `2c05d18` (test), `c212e8f` (feat)

## TDD Gate Compliance

Gate sequence verified in git log: `test(04-07)` (2c05d18) strictly before `feat(04-07)` (c212e8f). RED run failed all 6 tests (NotImplementedError stubs, repo mypy-strict pre-commit pattern from 04-02/04-03); GREEN run passed all 6 before commit.

## Files Created/Modified

- `app/webhooks/handoff.py` — router, `NoAnswerHandoff` model, `_verify_bearer` dependency, `handoff_no_answer` endpoint
- `app/webhooks/tests/test_handoff_no_answer.py` — 6 tests: bearer reject x2, 422 x2, happy path, idempotent retransmit
- `app/main.py` — `handoff_router` import + `include_router`

`app/webhooks/__init__.py` was listed in the plan's files but needed no change — it is an empty package marker and routers are imported directly from submodules (existing repo style).

## Decisions Made

- **`settings` singleton over `Settings()`:** the plan's action literal instantiates `Settings()` inside the dependency/endpoint; repo convention (settings.py docstring: "Import as `from app.config.settings import settings` everywhere") and every existing webhook use the singleton. Singleton used.
- **`app.state.session_factory`:** the plan named `db_session_factory` but instructed "confirm exact attribute names against existing app/main.py — rename to match". main.py sets `app.state.session_factory`; matched.
- **Fake session in tests:** the plan suggested "in-memory or test-Postgres session". No sqlite driver is in the dependency set, and the Case model carries Postgres-only server defaults (`gen_random_uuid()`, `now()`). A 20-line `_FakeSession` (execute/add/commit) matches the repo's AsyncMock-on-app.state test pattern (04-03 precedent) and asserts the same behavior (row fields, single insert, idempotent skip).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan-internal contradiction on the example phone vs the E.164 regex**
- **Found during:** Task 1 (test authoring)
- **Issue:** `<behavior>` uses `phone="+573001"` (6 digits) as the valid happy-path input, but `<action>` mandates `^\+\d{8,15}$` (8-15 digits) — the example can never pass its own validator.
- **Fix:** Kept the regex (it is the threat-model mitigation T-04-07-04; input validation at a trust boundary is never simplified away) and used a full-length Colombian E.164 number `+573001234567` in tests. Same contradiction class as 04-02's PNG signature; here the security spec wins over the abbreviated example.
- **Files modified:** app/webhooks/tests/test_handoff_no_answer.py
- **Verification:** happy path 200 with the full number; `phone="abc"` 422
- **Committed in:** 2c05d18

---

**Total deviations:** 1 auto-fixed (plan bug). No scope creep, no new dependencies.

## Known Stubs

None. The RED-phase `NotImplementedError` stubs were replaced in the GREEN commit.

## Threat Flags

None beyond the plan's threat model — the new endpoint and its auth path are exactly the modeled surface (T-04-07-01/02/03/04 all implemented as specified; T-04-07-05 accepted per plan).

## User Setup Required

**External service configuration needed (from plan frontmatter `user_setup`):**

1. **Meta Business Suite -> WhatsApp Manager -> Message templates:** submit UTILITY template `voice_no_answer_followup`, lang `es`, body per D-20, quick-reply buttons per D-21. The endpoint is deployable before approval; `send_template` will 4xx until Meta approves.
2. **Railway -> landa-agent-service -> Variables:** after approval, `META_TEMPLATE_NO_ANSWER_NAME=voice_no_answer_followup` (already the code default).

## Verification Results

- `pytest app/webhooks/tests/test_handoff_no_answer.py -q` -> 6 passed
- Full suite (`pytest`) -> 233 passed, 0 failed
- `ruff check` + `black --check` on handoff.py + main.py -> clean; `mypy --strict app/webhooks/handoff.py` -> 0 errors
- Route check via `app.openapi()["paths"]` -> `/case/handoff/no_answer` present (the plan's `[r.path for r in app.routes]` one-liner is broken on this FastAPI version, per 04-03 finding)
- Greps: `compare_digest` x2, `send_template` x1, `case_id` x9 in handoff.py

## Next Phase Readiness

- lambda-proyect can now replace its dead `whatsapp_notifier.py` stub with a `POST /case/handoff/no_answer` call carrying the shared bearer token.
- Quick-reply taps (`si_ayudenme` / `mas_tarde`) flow through the existing interactive routing in `webhooks/meta.py` into the Q&A graph — 04-04/04-05 payment nodes pick the case up from the `cases` table by phone.

## Self-Check: PASSED

- `app/webhooks/handoff.py` EXISTS (contains `router = APIRouter` — must_have artifact)
- `app/webhooks/tests/test_handoff_no_answer.py` EXISTS
- `app/main.py` modified (handoff_router registered)
- Commits 2c05d18, c212e8f present in git log
- 233 tests passing, 0 failures; worktree clean before SUMMARY commit

---
*Phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona*
*Completed: 2026-07-03*
