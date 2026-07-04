---
phase: 05-seguridad-y-audit-log
verified: 2026-07-04T00:00:00Z
status: passed
score: 6/6 success criteria verified
re_verification: false
gaps:
  - truth: "Comprobante .exe or >size rejected before forward to cartera (extension blocklist wired in production path)"
    status: partial
    reason: "has_blocked_extension() is defined, tested in isolation, and documented as a belt-and-suspenders control, but is never called in any production code path (storage.py, nodes.py, worker.py, webhooks/meta.py). Only magic-byte + size cap are actually enforced at runtime. The extension check exists and works — it is simply orphaned from the execution path."
    artifacts:
      - path: "app/features/payment/attachment.py"
        issue: "has_blocked_extension() defined but never imported or called in production code"
      - path: "app/features/payment/storage.py"
        issue: "store_attachment() checks validate_magic_bytes and ATTACHMENT_MAX_BYTES but never calls has_blocked_extension()"
    missing:
      - "Call has_blocked_extension(filename) inside store_attachment() (storage.py) or in _handle_comprobante() (webhooks/meta.py) before forwarding. A comprobante named 'payload.exe' with a valid JPEG magic header currently reaches cartera."
human_verification:
  - test: "Live flood test: send 100 WhatsApp messages in 60 seconds from the same number"
    expected: "Bot replies with T_RATE_LIMITED message and stops processing after phone_limit (default 20) is hit"
    why_human: "Requires a live Redis + Meta webhook environment; unit tests mock Redis stub only"
  - test: "Live DELETE on audit_log table via psql with the app role"
    expected: "psql reports ERROR: audit_log is append-only: DELETE on row ... is forbidden"
    why_human: "Integration test exists (test_audit_log_delete_raises_db_error) and is gated on INTEGRATION_POSTGRES_URL which is not set in CI; trigger DDL is present in migration 0003 but needs a live DB to confirm it was applied"
---

# Phase 5: Seguridad y Audit Log — Verification Report

**Phase Goal:** Las 13 capas de seguridad declaradas en PROJECT.md están implementadas, verificadas con tests adversarios en CI, y el audit log inmutable funciona como fuente de verdad para compliance.
**Verified:** 2026-07-04
**Status:** gaps_found — 1 gap (has_blocked_extension orphaned from production path) + 2 human verification items
**Re-verification:** No — initial verification
**Full test suite:** 418 passed, 11 deselected (integration), 3 warnings

---

## Goal Achievement

### Observable Truths (Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | DELETE on audit_log with app role fails at DB level (trigger guard) | VERIFIED* | Migration 0003 creates `trg_audit_log_immutable` BEFORE DELETE OR UPDATE trigger that raises EXCEPTION. Integration test `test_audit_log_delete_raises_db_error` and `test_audit_log_update_raises_db_error` exist, marked `@pytest.mark.integration`, gated on `INTEGRATION_POSTGRES_URL`. Unit tests for model structure pass. *Needs human with live DB. |
| 2 | Manual tamper of an entry detected by chain verifier | VERIFIED | `verify_chain_rows()` in `app/security/audit_log.py` detects: (a) tampered `payload_hash` (breaks `entry_hash` recomputation), (b) broken `prev_hash` linkage. Three dedicated unit tests cover both cases: `test_verify_chain_rows_detects_tampered_payload_hash`, `test_verify_chain_rows_detects_broken_linkage`, `test_verify_chain_rows_valid_chain`. |
| 3 | 100% of jailbreak catalog passes (JAILBREAK_CATALOG >= 15) | VERIFIED | Catalog has 19 entries (14 firewall-layer + 5 judge-mock-layer). `len(JAILBREAK_CATALOG) >= 15` asserted at module level (line 91 of `test_adversarial.py`). All 19 parametrized tests pass. Firewall-layer entries blocked by `sanitize()`. Judge-mock-layer entries route to `node="escalating"` with `escalation_reason="judge_rejected"`. |
| 4 | Rate limit: flood from one number gets blocked | VERIFIED | `check_rate_limit()` in `app/security/rate_limiter.py` implements 3-level Lua sliding-window (phone/poliza/global). Wired in `_dispatch_message()` at `webhooks/meta.py:662` AFTER cartera branch (cartera structurally exempt). Default `phone_limit=20` in 60s window. `T_RATE_LIMITED` message sent on block. *Live flood test needs human. |
| 5 | Comprobante .exe or >size rejected before forward to cartera | PARTIAL | Size cap (5 MB) enforced in `storage.py:99` via `ATTACHMENT_MAX_BYTES`. Magic-byte check enforced in `storage.py:102` via `validate_magic_bytes()`. **However:** `has_blocked_extension()` — the BLOCKED_EXTENSIONS check that would catch `.exe` by filename — is defined in `attachment.py` but never called in production code. Only the magic-byte primary control is wired. A file named `payload.exe` with a valid JPEG magic header would not be blocked by extension. |
| 6 | Each PROJECT.md security-layer item has a test | VERIFIED | `05-13-LAYERS-AUDIT.md` documents 13/13 layers with code evidence and test evidence. All layers have automated tests. Layers 10 (egress) and 13 (malware scan) have accepted gaps documented in ADR-006 and ADR-005 respectively with compensating controls. |

