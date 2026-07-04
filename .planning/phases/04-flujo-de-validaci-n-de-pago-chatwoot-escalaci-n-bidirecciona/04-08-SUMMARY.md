---
phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
plan: "08"
subsystem: security/output-firewall + integration-tests
tags:
  - output-firewall
  - security
  - payment-flow
  - integration-tests
  - e2e
dependency_graph:
  requires:
    - 04-01 (output_firewall.py skeleton + check_outbound function)
    - 04-02 (Meta media/templates)
    - 04-03 (Chatwoot bidirectional)
    - 04-04 (payment subgraph + nodes)
    - 04-05 (cartera routing + graph resume)
    - 04-06 (business-hours timers)
    - 04-07 (voice handoff endpoint)
  provides:
    - D-28: output firewall wired in all outbound dispatch paths
    - Integration tests verifying all 6 Phase 4 success criteria
  affects:
    - app/webhooks/meta.py (_send_outbound gated)
    - app/worker.py (mirror_outbound gated)
tech_stack:
  added: []
  patterns:
    - check_outbound called before every Meta send AND before every Chatwoot mirror
    - payment_approved flag forwarded from AIMessage.additional_kwargs into ARQ job kwargs
    - Escalation substitute replaces blocked text; Chatwoot private note carries fw_reason
key_files:
  created:
    - tests/integration/__init__.py
    - tests/integration/conftest.py
    - tests/integration/test_payment_e2e.py
    - tests/fixtures/sample.jpg
  modified:
    - app/webhooks/meta.py (_send_outbound: import + firewall gate block)
    - app/worker.py (mirror_outbound: payment_approved kwarg + gate)
    - app/security/tests/test_output_firewall.py (TestSendOutboundFirewallWiring added)
    - pyproject.toml (integration marker registered)
decisions:
  - "D-28 gate placed at the top of _send_outbound before any send call — early return on block prevents mirror_outbound enqueue of blocked text"
  - "payment_approved forwarded as explicit ARQ kwarg so mirror_outbound can re-check without re-reading AIMessage (ARQ jobs receive primitive types only)"
  - "Pre-existing C901 complexity on _send_outbound deferred to deferred-items — it existed before plan 04-08"
  - "Integration tests use minimal FastAPI app + AsyncMock (no real Postgres/Redis) — hermetic, fast, matches existing test patterns"
  - "Task 3 (live Railway smoke) is a human-verify checkpoint — automated tests cover the 6 criteria in CI; live smoke validates end-to-end with real WhatsApp numbers"
metrics:
  duration_min: 45
  completed_date: "2026-07-03"
  tasks_completed: 2
  tasks_total: 3
  files_changed: 8
---

# Phase 04 Plan 08: Output Firewall Wiring + Phase 4 Integration Tests Summary

**One-liner**: Wired deterministic `check_outbound` gate into Meta `_send_outbound` + ARQ `mirror_outbound` (D-28); added 6 Phase 4 end-to-end integration tests covering all ROADMAP success criteria.

## What Was Built

### Task 1: Wire check_outbound into _send_outbound + mirror_outbound

**Files modified**: `app/webhooks/meta.py`, `app/worker.py`

The output firewall gate is now active on both outbound dispatch paths:

1. **`_send_outbound` (app/webhooks/meta.py)**: `check_outbound(text, payment_approved=...)` runs before any `send_text` / `send_buttons` / `send_list` call. On block:
   - Logs `output_firewall.payment_blocked` with `reason`, `phone_hash`, `wamid_in`
   - Sends escalation substitute: "La revision requiere validacion adicional. Te conecto con un agente humano."
   - Opens Chatwoot conversation and posts private note with `fw_reason`
   - Returns early — `mirror_outbound` is NOT enqueued for the blocked text (T-04-08-03)

2. **`mirror_outbound` ARQ job (app/worker.py)**: Extended signature with `payment_approved: bool = False`. Checks `check_outbound` before posting to Chatwoot. Blocked messages are suppressed silently (log only, no Chatwoot post).

3. **ARQ enqueue site**: Updated to forward `payment_approved` from `AIMessage.additional_kwargs` so the worker can re-check without re-parsing the message.

### Task 2: Integration tests — 6 success criteria end-to-end

**Files created**: `tests/integration/__init__.py`, `tests/integration/conftest.py`, `tests/integration/test_payment_e2e.py`, `tests/fixtures/sample.jpg`

Six `@pytest.mark.integration` tests covering all Phase 4 ROADMAP success criteria:

| Test | Criterion | What it verifies |
|------|-----------|-----------------|
| `test_happy_path_approve` | 1+2 | Image → cartera forward → aprobar → client confirmation with payment_approved=True; D-27 (no LLM call) |
| `test_reject_path_escalates` | 3 | cartera rechazar → escalation message to client + Chatwoot conversation opened |
| `test_spoofed_cartera_number_silently_dropped` | 4 | Unknown sender → zero outbound, HTTP 200 |
| `test_chatwoot_agent_reply_relays_to_client` | 5 | Chatwoot agent_bot mirror NOT relayed; human agent text relayed once |
| `test_handoff_no_answer_dispatches_template` | 6 | lambda POST → template with D-20 params + D-21 quick-replies; idempotent second call |
| `test_output_firewall_blocks_hallucinated_confirmation` | D-28 | Hallucinated payment text blocked; escalation substitute sent; Chatwoot note contains `output_firewall.payment_blocked`; mirror not enqueued |

## Verification Results

```
296 passed, 3 warnings in 16s
```

Full suite: 287 (pre-plan) + 3 (TestSendOutboundFirewallWiring) + 6 (integration tests) = 296.

```
grep -rn "check_outbound" app/webhooks/ app/worker.py
# app/webhooks/meta.py:60:from app.security.output_firewall import check_outbound
# app/webhooks/meta.py:149:    allowed, fw_reason = check_outbound(text_content, payment_approved=...)
# app/worker.py:85:    from app.security.output_firewall import check_outbound
# app/worker.py:88:    allowed, fw_reason = check_outbound(text, payment_approved=payment_approved)
```

Gate wired in 2 paths (Meta send + Chatwoot mirror).

## Deviations from Plan

### Auto-fixed Issues

None — plan executed as written for Tasks 1 and 2.

### Pre-existing Issue (Deferred)

**[Out of scope - pre-existing] C901 complexity on `_send_outbound`**
- Found during: Task 1 (ruff check)
- Issue: `_send_outbound` had C901 complexity=11 > 10 BEFORE plan 04-08 changes (confirmed via git stash)
- Action: Deferred to `deferred-items.md` — adding the firewall gate increased complexity marginally but the issue predates this plan
- Deferred to: `deferred-items.md`

## Task 3: Checkpoint Status

Task 3 is a `checkpoint:human-verify` for the live Railway smoke. The 6 integration tests in CI are necessary but not sufficient — the Railway smoke with real WhatsApp numbers validates the end-to-end flow in production.

See `04-08-PLAN.md §Task 3` for the full smoke checklist. When complete, create `04-08-SMOKE.md` per the F3 pattern.

## Self-Check

**Created files exist:**
- [x] tests/integration/__init__.py
- [x] tests/integration/conftest.py
- [x] tests/integration/test_payment_e2e.py
- [x] tests/fixtures/sample.jpg

**Commits exist:**
- [x] 5afa4bd — feat(04-08): wire check_outbound
- [x] 1f31a7a — test(04-08): add Phase 4 e2e integration tests
- [x] fc92288 — fix(04-08): remove unused imports

## Self-Check: PASSED
