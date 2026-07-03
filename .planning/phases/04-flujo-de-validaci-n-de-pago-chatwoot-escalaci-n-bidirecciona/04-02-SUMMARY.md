---
phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
plan: 02
subsystem: integrations
tags: [whatsapp, meta-cloud-api, httpx, media, templates, magic-bytes, anyio]

# Dependency graph
requires:
  - phase: 04-01
    provides: PaymentSettings + payment module skeletons (attachment constants slot)
  - phase: 02-integraci-n-softseguros-whatsapp-cloud-api
    provides: MetaCloudClient base (_post_message, _http pool, _hash_phone logging discipline)
provides:
  - MetaCloudClient.upload_media(file_path, mime_type) -> media_id (multipart POST /{phone_id}/media)
  - MetaCloudClient.download_media(media_id) -> (bytes, mime) with pre-download 5 MB gate (D-25)
  - MetaCloudClient.send_media(to, media_id, media_type, caption, buttons) -> wamid (interactive header variant, D-04)
  - MetaCloudClient.send_template(to, name, lang, body_params, quick_reply_payloads) -> wamid (D-19/20/21)
  - app/features/payment/attachment.py validate_magic_bytes + ATTACHMENT_MAX_BYTES + ALLOWED_MIME_TYPES (D-24/25/26)
affects: [04-04, 04-05, 04-07, 04-08]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "TDD RED with strict-mypy pre-commit: typed stubs raising NotImplementedError so failing tests stay statically valid"
    - "Co-located app tests bootstrap Settings() env via local conftest.py (tests/conftest.py does not reach app/*/tests/)"
    - "anyio.Path for file I/O inside async methods (ruff ASYNC230/240)"

key-files:
  created:
    - app/features/payment/attachment.py
    - app/features/payment/tests/test_attachment.py
    - app/integrations/tests/__init__.py
    - app/integrations/tests/conftest.py
    - app/integrations/tests/test_meta_cloud_media.py
  modified:
    - app/integrations/meta_cloud.py

key-decisions:
  - "AsyncMock + monkeypatch on client._http verbs (existing repo pattern) instead of respx — no new dependency"
  - "PNG magic signature is the 6-byte prefix (behavior spec wins over action's 8-byte signature; prefix suffices for D-26 intent validation)"
  - "_fetch_cdn extracted as a seam: lookaside.fbsbx.com is off the graph base URL, bearer re-sent manually, one-shot client"
  - "upload_media reads file via anyio (transitive dep of httpx/starlette) — no event-loop blocking, no new pyproject entry"

patterns-established:
  - "TDD RED commits carry typed NotImplementedError stubs to satisfy the mypy --strict pre-commit hook on app/ co-located tests"
  - "Payload dicts built inline for one-shot Meta shapes; wamid extraction always via _post_message"

requirements-completed: [D-03, D-08, D-18, D-19, D-20, D-21, D-24, D-25, D-26, D-27]

# Metrics
duration: 22min
completed: 2026-07-03
---

# Phase 4 Plan 02: Meta media + template methods and magic-byte validator Summary

**4 MetaCloudClient methods (upload_media, download_media two-step CDN flow, send_media with interactive-header buttons, send_template with quick replies) plus a stdlib-only magic-byte validator gating comprobantes at 5 MB / 4 mime types**

## Performance

- **Duration:** 22 min
- **Started:** 2026-07-03T14:55:18Z
- **Completed:** 2026-07-03T15:17:01Z
- **Tasks:** 3 (all TDD)
- **Files modified:** 6

## Accomplishments

- `upload_media`: multipart POST to `/{phone_id}/media` (anyio async file read), returns Meta `media_id`
- `download_media`: graph metadata GET then lookaside CDN GET; `file_size` gate rejects >5 MB BEFORE the binary download (D-25); returns `(bytes, mime_type)`
- `send_media`: plain image/document payload with optional caption; interactive-header variant when `(id, title)` buttons given, capped at 3 (D-04); rejects any other media type (D-24)
- `send_template`: body text params + indexed `quick_reply` button components per Meta template spec (D-19/20/21) — unblocks the lambda no-answer handoff (04-07)
- `validate_magic_bytes`: deterministic prefix check for JPEG/PNG/WebP/PDF; rejects mime-spoof (.exe as jpeg) and disallowed declared mimes; no python-magic system dep (A7)
- 22 tests green (12 media/template + 10 validator/constants); full repo suite 171 passed, zero regressions

## Task Commits

Each task followed TDD (test RED -> feat GREEN):

1. **Task 1: upload_media + download_media** - `dd5dddc` (test), `f3b1b52` (feat)
2. **Task 2: send_media + send_template** - `aa597b8` (test), `c93bcf5` (feat)
3. **Task 3: magic-byte validator + constants** - `9f7068d` (test), `002510d` (feat)

## TDD Gate Compliance

All three tasks show `test(...)` commits strictly before their `feat(...)` commits in git log. Every RED run was verified failing (NotImplementedError at runtime) before GREEN; every GREEN run verified passing before commit. No refactor commits needed.

## Files Created/Modified

- `app/integrations/meta_cloud.py` - 4 new methods + `_fetch_cdn` seam + `token` constructor param (raw bearer for CDN downloads, never logged)
- `app/features/payment/attachment.py` - `ATTACHMENT_MAX_BYTES` (5 MB, D-25), `ALLOWED_MIME_TYPES` (D-24), `_MAGIC` signatures, `validate_magic_bytes` (D-26)
- `app/integrations/tests/test_meta_cloud_media.py` - 12 async tests, AsyncMock on `_http` verbs
- `app/integrations/tests/conftest.py` - env bootstrap for `Settings()` (co-located tests do not inherit `tests/conftest.py`)
- `app/integrations/tests/__init__.py` - package marker
- `app/features/payment/tests/test_attachment.py` - 10 validator/constant tests

