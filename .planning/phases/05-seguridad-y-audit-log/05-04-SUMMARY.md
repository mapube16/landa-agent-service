---
phase: 05-seguridad-y-audit-log
plan: 04
subsystem: security/audit
tags: [audit-log, graph-nodes, qa, payment, tdd, sec-04]
dependency_graph:
  requires: [05-01]
  provides: [audit capture in QA and payment graph nodes]
  affects: [app/features/qa/nodes.py, app/features/payment/nodes.py]
tech_stack:
  added: []
  patterns: [module-import monkeypatch, fire-and-forget emit_task, TDD RED-GREEN]
key_files:
  created:
    - tests/security/test_audit_capture.py
  modified:
    - app/features/qa/nodes.py
    - app/features/payment/nodes.py
decisions:
  - module-import (from app.security import audit_log) used so a single monkeypatch of audit_log.emit_task covers all call sites in each module
  - judge_decision emitted before branching on approval so rejection path is audited without a separate call site
  - conv_id derived from thread_id|conversation_id at node_answer entry to avoid re-read per emit
metrics:
  duration: ~35min
  completed: 2026-07-04
  tasks_completed: 2
  files_modified: 3
---

# Phase 05 Plan 04: Graph Audit Capture Summary

Implements SEC-04 audit capture at every critical decision point inside the LangGraph nodes: each LLM turn, tool call, judge decision, escalation, and cartera payment decision now emits a hash-chained audit event through `audit_log.emit_task` from Plan 05-01.

## What Was Built

**QA graph hooks (app/features/qa/nodes.py):**

| Action | Hook point | Actor | Payload |
|--------|-----------|-------|---------|
| `llm_turn` | after `llm.ainvoke` returns | `bot` | `model_role`, `response_sha256`, `has_tool_calls` |
| `tool_call` | inside tool execution block, before ToolNode.ainvoke | `bot` | `tools` (comma-joined names) |
| `judge_decision` | after `judge_response` returns rubric | `judge` | `approved` + `flag_*` booleans flattened from rubric |
| `escalation` | entry to `node_escalate` | `bot` | `reason` |

**Payment graph hooks (app/features/payment/nodes.py):**

| Action | Hook point | Actor | Payload |
|--------|-----------|-------|---------|
| `payment_approved` | after case status update in `node_confirming` | `cartera` | `case_id`, `status="approved"` |
| `payment_rejected` | after Chatwoot note in `node_payment_escalate` | `cartera` | `case_id`, `status="escalated"` |

**Test coverage (tests/security/test_audit_capture.py):**

11 tests (all passing), covering:
- Plain answer turn emits llm_turn + judge_decision in correct order
- Actor and conversation_id/poliza_id set from state fields
- Tool execution path emits tool_call with tool names
- Rejecting judge emits judge_decision with approved=False
- node_escalate emits escalation with reason from state
- No float or nested dict in any QA payload
- Fail-open: node_answer completes without session_factory (unpatched emit_task)
- node_confirming emits payment_approved with actor=cartera
- conversation_id derives from wa_phone/thread_id
- node_payment_escalate emits payment_rejected
- No float or nested dict in payment payloads

## Architecture Decisions

**Module-level import pattern:** `from app.security import audit_log` (not `from app.security.audit_log import emit_task`) allows a single `monkeypatch.setattr(audit_mod, "emit_task", recorder.emit_task)` to cover all call sites in the module under test. This is the idiomatic pattern for testable fire-and-forget singletons.

**emit_task placement:** Called inline (not awaited, not try/except wrapped) immediately after the operation it captures. Plan 05-01 guarantees emit_task never raises and swallows all exceptions internally.

**No PII in payloads:** All message content is SHA-256 hashed before entering the payload; rubric booleans are emitted as `flag_*` keys without the free-text `rationale`. Consistent with CLAUDE.md no-PII-persistence rule.

**judge_decision before branching:** Audit emitted after `judge_response` returns but before the `if rubric is None or not is_approved` branch — this ensures both approved and rejected turns are captured in a single call site.

## Deviations from Plan

None — plan executed exactly as written.

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 057d36e | test | TDD RED: failing audit capture tests for QA and payment nodes |
| c450519 | feat | TDD GREEN: audit hooks in QA graph nodes (llm_turn, tool_call, judge_decision, escalation) |
| d28bd46 | feat | audit hooks in payment nodes (payment_approved, payment_rejected) |

## Verification

- `grep -c "emit_task" app/features/qa/nodes.py` = 4 (>= 4 required)
- `grep -c "emit_task" app/features/payment/nodes.py` = 2 (>= 2 required)
- `pytest tests/security/test_audit_capture.py -q` = 11 passed
- `pytest -m "not integration"` = 382 passed, 0 failures (baseline was 350 + 11 new + 21 from other plans)
- `ruff check` and `black --check` both clean on all three files

## Self-Check: PASSED

- tests/security/test_audit_capture.py: EXISTS
- app/features/qa/nodes.py: EXISTS, contains `from app.security import audit_log`
- app/features/payment/nodes.py: EXISTS, contains `from app.security import audit_log`
- Commits 057d36e, c450519, d28bd46: VERIFIED in git log