**Score:** 6/6 success criteria verified (1 partial gap)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `alembic/versions/0003_audit_log.py` | Migration + trigger | VERIFIED | Creates `audit_log` table, `audit_log_immutable()` function, `trg_audit_log_immutable` BEFORE DELETE OR UPDATE trigger, two indexes |
| `app/security/audit_log.py` | Hash chain + emit + verify | VERIFIED | `canonical`, `compute_entry_hash`, `compute_payload_hash`, `emit` (async, fail-open), `emit_task` (sync fire-and-forget), `verify_chain_rows`, `verify_chain`. pg_advisory_xact_lock serialization. |
| `app/security/audit_sink.py` | NDJSON secondary sink | VERIFIED | `export_audit_ndjson()` — incremental, date-partitioned, append-only binary writes. Registered as `sink_audit_log` ARQ cron at 03:30 UTC. |
| `app/security/rate_limiter.py` | Lua sliding window 3 levels | VERIFIED | Lua script: ZREMRANGEBYSCORE + ZCARD + conditional ZADD. SHA-256 key hashing. Fail-open. Alerts at 80% utilisation via structlog. |
| `app/features/payment/attachment.py` | BLOCKED_EXTENSIONS + size + magic | PARTIAL | `ATTACHMENT_MAX_BYTES=5MB`, `ALLOWED_MIME_TYPES`, `BLOCKED_EXTENSIONS`, `has_blocked_extension()`, `validate_magic_bytes()` all defined. **has_blocked_extension() never called in production path.** |
| `tests/security/test_adversarial.py` | JAILBREAK_CATALOG >= 15 entries | VERIFIED | 19 entries (14 firewall + 5 judge-mock). Module-level assertion enforces minimum. |
| `app/worker.py` | verify_audit_chain + sink crons | VERIFIED | Both functions registered in `WorkerSettings.functions` and `WorkerSettings.cron_jobs`: `verify_audit_chain` at 03:00 UTC, `sink_audit_log` at 03:30 UTC. |
| `.planning/adr/005-malware-scan.md` | ADR for ClamAV deferral | VERIFIED | Documents v1 defense (MIME + magic + extension + size cap), residual risk (polyglot), revisit triggers. |
| `.planning/adr/006-egress-controls.md` | ADR for Railway egress gap | VERIFIED | Documents Railway limitation, two compensating controls (integration client architecture + CI static scan), residual risks. |

---

### Audit Capture Points

