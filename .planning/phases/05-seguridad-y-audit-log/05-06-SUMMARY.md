---
phase: 05-seguridad-y-audit-log
plan: "06"
subsystem: webhooks/security
tags: [rate-limiting, audit-log, outbound, dispatch, SEC-04, SEC-06]
dependency_graph:
  requires: [05-01, 05-03]
  provides: [rate-limit-enforcement, outbound-audit-capture]
  affects: [app/webhooks/meta.py]
tech_stack:
  added: []
  patterns:
    - check_rate_limit injected in _dispatch_message between cartera and allowlist
    - _peek_poliza_id mirrors _reset_if_closed checkpointer.aget pattern
    - sent=True flag tracks successful outbound to gate audit emit
    - audit_log.emit_task fire-and-forget from hot path (fail-open)
key_files:
  created: [tests/test_webhooks_meta_f5.py]
  modified: [app/webhooks/meta.py]
decisions:
  - Cartera exemption via structural ordering (rate_limit placed AFTER cartera branch) — no explicit allowlist needed; exemption is a dispatch-order property
  - _peek_poliza_id uses same try/except + None-return pattern as _reset_if_closed — one checkpoint read per message, accepted cost
  - sent=True flag approach for outbound_sent — does not reorder mirror enqueue, minimal restructuring of existing try/except
  - conversation_id in audit rows uses _hash_phone (8-char truncated sha256) — consistent with log PII redaction; differs from 05-04 which uses LangGraph thread_id (noted in code comment)
  - C901 noqa on both _send_outbound and _dispatch_message — pre-existing complexity, plan explicitly accepts refactor deferral
metrics:
  duration_min: 52
  completed_date: "2026-07-04"
  tasks_completed: 2
  files_modified: 2
  tests_added: 12
  test_baseline: 382
  test_final: 394
---

# Phase 05 Plan 06: Webhook Rate Limit + Outbound Audit Summary

Webhook hot path wired with multi-level rate limiting in `_dispatch_message` (cartera structurally exempt by dispatch ordering) and outbound audit capture in `_send_outbound` (`outbound_sent`/`outbound_blocked` events via `audit_log.emit_task`).

## What Was Built

### Task 1: Rate limit enforcement in _dispatch_message

Step 4c' inserted between the cartera branch (`return`) and the client allowlist check (`is_echo_allowed`). This placement implements the cartera exemption without a separate list — cartera numbers early-return at 4c, so they structurally never reach 4c'.

New helper `_peek_poliza_id` reads the LangGraph checkpoint to extract `poliza_id` for the per-poliza rate-limit level. Mirrors `_reset_if_closed` exactly: `checkpointer.aget` → `channel_values.get("poliza_id")` → `try/except → None`.

The dispatch order is now:
```
HMAC -> parse -> dedup -> cartera-allowlist -> rate_limit
    -> client-allowlist -> firewall -> graph
```

Fail-open implemented at two levels: `check_rate_limit` itself fails open on Redis errors (05-03); the outer `try/except` in `_dispatch_message` provides a second belt-and-suspenders layer.

Rate-limited messages receive `T_RATE_LIMITED` via `meta.send_text` inside its own `try/except` (send failure only logs). Return after sending — firewall/graph never reached.

### Task 2: outbound_sent / outbound_blocked audit capture in _send_outbound

Two `audit_log.emit_task` calls added:

1. **`outbound_blocked`** — emitted in the output-firewall block branch after the Chatwoot note, before `return`. Payload: `{wamid_in, reason}`.

2. **`outbound_sent`** — emitted after the send try-block, gated on `sent=True` flag. The flag is set inside the try-block for each branch (text/buttons/list) only if the send call succeeds. Payload: `{wamid_in, kind, text_sha256}` where `text_sha256 = sha256(text_for_mirror)` binds the audit row to the exact delivered text without storing PII.

`conversation_id` uses `_hash_phone(phone)` in both events — no raw phone in audit rows (CLAUDE.md PII invariant). This differs from 05-04 which uses the LangGraph thread_id; the difference is documented in a code comment.

The mirror enqueue order is preserved exactly (no functional reordering).

## Deviations from Plan

None — plan executed exactly as written. The `_RLAllow` inline sentinel class used for the outer fail-open path (when `check_rate_limit` itself raises) is an implementation detail not specified in the plan, but follows the "belt-and-suspenders" instruction literally.

## Test Results

| Test | Result |
|------|--------|
| `test_rate_limited_sends_t_rate_limited_and_returns` | PASS |
| `test_rate_limit_allowed_continues_dispatch` | PASS |
| `test_cartera_number_never_calls_check_rate_limit` | PASS |
| `test_duplicate_message_never_calls_check_rate_limit` | PASS |
| `test_rate_limit_exception_fails_open` | PASS |
| `test_peek_poliza_id_returns_poliza_from_checkpoint` | PASS |
| `test_peek_poliza_id_returns_none_when_no_checkpointer` | PASS |
| `test_peek_poliza_id_returns_none_when_checkpointer_errors` | PASS |
| `test_outbound_sent_audit_emitted_on_successful_send` | PASS |
| `test_outbound_blocked_audit_emitted_on_firewall_block` | PASS |
| `test_outbound_sent_not_emitted_when_send_raises` | PASS |
| `test_outbound_sent_payload_contains_correct_text_sha256` | PASS |

Full suite: **394 passed, 11 deselected** (baseline was 382).

## Verification Checks

- `grep -n "check_rate_limit" app/webhooks/meta.py` — shows at line 662, between cartera branch (line 630 return) and `is_echo_allowed` (line 692)
- `grep -c "emit_task" app/webhooks/meta.py` — returns 2 (outbound_blocked + outbound_sent)
- `ruff check app/webhooks/meta.py tests/test_webhooks_meta_f5.py` — All checks passed
- `black --check app/webhooks/meta.py tests/test_webhooks_meta_f5.py` — All files unchanged

## Self-Check: PASSED

Files exist:
- `app/webhooks/meta.py` — modified with rate limit + audit capture
- `tests/test_webhooks_meta_f5.py` — created, 12 tests

Commits:
- `1ac0a71` — test(05-06): RED phase, 12 failing tests
- `0e13bf0` — feat(05-06): GREEN phase, implementation + lint cleanup
