---
phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
plan: 03
subsystem: webhooks
tags: [chatwoot, meta-cloud-api, hmac, redis, fastapi, whatsapp, bidirectional]

# Dependency graph
requires:
  - phase: 03-q-a-inbound
    provides: ChatwootClient (get_or_create_conversation, _cache_get/_cache_set, post_message)
  - phase: 04-01
    provides: ChatwootSettings.webhook_secret (CHATWOOT_WEBHOOK_SECRET SecretStr)
provides:
  - Inverse Redis index chatwoot:phone_by_conv:{conv_id} populated on conversation create (7d TTL)
  - ChatwootClient.get_phone_by_conv(conv_id) — Redis-first, Chatwoot API fallback, None on miss
  - ChatwootClient.download_attachment(data_url) — authed blob download via shared _http
  - POST /webhooks/chatwoot — HMAC verify + filters + dedup + text/media relay to client via Meta
affects: [04-04, 04-05, escalation, payment]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Inverse Redis index alongside forward index, both written in the same code path"
    - "Webhook 200-ack semantics post-HMAC (never 5xx to Chatwoot — retry-forever prevention)"
    - "Chatwoot-host-only download guard before fetching attachment data_urls"

key-files:
  created:
    - app/webhooks/chatwoot.py
    - app/webhooks/tests/test_chatwoot_webhook.py
    - app/integrations/tests/test_chatwoot_inverse_index.py
    - app/conftest.py
  modified:
    - app/integrations/chatwoot.py
    - app/main.py
    - tests/integrations/test_chatwoot.py

key-decisions:
  - "Agent text rides as caption on the first attachment when attachments exist (no duplicate standalone text message)"
  - "ALLOWED_MIME_TYPES resolved via importlib with local fallback until parallel Plan 04-02 merges"
  - "Relay failures ack 200 + log.error instead of 5xx (Chatwoot retry-forever prevention); ARQ retry deferred until losses appear"

patterns-established:
  - "app/conftest.py: env-var bootstrap for tests colocated under app/ (mirrors tests/conftest.py placeholders)"
  - "mypy pre-commit on app-tree test files: type: ignore[import-not-found]/[untyped-decorator] on pytest imports/fixtures (04-01 precedent)"

requirements-completed: [D-15, D-16, D-17, D-18]

# Metrics
duration: 25min
completed: 2026-07-03
---

# Phase 4 Plan 03: Chatwoot → Client Bidirectional Channel Summary

**HMAC-verified POST /webhooks/chatwoot relays human-agent text + attachments to the client via Meta Cloud API, resolved through a new inverse Redis index (conv_id → phone), with agent_bot loop filtering and 24h message dedup**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-07-03T14:56:00Z
- **Completed:** 2026-07-03T15:21:00Z
- **Tasks:** 2 (both TDD: RED + GREEN commits each)
- **Files modified:** 9 (6 created, 3 modified)

## Accomplishments

- `ChatwootClient.get_or_create_conversation` now writes `chatwoot:phone_by_conv:{conv_id}` (7d TTL) alongside the forward index; `get_phone_by_conv` resolves Redis-first with `GET /conversations/{id}` API fallback (parses `meta.sender.phone_number`, repopulates cache, returns None on 404 — never raises).
- New router `POST /webhooks/chatwoot`: constant-time `X-Chatwoot-Signature` HMAC verify (supports `sha256=` prefix and bare hex), event/message_type/sender filters, Redis dedup `chatwoot:msg:{id}` (SET NX, 24h), phone resolve, text relay via `meta.send_text`, attachment re-upload via `meta.upload_media` + `meta.send_media`.
- Loop prevention verified by test: `sender.type == "agent_bot"` (bot mirror messages) are dropped; only `sender.type == "user"` relays (D-15, T-04-03-03).
- Attachment guards: Chatwoot-host-only `data_url` download (T-04-03-05, also prevents `api_access_token` leak to foreign hosts), mime allowlist (T-04-03-06), caption on first attachment only.
- 11 new tests green; full suite (205 tests, tests/ + app/) green.

## Task Commits

Each task was committed atomically (TDD: RED → GREEN):

1. **Task 1: Inverse index in ChatwootClient** — `8d1c662` (test), `0fb20db` (feat)
2. **Task 2: POST /webhooks/chatwoot — HMAC + dedup + filter + dispatch** — `ef80a42` (test), `74986ca` (feat)

## Files Created/Modified

- `app/webhooks/chatwoot.py` — router: HMAC verify, filters, dedup, `_resolve_and_relay`, `_relay_attachments`
- `app/integrations/chatwoot.py` — inverse index write, `get_phone_by_conv`, `download_attachment`, `_cache_get_raw`/`_cache_set_raw` generalization
- `app/main.py` — `chatwoot_webhook_router` registered next to `meta_router`
- `app/webhooks/tests/test_chatwoot_webhook.py` — 8 webhook tests (minimal FastAPI app + AsyncMock state)
- `app/integrations/tests/test_chatwoot_inverse_index.py` — 3 inverse-index tests (FakeRedis)
- `app/conftest.py` — env-var bootstrap for app-tree colocated tests
- `tests/integrations/test_chatwoot.py` — stale single-cache-write assertion updated

## Decisions Made

