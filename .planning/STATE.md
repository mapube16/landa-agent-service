---
gsd_state_version: 1.0
milestone: v21.0
milestone_name: milestone
status: phase-04-in-progress
last_updated: "2026-07-03T22:00:00.000Z"
progress:
  total_phases: 8
  completed_phases: 0
  total_plans: 31
  completed_plans: 19
  percent: 61
current_phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
current_plan: 04-06
stopped_at: "Completed 04-05-PLAN.md"
---

## Decisions

- (04-04) Payment nodes integrated directly into QA graph (vertical extension) — avoids cross-graph state serialisation complexity
- (04-04) Session DI via _session_factory_fn() asynccontextmanager — monkeypatch-friendly, no ABC required
- (04-04) cartera_phone_allowlist empty → escalate via Chatwoot with error log; v1 sends to first entry only
- (04-05) LangGraph Command(resume=value) preferred over aupdate_state+ainvoke for interrupt() resumption
- (04-05) _get_cartera_allowlist() uses lru_cache(maxsize=1) — same pattern as get_meta_client()
- (04-05) Cartera branch inserted BEFORE client allowlist in _dispatch_message (D-06, T-04-05-01)

## Performance Metrics

| Phase | Plan | Duration (min) | Tasks | Files |
|-------|------|---------------|-------|-------|
| 04 | 04-04 | 90 | 2 | 7 |
| 04 | 04-05 | 45 | 2 | 4 |