## Decisions Made

- `httpx.MockTransport`/respx not used: repo's established `AsyncMock` + `monkeypatch` pattern covers all assertions with zero new dependencies
- `upload_media` does NOT route through `_post_message` (the plan key_link groups it there, but `/media` returns `{"id": ...}` — no wamid to extract). `send_media`/`send_template` do route through `_post_message` as specified
- Token stored as `self._token` on the client (plan-directed) because the CDN host is outside the `_http` base URL headers

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] mypy --strict pre-commit hook rejects RED tests referencing nonexistent methods**
- **Found during:** Task 1 (RED commit)
- **Issue:** The mypy hook checks `^app/` including co-located tests; pyproject's `app/.*/tests/` mypy exclude does not apply to files pre-commit passes explicitly. RED tests calling `upload_media` etc. failed static analysis, blocking the commit (04-01 avoided this by putting RED tests under `tests/unit/`, but this plan mandates co-located tests)
- **Fix:** RED commits carry typed method stubs raising `NotImplementedError` — mypy passes, tests still genuinely fail at runtime. Also `# noqa: S106` on the test's dummy `token=` kwarg (ruff flags it as hardcoded password)
- **Files modified:** app/integrations/meta_cloud.py, app/integrations/tests/test_meta_cloud_media.py
- **Verification:** hooks green on all 6 commits; RED runs failed as required
- **Committed in:** dd5dddc, aa597b8, 9f7068d

**2. [Rule 3 - Blocking] attachment.py needed in Task 1, planned for Task 3**
- **Found during:** Task 1 (download_media)
- **Issue:** `download_media` imports `ATTACHMENT_MAX_BYTES` from `app.features.payment.attachment`, which Task 3 would create — import would fail
- **Fix:** Created `attachment.py` with the size constant in Task 1 GREEN; Task 3 added the validator and remaining constants
- **Files modified:** app/features/payment/attachment.py
- **Verification:** Task 1 tests import and pass; Task 3 extended the same file
- **Committed in:** f3b1b52

**3. [Rule 3 - Blocking] ruff ASYNC240 rejected blocking pathlib I/O in async upload_media**
- **Found during:** Task 1 (GREEN commit)
- **Issue:** `file_path.stat()` / `file_path.open()` inside `async def` block the event loop; ruff hook failed the commit
- **Fix:** `content = await anyio.Path(file_path).read_bytes()` — async read, size from `len(content)`. anyio is already a transitive dependency of httpx/starlette; nothing added to pyproject
- **Files modified:** app/integrations/meta_cloud.py
- **Verification:** ruff clean, tests green
- **Committed in:** f3b1b52

**4. [Rule 1 - Bug] Plan-internal contradiction on PNG magic signature**
- **Found during:** Task 3
- **Issue:** `<behavior>` requires `validate_magic_bytes(b"\x89PNG\r\n", "image/png") == True` (6-byte chunk) but `<action>` specifies the 8-byte signature `b"\x89PNG\r\n\x1a\n"`, which can never prefix-match a 6-byte chunk
- **Fix:** Used the 6-byte prefix `b"\x89PNG\r\n"` — behavior spec wins per TDD; prefix strength is equivalent for D-26 intent validation (real PNGs always carry the full 8 bytes anyway)
- **Files modified:** app/features/payment/attachment.py
- **Verification:** all behavior cases from the plan pass verbatim
- **Committed in:** 002510d

---

**Total deviations:** 4 auto-fixed (3 blocking, 1 plan bug)
**Impact on plan:** All fixes required to satisfy repo lint/type gates or resolve plan-internal contradictions. No scope creep, no new dependencies.

## Issues Encountered

- `uv` is not on PATH in the executor shell; tests/lint ran via the project venv interpreter directly (`.venv/Scripts/python.exe -m pytest/ruff/black`) — functionally identical to `uv run`

## Verification Results

- `pytest app/integrations/tests/test_meta_cloud_media.py app/features/payment/tests/test_attachment.py -q` → 22 passed
- Full suite (`pytest -q`) → 171 passed, 0 failed
- `ruff check` + `black --check` on both production files → clean
- `grep -E "python.?magic" pyproject.toml` → empty (no new system dep)
- Token discipline grep → `_token` referenced only via attribute access; never formatted into log lines (T-04-02-03)

## Known Stubs

None — all `NotImplementedError` stubs from RED phases were replaced in their GREEN commits. `validate_magic_bytes` has no production caller yet by design; Plan 04-04 wires it into the comprobante path (T-04-02-01 call-site discipline).

## Threat Flags

None — no security surface introduced beyond the plan's threat model (all four methods and the validator are the modeled surface; mitigations T-04-02-01/03/04 implemented as specified).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Waves 3-5 unblocked: 04-04 (comprobante handling) can call `download_media` + `validate_magic_bytes` + `upload_media`/`send_media`; 04-05 (cartera taps) has the buttons pattern; 04-07 (lambda handoff) has `send_template`
- Reminder for 04-07: template `voice_no_answer_followup` must be approved by Meta before production use (out-of-band prerequisite)

## Self-Check: PASSED

- All 6 key files exist on disk (verified with `[ -f ]`)
- All 6 task commits present in git log (dd5dddc, f3b1b52, aa597b8, c93bcf5, 9f7068d, 002510d)
- `async def upload_media` present in app/integrations/meta_cloud.py (must_have artifact check)

---
*Phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona*
*Completed: 2026-07-03*
