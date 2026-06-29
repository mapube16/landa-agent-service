---
phase: 02-integraci-n-softseguros-whatsapp-cloud-api
plan: 02
subsystem: meta cloud api integration — outbound client + webhook receiver + echo
tags: [meta-cloud-api, whatsapp, hmac, idempotency, allowlist, webhook, echo]
requires: [02-01]
provides:
  - MetaCloudClient.send_text + MetaCloudClient.send_media_ack (outbound)
  - get_meta_client() cached httpx singleton with bearer Authorization
  - _hash_phone helper (sha256[:8]) for PII-safe log correlation
  - GET /webhooks/meta subscription challenge (D-09, D-17)
  - POST /webhooks/meta receiver (HMAC -> parse -> dedup -> allowlist -> echo)
  - _verify_signature helper using hmac.compare_digest (D-16)
  - echo helpers: _normalize_e164 / is_echo_allowed / format_echo / format_media_echo
  - app.state.meta wired in lifespan
  - meta_router included after health_router
affects: [app/main.py (lifespan + router include)]
tech-stack:
  added: []
  patterns:
    - "Raw body capture before json parse for HMAC integrity (Pitfall 1/10)"
    - "hmac.compare_digest exclusively, never ==, for HMAC (D-16)"
    - "redis.set NX EX for idempotency (binary-safe key + value, Pitfall 6)"
    - "E.164 normalisation on both sides of allowlist comparison (Pitfall 8)"
    - "Phone log correlation via sha256[:8], never raw (T-02-08)"
    - "Malformed JSON -> 200 (not 422) to silence Meta 24h retry loop (Pitfall 7)"
key-files:
  created:
    - tests/test_integrations_meta_cloud.py
    - tests/test_features_handoff_echo.py
    - tests/test_webhooks_meta.py
  modified:
    - app/integrations/meta_cloud.py (skeleton -> implementation)
    - app/features/handoff/echo.py (skeleton -> implementation)
    - app/webhooks/meta.py (skeleton -> implementation)
    - app/main.py (lifespan + meta_router)
decisions:
  - "send_media_ack imports format_media_echo locally to avoid circular import with handoff/echo"
  - "_dispatch_message receives meta + redis as typed Any to avoid circular module imports — contract enforced by test stubs"
  - "Status-update path NEVER touches Redis (no message_id semantically); confirmed by test_post_status_update_acknowledged_without_dispatch"
  - "_normalize_e164 strips whitespace as well as prefixing '+' — extra defensiveness against operator-set allowlist entries"
metrics:
  duration: 35m
  completed: 2026-06-28
---

# Phase 2 Plan 02: Meta Cloud API integration — outbound client + webhook + echo Summary

One-liner: Implemented the WhatsApp slice end-to-end — MetaCloudClient outbound sender, webhook receiver with the D-15 invariant order (HMAC -> parse -> dedup -> allowlist -> echo), pure-function echo helpers, and 36 new tests covering every branch including the HMAC matrix, idempotency dup-skip, E.164 normalisation, media echo, status acknowledge, and malformed-JSON tolerance.

## What Shipped

### `app/integrations/meta_cloud.py` (skeleton -> implementation, ~115 lines)

- `MetaCloudClient.send_text(to, body) -> str` POSTs the `OutboundText` Pydantic shape to `/{phone_id}/messages` and returns the upstream `wamid`. Raises `httpx.HTTPStatusError` on 4xx/5xx (F2 does not retry; caller decides).
- `MetaCloudClient.send_media_ack(to, media_type) -> str` delegates to `send_text(format_media_echo(media_type))`. Local import of `format_media_echo` keeps the dep-graph acyclic.
- `get_meta_client()` is an `@lru_cache(maxsize=1)` singleton holding an `httpx.AsyncClient` with `base_url=META_BASE_URL`, `Authorization: Bearer {WA_TOKEN}` baked into headers once, 20/50 keepalive limits, and a 10s timeout.
- `_hash_phone(phone)` exported as the canonical PII-safe correlation token (sha256[:8]) used by both this module and `app/webhooks/meta.py`.

### `app/features/handoff/echo.py` (skeleton -> implementation, ~45 lines)