- **Caption-instead-of-duplicate-text:** the plan's literal steps (send standalone text AND caption=content on first attachment) would deliver the agent's text twice when attachments exist. Implemented the plan's stated intent ("avoid duplication"): standalone `send_text` only when no attachments; otherwise content rides as caption on the first attachment.
- **Relay failure semantics:** ack 200 + `log.error` on send failures (ponytail comment marks the ceiling: add ARQ retry if message losses appear). 5xx would trigger Chatwoot retry-forever on permanent failures.
- **`_cache_set_raw`/`_cache_get_raw`:** generalized the int-only cache helpers via raw-bytes siblings with the int wrappers delegating — smallest diff, existing writers untouched.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `ALLOWED_MIME_TYPES` lives in parallel Plan 04-02 worktree**
- **Found during:** Task 2 (webhook module)
- **Issue:** Plan says import from `app.features.payment.attachment`, but that module is created by Plan 04-02 (same wave, different worktree) — a static import breaks this worktree at import time and trips the pre-commit mypy hook (`import-not-found`, and `warn_unused_ignores` post-merge if suppressed).
- **Fix:** `importlib.import_module` with `except ImportError` fallback to the exact 04-02 value `frozenset({"image/jpeg","image/png","image/webp","application/pdf"})`. Post-merge the canonical constant is picked up automatically with zero follow-up edits.
- **Files modified:** app/webhooks/chatwoot.py
- **Verification:** module imports and tests pass in this worktree; mypy pre-commit passes
- **Committed in:** 74986ca

**2. [Rule 3 - Blocking] app-tree tests cannot instantiate Settings**
- **Found during:** Task 1 (RED)
- **Issue:** `pytest` config `testpaths=["tests"]` means `tests/conftest.py` (env-var placeholders) is not loaded for tests under `app/**/tests/`; importing `app.integrations.chatwoot` fails on `Settings()` REQUIRED fields.
- **Fix:** added `app/conftest.py` mirroring the tests/conftest.py env-var setdefault block (dummy values only).
- **Files modified:** app/conftest.py
- **Verification:** `pytest app/...` collects and runs green
- **Committed in:** 8d1c662

**3. [Rule 1 - Bug] Stale assertion in existing chatwoot test**
- **Found during:** Task 1 (GREEN)
- **Issue:** `tests/integrations/test_chatwoot.py` asserted exactly ONE 7-day-TTL cache write in `get_or_create_conversation`; the planned inverse index adds a second, breaking the outdated invariant.
- **Fix:** assertion updated to expect 2 writes and explicitly checks `chatwoot:phone_by_conv:42` is among the written keys.
- **Files modified:** tests/integrations/test_chatwoot.py
- **Verification:** full suite green
- **Committed in:** 0fb20db

---

**Total deviations:** 3 auto-fixed (1 bug, 2 blocking)
**Impact on plan:** All fixes required for the plan to execute in a parallel-worktree wave. No scope creep.

## Issues Encountered

- **structlog reserved kwarg:** `log.info(..., event=event)` raises `TypeError` (structlog reserves `event` for the message). Renamed log field to `event_name`; caught by the test suite before commit.
- **Plan verification one-liner outdated:** `[r.path for r in app.routes]` fails on this FastAPI version — included routers are wrapped in path-less `_IncludedRouter` objects. Verified registration via `app.openapi()["paths"]` instead (`/webhooks/chatwoot` and `/webhooks/meta` both present).
- **ruff ASYNC240 + C901:** async pathlib unlink replaced with `anyio.Path(...).unlink` (anyio ships with starlette); `receive` split into `_filter_reason` + `_resolve_and_relay` to satisfy complexity ≤ 10.
- **TDD RED vs pre-commit mypy:** RED commits importing a not-yet-existing module fail the mypy hook. Temporary `# type: ignore[import-not-found]` added in the RED commit and removed in the GREEN commit (file re-checked with the module present).

## TDD Gate Compliance

Gate sequence verified in git log: `test(...)` → `feat(...)` for both tasks (8d1c662 → 0fb20db, ef80a42 → 74986ca). RED runs failed for the right reasons (missing inverse index / missing module) before each GREEN.

## Known Stubs

None. The `ALLOWED_MIME_TYPES` fallback is a documented parallel-wave shim that self-resolves when 04-02 merges (importlib picks up the canonical constant) — it is fully functional, not a stub.

## User Setup Required

**External service configuration needed (from plan frontmatter `user_setup`):**

1. **Chatwoot admin → Settings → Integrations → Webhooks:** add outbound webhook URL `https://<agent-domain>/webhooks/chatwoot` with an HMAC secret, subscribed to the `message_created` event.
2. **Railway → landa-agent-service → Variables:** set `CHATWOOT_WEBHOOK_SECRET` to the same HMAC secret.

## Next Phase Readiness

- Bidirectional escalation channel complete: human agent replies (text + image/PDF) reach the client via WhatsApp once 04-02's `upload_media`/`send_media`/`download_media` merge (this webhook calls those duck-typed methods on `app.state.meta`; tests mock them).
- **Orchestrator note (out-of-scope discovery):** `pyproject.toml` `testpaths = ["tests"]` means colocated tests under `app/**/tests/` (04-01, 04-02, 04-03) do NOT run under a plain `uv run pytest`. Consider extending `testpaths` to `["tests", "app"]` in a single non-parallel commit so CI collects them.

## Self-Check: PASSED

All 5 key files exist on disk; all 4 task commits (8d1c662, 0fb20db, ef80a42, 74986ca) present in git log plus this docs commit; worktree clean.

---
*Phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona*
*Completed: 2026-07-03*
