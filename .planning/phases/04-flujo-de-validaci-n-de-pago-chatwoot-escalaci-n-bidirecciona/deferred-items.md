# Phase 04 Deferred Items

## C901 complexity on `_send_outbound` (app/webhooks/meta.py)

**Discovered**: Plan 04-08, Task 1  
**Pre-existing**: Yes — complexity was already 11 > 10 before 04-08 changes (confirmed via git stash)  
**Ruff rule**: C901  
**Recommendation**: Extract the firewall gate block into `_check_and_escalate(app_state, phone, text, payment_approved, wamid)` helper function to reduce complexity back below threshold.  
**Priority**: Low (no functional impact, test suite green)
