---
gsd_state_version: 1.0
milestone: v18.0
milestone_name: milestone
status: unknown
last_updated: "2026-07-04T15:16:26.579Z"
progress:
  total_phases: 8
  completed_phases: 1
  total_plans: 36
  completed_plans: 29
---

## Decisions

- (04-04) Payment nodes integrated directly into QA graph (vertical extension) — avoids cross-graph state serialisation complexity
- (04-04) Session DI via _session_factory_fn() asynccontextmanager — monkeypatch-friendly, no ABC required
- (04-04) cartera_phone_allowlist empty → escalate via Chatwoot with error log; v1 sends to first entry only
- (04-05) LangGraph Command(resume=value) preferred over aupdate_state+ainvoke for interrupt() resumption
- (04-05) _get_cartera_allowlist() uses lru_cache(maxsize=1) — same pattern as get_meta_client()
- (04-05) Cartera branch inserted BEFORE client allowlist in _dispatch_message (D-06, T-04-05-01)
- [Phase 04-06]: business_minutes_between uses day-by-day loop (60-day bound) over WORKDAY_BLOCKS; int minutes via integer division
- [Phase 04-06]: _now_utc and _get_settings_payment are module-level callables for monkeypatching without touching real singletons
- [Phase 04-06]: cron(check_pending_cases, minute=set(range(60))) fires every minute; off-hours gate bails immediately so overhead is negligible
- [Phase 04]: D-28 gate placed at top of _send_outbound (early return on block); payment_approved forwarded as ARQ primitive kwarg for mirror re-check
- [Phase 05-01]: metadata_json Python attribute maps SQL 'metadata' column (SQLAlchemy reserved attr conflict); AuditPayload RootModel rejects floats/nested; emit/emit_task fully fail-open; pg_advisory_xact_lock serializes chain inserts (v1); pre-existing mypy errors in app/features/payment/ are deferred (out of scope)
- [Phase 05-02]: JB-01 (Spanish ignore variant) classified judge_mock — Spanish does not trigger English-only firewall patterns; integration tests gated on INTEGRATION_LLM not OPENROUTER_API_KEY (conftest always sets the latter to fake); judge_retries=1 in test state forces single-shot escalation
- [Phase 05-06]: Cartera exemption via structural ordering: rate_limit placed AFTER cartera branch in _dispatch_message — no explicit allowlist needed, exemption is a dispatch-order property
- [Phase 05-06]: outbound_sent gated on sent=True flag (set inside try-block per branch) — does not reorder mirror enqueue, minimal restructuring of _send_outbound

## Performance Metrics

| Phase | Plan | Duration (min) | Tasks | Files |
|-------|------|---------------|-------|-------|
| 04 | 04-04 | 90 | 2 | 7 |
| 04 | 04-05 | 45 | 2 | 4 |
| Phase 04 P06 | 9 | 2 tasks | 4 files |
| Phase 04 P04-08 | 45 | 2 tasks | 8 files |
| 05 | 05-01 | 23 | 3 | 8 |
| 05 | 05-02 | 30 | 2 | 2 |
| 05 | 05-03 | 22 | 2 | 2 |
| 05 | 05-04 | 35 | 2 | 3 |
| 05 | 05-05 | 30 | 2 | 3 |
| Phase 05 P06 | 52 | 2 tasks | 2 files |