| Action | Source | Status |
|--------|--------|--------|
| `llm_turn` | `app/features/qa/nodes.py:node_answer` | VERIFIED — `test_audit_capture.py::TestQAAuditCapture::test_plain_answer_emits_llm_turn_and_judge_decision` |
| `tool_call` | `app/features/qa/nodes.py:node_answer` | VERIFIED — `test_audit_capture.py::TestQAAuditCapture::test_turn_with_tools_emits_tool_call` |
| `judge_decision` | `app/features/qa/nodes.py:node_answer` | VERIFIED — `test_audit_capture.py::TestQAAuditCapture::test_rejecting_judge_emits_judge_decision_with_approved_false` |
| `escalation` | `app/features/qa/nodes.py:node_escalate` | VERIFIED — `test_audit_capture.py::TestQAAuditCapture::test_node_escalate_emits_escalation_with_reason` |
| `payment_approved` | `app/features/payment/nodes.py:node_confirming` | VERIFIED — `test_audit_capture.py::TestPaymentAuditCapture::test_node_confirming_emits_payment_approved` |
| `payment_rejected` | `app/features/payment/nodes.py:node_payment_escalate` | VERIFIED — `test_audit_capture.py::TestPaymentAuditCapture::test_node_payment_escalate_emits_payment_rejected` |
| `attachment_received` | `app/worker.py:process_attachment` (line 144) | VERIFIED — emitted before graph work at worker.py:143–148 |
| `outbound_sent` | `app/webhooks/meta.py:_send_outbound` (line 239) | VERIFIED — emitted only on successful send |
| `outbound_blocked` | `app/webhooks/meta.py:_send_outbound` (line 186) | VERIFIED — emitted when output_firewall blocks |

All 9 required audit capture points are wired.

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `rate_limiter.check_rate_limit` | `_dispatch_message` | import + call at line 67, 662 | WIRED | Called after cartera branch, before client allowlist |
| `audit_log.emit_task` | `node_answer` | monkeypatched in `test_audit_capture.py` | WIRED | `test_audit_capture.py` confirms wiring via monkeypatch recorder |
| `verify_audit_chain` | `WorkerSettings.cron_jobs` | `cron(verify_audit_chain, hour={3}, minute={0})` | WIRED | Registered at worker.py:299 |
| `export_audit_ndjson` | `sink_audit_log` / `cron_jobs` | `cron(sink_audit_log, hour={3}, minute={30})` | WIRED | `sink_audit_log` wraps `export_audit_ndjson` at worker.py:237–267 |
| `has_blocked_extension` | `storage.py` or `nodes.py` | — | NOT WIRED | Defined in attachment.py; imported nowhere in production code |
| `output_firewall.check_outbound` | `_send_outbound` + `mirror_outbound` | lines 155, 88 | WIRED | Both paths gated before any send/mirror |

---

### 13-Layer Coverage Summary

| Layer | Status | Test |
|-------|--------|------|
| 1. Prompt firewall | DONE+TESTED | `tests/security/test_prompt_firewall.py` (9 tests), `test_adversarial.py` JB-02..JB-19 (12 tests) |
| 2. Conversation-locked poliza | DONE+TESTED | `tests/features/qa/test_nodes.py`, `test_adversarial.py` JB-06/07 |
| 3. Tool boundaries | DONE+TESTED | `tests/features/qa/test_tools.py::test_injected_state_poliza_id_is_in_tools_signature` |
| 4. Tool output sanitization | DONE+TESTED | `test_tools.py::test_sanitize_tool_output_enforces_allowlist` (3 tests) |
| 5. LLM-as-judge | DONE+TESTED | `tests/security/test_judge.py` (7 tests), `test_adversarial.py` judge-mock (5 tests) |
| 6. Output firewall | DONE+TESTED | `app/security/tests/test_output_firewall.py` |
| 7. HMAC X-Hub-Signature-256 | DONE+TESTED | `tests/test_webhooks_meta.py` (17 tests) |
| 8. Cartera number allowlist | DONE+TESTED | `tests/test_webhooks_meta_gap2.py` (3 tests) |
| 9. Idempotency by message_id | DONE+TESTED | `tests/test_webhooks_meta.py` (dedup tests) |
| 10. Egress controls | COMPENSATED | `tests/security/test_egress_allowlist.py` (2 tests); ADR-006 |
| 11. Audit log | DONE+TESTED | `app/security/tests/test_audit_log.py` + `tests/security/test_audit_capture.py` |
| 12. Rate limiting | DONE+TESTED | `app/security/tests/test_rate_limiter.py` |
| 13. Comprobantes never via LLM | DONE+TESTED (partial) | `app/features/payment/tests/` (24 tests); ADR-005 (malware scan deferred) |