- `_normalize_e164(raw)` strips whitespace then prepends `+` if missing. Idempotent.
- `is_echo_allowed(phone)` normalises both `phone` and every `WA_ECHO_ALLOWLIST` entry before set-membership.
- `format_echo(text)` returns `"echo: <text>"`.
- `format_media_echo(media_type)` returns `"echo: [<media_type>] received"`.

### `app/webhooks/meta.py` (skeleton -> implementation, ~210 lines)

- Module docstring documents the D-15 invariant order **HMAC -> parse -> dedup -> allowlist -> echo** with the threat-model reasoning for each gate.
- `_verify_signature(raw, header, secret)` uses `hmac.compare_digest` only.
- **`GET /webhooks/meta`** — Query-aliased `hub.mode` / `hub.verify_token` / `hub.challenge` params; 200 + plain-text challenge on subscribe+match, 403 otherwise.
- **`POST /webhooks/meta`** — Raw body captured first, HMAC verified, Pydantic parse, then per-message dispatch through `_dispatch_message`. Status updates acknowledged without dispatch (D-05). Malformed JSON returns 200 (Pitfall 7).
- `_dispatch_message(msg, meta, redis)` runs: idempotency (binary `redis.set` with `nx=True, ex=86400`) -> allowlist -> echo. Text -> `send_text`. Image/audio/sticker/video/document/voice/location -> `send_media_ack`. Unsupported (contacts/interactive/button/unknown) -> log + skip. All log lines use `_hash_phone` only.

### `app/main.py` (lifespan + router include)

- Added imports for `get_meta_client` and `meta_router` (E402 guarded — must come after `init_sentry`).
- Lifespan registers `app.state.meta = get_meta_client()` between the checkpointer setup and the Plan 02-03 `app.state.softseguros` wiring.
- `app.include_router(meta_router)` placed after `app.include_router(health_router)`. Plan 02-03's `/test/poliza/{poliza_id}` endpoint and `app.state.softseguros` wiring left untouched.

### Tests created

| File | Count | Coverage |
|---|---|---|
| `tests/test_features_handoff_echo.py` | 11 | E.164 add/idempotent/strip + allowlist truth table (with/without `+`, unknown, empty) + format_echo (ASCII + unicode) + format_media_echo (image/audio) |
| `tests/test_integrations_meta_cloud.py` | 12 | Constants (`META_API_VERSION`/`META_BASE_URL`) + factory singleton + phone_id wiring + Bearer header wiring + base_url + `_hash_phone` (length + determinism + uniqueness) + `send_text` POST shape + wamid return + 4xx raises + `send_media_ack` delegation |
| `tests/test_webhooks_meta.py` | 13 | GET challenge (200 match / 403 wrong token / 403 wrong mode) + POST HMAC (200 valid+echo / 401 invalid / 401 missing header) + dedup dup-skip + non-allowlisted skip + image media-ack dispatch + status acknowledge no-dispatch + malformed JSON -> 200 + E.164 Meta-without-plus matches allowlist-with-plus + unsupported type skip |

## Verification

- `.venv/Scripts/pytest.exe -q` -> **66 passed** (12 Phase 1 + 18 Plan 02-03 softseguros/smoke + 36 Plan 02-02)
  - `tests/test_features_handoff_echo.py` 11 passed
  - `tests/test_integrations_meta_cloud.py` 12 passed
  - `tests/test_webhooks_meta.py` 13 passed
- `.venv/Scripts/mypy.exe --strict app/` -> **Success: no issues found in 30 source files**
- `Grep '==' app/webhooks/meta.py` -> 6 matches, all benign:
  - 3 in docstrings (the explicit prohibition references)
  - `hub_mode == "subscribe"` (config-time check, no HMAC vector)
  - `hub_verify_token == settings.whatsapp.verify_token.get_secret_value()` (config-time check, D-17 explicitly allows)
  - `msg.type == "text"` (string literal discriminator)
  Zero `==` in HMAC path — confirmed via `_verify_signature` which uses `hmac.compare_digest` exclusively.
- `Grep 'from_phone' app/` -> 0 hits. No raw phone numbers reach any log line; every correlation uses `_hash_phone(sha256[:8])`.
- Order invariant **HMAC -> parse -> dedup -> allowlist -> echo** verified end-to-end by `test_post_duplicate_message_id_skips_echo`: same payload sent twice yields exactly one `send_text` call, and `test_post_non_allowlisted_sender_skips_echo` confirms dedup runs even for non-allowlisted senders (asserts `redis.set` is called once but `send_text` is not).
- Routes registered (verified by every passing webhook test): `GET /webhooks/meta`, `POST /webhooks/meta`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Top-level `app.*` imports in new test modules fail before autouse fixture runs**

