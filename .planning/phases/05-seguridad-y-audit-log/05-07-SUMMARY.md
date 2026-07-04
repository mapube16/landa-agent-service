---
phase: 05-seguridad-y-audit-log
plan: 07
subsystem: security
tags: [audit, attachment, egress, adr, retrospective, tdd]
dependency_graph:
  requires: [05-01, 05-02, 05-03, 05-04, 05-05, 05-06]
  provides: [13-layer-audit-doc, attachment-hardening, egress-ci-guard, adr-005, adr-006, input-sanitizer]
  affects: [app/features/payment/attachment.py, app/security/input_sanitizer.py, tests/security/test_egress_allowlist.py]
tech_stack:
  added: []
  patterns: [TDD-red-green, belt-and-suspenders-validation, static-analysis-CI-guard, ADR-decision-documentation]
key_files:
  created:
    - app/security/input_sanitizer.py
    - app/features/payment/tests/test_attachment_hardening.py
    - tests/security/test_egress_allowlist.py
    - .planning/adr/005-malware-scan.md
    - .planning/adr/006-egress-controls.md
    - .planning/phases/05-seguridad-y-audit-log/05-13-LAYERS-AUDIT.md
  modified:
    - app/features/payment/attachment.py
decisions:
  - "[05-07] BLOCKED_EXTENSIONS as belt-and-suspenders alongside magic-byte primary control (RESEARCH Pattern 7)"
  - "[05-07] 5 MB attachment cap intentionally conservative vs ROADMAP 10 MB ceiling; documented in attachment.py and ADR-005"
  - "[05-07] input_sanitizer.py as thin re-export of prompt_firewall.sanitize (no logic duplication, satisfies CLAUDE.md structure contract)"
  - "[05-07] Egress CI guard via static regex scan of app/ — compensating control for Railway hobby plan network gap (ADR-006)"
  - "[05-07] ClamAV deferred to v2; v1 attachment defense = MIME allowlist + magic-byte + BLOCKED_EXTENSIONS + 5 MB cap (ADR-005)"
  - "[05-07] Layer 4 (tool output sanitization) re-verified as DONE — RESEARCH PARTIAL status was incorrect; field allowlist + injection strip both present in tools.py"
metrics:
  duration: ~25 minutes
  completed: 2026-07-04
  tasks_completed: 3
  tasks_total: 3
  files_created: 6
  files_modified: 1
  tests_added: 24
  tests_before: 394
  tests_after: 418
---

# Phase 5 Plan 7: Phase Closure — 13-Layer Audit + Attachment Hardening + ADRs Summary

**One-liner:** BLOCKED_EXTENSIONS + egress CI scan + ADR-005/006 + 13/13-layer audit with file+test evidence per layer closes Phase 5 security posture.

---

## What Was Built

### Task 1: Gap Fixes (TDD)

**`app/security/input_sanitizer.py`** — Thin re-export (`from app.security.prompt_firewall import sanitize as sanitize_input`) that satisfies the CLAUDE.md structure diagram without duplicating logic (RESEARCH Open Question 4).

**`app/features/payment/attachment.py`** — Added:
- `BLOCKED_EXTENSIONS: Final[frozenset[str]]` — 10 executable/script suffixes (.exe, .js, .sh, .bat, .cmd, .dll, .msi, .scr, .ps1, .jar)
- `has_blocked_extension(filename: str | None) -> bool` — case-insensitive suffix check, None-safe
- Updated `ATTACHMENT_MAX_BYTES` comment to document intentional conservative choice vs ROADMAP 10 MB ceiling

**`app/features/payment/tests/test_attachment_hardening.py`** — 16 new tests across 4 classes: TestBlockedExtensions (13 tests), TestMzMagicBytesRejected (3 tests), TestOversizePayload (3 tests), TestRegressionValidInput (3 tests).

**`tests/security/test_egress_allowlist.py`** — Static regex scan of `app/**/*.py` for hardcoded `https?://` literals. Every hostname must be in `ALLOWED_EGRESS_HOSTS` (SoftSeguros, Meta, Chatwoot, OpenRouter, LangSmith, Sentry) or be a localhost/test placeholder. 2 tests; CI compensating control for Railway egress gap.