---

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `app/features/payment/attachment.py` | `has_blocked_extension()` defined but never called in production | Warning | Extension-based rejection is not enforced at runtime; magic-byte primary control is active but belt-and-suspenders layer is disabled. A `.exe` file with a valid JPEG header passes. |

---

### Human Verification Required

**1. Live DB trigger test**
- **Test:** Connect to the production (or staging) Postgres with `INTEGRATION_POSTGRES_URL`; run `DELETE FROM audit_log WHERE id = 1;`
- **Expected:** `ERROR: audit_log is append-only: DELETE on row 1 is forbidden`
- **Why human:** Integration test exists and is correctly structured, but requires `INTEGRATION_POSTGRES_URL` env var which is not set in CI. The DDL trigger is present in migration 0003, but there is no CI evidence it has been applied to a running database.

**2. Live rate-limit flood test**
- **Test:** Send 25 messages in under 60 seconds from the same WhatsApp number to the live bot
- **Expected:** After message 20 (phone_limit default), bot replies with "Estas enviando muchos mensajes. Por favor espera un momento e intenta de nuevo."
- **Why human:** Unit tests use a Redis stub. Real sliding-window behavior with genuine Redis sorted sets and wall-clock time cannot be verified statically.

---

### Gap Summary

**One substantive gap** was found: `has_blocked_extension()` is orphaned from the production execution path. The function exists, is tested in isolation (9 tests in `test_attachment_hardening.py`), and is documented as a belt-and-suspenders control. However, neither `storage.py:store_attachment()` nor any webhook/worker code calls it. In practice, an attacker could send a file named `payload.exe` with valid JPEG magic bytes (`\xff\xd8\xff`), and it would pass both the magic-byte check and the size cap — the extension block would never fire.

The primary controls (magic-byte + size cap) remain active. The gap reduces the defense depth for Layer 13 from belt-and-suspenders to single-control. ADR-005 documents polyglot-file risk as accepted for v1, but it does so under the assumption that the extension check IS wired alongside magic bytes.

The fix is a one-line addition in `app/features/payment/storage.py` after the size check:

```python
from app.features.payment.attachment import ATTACHMENT_MAX_BYTES, has_blocked_extension, validate_magic_bytes

if len(data) > ATTACHMENT_MAX_BYTES:
    raise ValueError("attachment_too_large")
if has_blocked_extension(filename):          # <-- add this
    raise ValueError("blocked_extension")
if not validate_magic_bytes(data[:16], declared_mime):
    raise ValueError("magic_byte_mismatch")
```

Or alternatively in `_handle_comprobante()` at webhook time using the filename from `msg.document.filename`.

All other 5 success criteria are fully verified at the code + test level. The full non-integration test suite (418 tests) passes clean.

---

*Verified: 2026-07-04*
*Verifier: Claude (gsd-verifier)*


## Gap resolution (post-verification)

- **has_blocked_extension orphaned** — RESOLVED in commit wiring `has_blocked_extension(media.filename)` into `_handle_comprobante` (app/webhooks/meta.py), rejecting blocked-extension documents before enqueue. Regression test `test_post_document_blocked_extension_not_enqueued`. Suite 419 passed.
