---
gsd_state_version: 1.0
milestone: v21.0
milestone_name: milestone
status: phase-04-in-progress
last_updated: "2026-07-03T21:00:00.000Z"
progress:
  total_phases: 8
  completed_phases: 0
  total_plans: 31
  completed_plans: 18
  percent: 58
current_phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
current_plan: 04-05
stopped_at: "Completed 04-04-PLAN.md"
---

## Decisions

- (04-04) Payment nodes integrated directly into QA graph (vertical extension) — avoids cross-graph state serialisation complexity
- (04-04) Session DI via _session_factory_fn() asynccontextmanager — monkeypatch-friendly, no ABC required
- (04-04) cartera_phone_allowlist empty → escalate via Chatwoot with error log; v1 sends to first entry only

## Performance Metrics

| Phase | Plan | Duration (min) | Tasks | Files |
|-------|------|---------------|-------|-------|
| 04 | 04-04 | 90 | 2 | 7 |