- **Found during:** Task 1, first `pytest` run
- **Issue:** `tests/test_features_handoff_echo.py` and `tests/test_integrations_meta_cloud.py` imported `from app.features... / from app.integrations.meta_cloud ...` at module scope. The autouse session-scoped `_test_env` fixture sets `POSTGRES_URL` and friends only after test collection — collection-time imports trigger `Settings()` instantiation, which fails with `ValidationError` for missing required fields.
- **Fix:** Moved every `from app.*` import inside the test functions / fixtures (same pattern as the existing `tests/test_llm_factory.py`). Module-level imports are restricted to `httpx`, `pytest`, stdlib.
- **Files modified:** `tests/test_features_handoff_echo.py`, `tests/test_integrations_meta_cloud.py`
- **Commit:** `538d3dc` (in-task auto-fix)

### Plan 02-03 coordination

Plan 02-03 landed `app/main.py` changes for SoftSeguros first (commits prior to mine). My Task 3 added Meta imports + lifespan registration + router include **around** the 02-03 wiring without removing anything. Final lifespan order: Postgres -> Redis -> checkpointer -> **Meta (new)** -> SoftSeguros (02-03). Final route order: health -> **meta (new)** -> /test/llm -> /test/sentry -> /test/poliza/{poliza_id} (02-03).

## Commits

| Hash | Task | Message |
|---|---|---|
| `538d3dc` | 1 | `feat(02-02): implement MetaCloudClient + echo helpers + 23 tests` |
| `6b8a2a0` | 2 | `feat(02-02): implement webhook handler — HMAC + dedup + allowlist + echo` |
| `d7d8717` | 3 | `feat(02-02): wire meta_router + app.state.meta into lifespan` |

(Note: `6b8a2a0` also swept in an untracked `02-03-SUMMARY.md` file that Plan 02-03 had dropped in the working tree; the doc itself belongs to that other plan and is committed as-is.)

## Notes for Downstream Plans

**Plan 02-03 (already in main):** Touched `app/main.py` before me. My edits inserted the Meta wiring **between** the checkpointer setup and the SoftSeguros wiring. Plan 02-03's `app.state.softseguros = get_softseguros_client()` and `app.state.softseguros._redis = app.state.redis` lines are intact; the `/test/poliza/{poliza_id}` endpoint is intact.

**Plan 02-04 (operator wire-up):** The public webhook URL is ready:

```
https://landa-agent-service-production.up.railway.app/webhooks/meta
```

The operator can subscribe this in the Meta dashboard with `WA_VERIFY_TOKEN = 96715a9c2658c915544f2faf735b98c0` (captured in 02-CONTEXT D-17) once Plan 02-04 sets the env vars in Railway. The handshake works against the GET endpoint immediately on deploy; POST starts dispatching echoes the moment a number in `WA_ECHO_ALLOWLIST` writes to the bot.

## Self-Check: PASSED

- `app/integrations/meta_cloud.py` — exists, 115 lines, exports `MetaCloudClient` / `get_meta_client` / `META_API_VERSION` / `META_BASE_URL` (+ private `_hash_phone`)
- `app/features/handoff/echo.py` — exists, 45 lines, exports `format_echo` / `format_media_echo` / `is_echo_allowed` (+ private `_normalize_e164`)
- `app/webhooks/meta.py` — exists, 210 lines, exports `router`; verified `hmac.compare_digest` used + raw body read before parse + dedup/allowlist/echo order
- `app/main.py` — `get_meta_client` import + `app.state.meta = get_meta_client()` in lifespan + `app.include_router(meta_router)` all present
- `tests/test_features_handoff_echo.py` — 11 tests
- `tests/test_integrations_meta_cloud.py` — 12 tests
- `tests/test_webhooks_meta.py` — 13 tests
- Commits `538d3dc`, `6b8a2a0`, `d7d8717` all present in `git log`
- `pytest -q` — 66 passed; `mypy --strict app/` — Success: no issues found in 30 source files
- Pre-commit (ruff, ruff-format, black, mypy, all pre-commit-hooks) — green on every commit