**Commit:** `a6b8ed4`

### Task 2: ADR-005 + ADR-006

**`.planning/adr/005-malware-scan.md`** — Documents ClamAV deferral to v2. v1 defense = MIME allowlist + magic-byte check + BLOCKED_EXTENSIONS + 5 MB cap. Revisit triggers: >100 attachments/day, threat incident, Railway plan upgrade.

**`.planning/adr/006-egress-controls.md`** — Documents Railway hobby plan has no VPC egress firewall. App-level compensating controls: (a) all outbound HTTP through four named integration clients with env-driven base URLs, (b) CI static egress scan in `tests/security/test_egress_allowlist.py`. Residual risk documented (supply-chain compromise not blocked at network level).

**Commit:** `e4eb6f7`

### Task 3: 13-Layer Audit Checklist

**`.planning/phases/05-seguridad-y-audit-log/05-13-LAYERS-AUDIT.md`** — One row per layer with code evidence (file:symbol) and test evidence (file::test). Notable findings:

- Layer 4 (tool output sanitization): RESEARCH marked PARTIAL — direct code inspection confirms `sanitize_tool_output()` in `tools.py` implements both field allowlisting AND `_INJECTION_STRIP` regex. Status corrected to DONE+TESTED.
- Layer 10 (egress controls): COMPENSATED — app-level controls + ADR-006 + CI scan.
- Layer 13 (comprobantes never LLM): DONE+TESTED with new hardening tests + ADR-005.

**Verdict: 13/13 layers implemented; 2 with compensating controls documented in ADR.**

**Commit:** `b1f732f`

---

## Test Results

| Metric | Value |
|--------|-------|
| Tests before plan | 394 passed, 11 deselected |
| Tests after plan | 418 passed, 11 deselected |
| New tests added | +24 |
| Suite status | GREEN |

---

## Deviations from Plan

### Auto-fixed Issues

None.

### Scope Notes

Pre-existing ruff lint errors in unrelated files (test_output_firewall.py, test_meta_cartera_routing.py, test_worker_audit.py, test_case_store.py) were discovered during the lint check but are out of scope per deviation rules. They were logged to deferred-items understanding — not fixed in this plan.

Layer 4 status correction (PARTIAL → DONE+TESTED) was resolved by direct code inspection — no code changes were needed, only the audit documentation reflects the correct status.

---

## Decisions Made

1. `BLOCKED_EXTENSIONS` as belt-and-suspenders: magic-byte is the primary control; extension check is a secondary filter. Neither is redundant — they catch different attack vectors (content spoofing vs filename-based execution).
2. 5 MB cap kept at its existing conservative value with improved documentation rather than raising to ROADMAP 10 MB — reduces exposure window for the residual polyglot-file risk accepted in ADR-005.
3. `input_sanitizer.py` implemented as a thin re-export rather than a new module — no logic duplication, satisfies the structural contract in CLAUDE.md.
4. Egress CI guard implemented as a static scan (not a runtime httpx middleware) — zero runtime overhead, catches hardcoded URL additions in PR review.

---

## Self-Check: PASSED

All created files confirmed present on disk. All task commits confirmed in git log.

| Check | Result |
|-------|--------|
| app/security/input_sanitizer.py | FOUND |
| app/features/payment/attachment.py | FOUND |
| app/features/payment/tests/test_attachment_hardening.py | FOUND |
| tests/security/test_egress_allowlist.py | FOUND |
| .planning/adr/005-malware-scan.md | FOUND |
| .planning/adr/006-egress-controls.md | FOUND |
| .planning/phases/05-seguridad-y-audit-log/05-13-LAYERS-AUDIT.md | FOUND |
| Commit a6b8ed4 (Task 1) | FOUND |
| Commit e4eb6f7 (Task 2) | FOUND |
| Commit b1f732f (Task 3) | FOUND |
| Test suite (418 passed) | PASSED |
