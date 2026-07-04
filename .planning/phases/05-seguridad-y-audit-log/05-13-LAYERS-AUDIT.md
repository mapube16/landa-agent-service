# 05-13 Layers Audit — Retrospective Security Checklist

**Phase:** 05-seguridad-y-audit-log
**Date:** 2026-07-04
**Auditor:** Execute-phase agent (05-07 closure)

This document maps each of the 13 PROJECT.md security layers to its code
implementation and automated test evidence.  Every layer is either
"implemented and tested" or explicitly deferred via ADR with a compensating
control.

Status values:
- DONE+TESTED: code exists and an automated test verifies the behavior
- DEFERRED: gap accepted for v1, documented in an ADR with compensating control
- COMPENSATED: full infra control not available; app-level control + ADR

---

## Audit Table

| # | Layer | Status | Code evidence | Test evidence | Notes |
|---|-------|--------|--------------|---------------|-------|
| 1 | Prompt firewall (length, control chars, Unicode normalization, OWASP pattern match) | DONE+TESTED | `app/security/prompt_firewall.py:sanitize` — 5-step pipeline, 12 patterns, NFKC normalization | `tests/security/test_prompt_firewall.py` (9 tests) + `tests/security/test_adversarial.py` JB-02..JB-19 firewall entries (12 tests) | Also re-exported via `app/security/input_sanitizer.py:sanitize_input` (CLAUDE.md structure gap closed) |
| 2 | Conversation-locked poliza (poliza_id in state, not from LLM generation) | DONE+TESTED | `app/features/qa/nodes.py:node_choose_policy` locks `poliza_id` in state; `node_answer` reads from `state["poliza_id"]` not from LLM output | `tests/security/test_adversarial.py::test_judge_mock_escalates_on_malicious_llm` (5 tests; cross-poliza leak JB-06/JB-07 route to escalation via judge) + `tests/features/qa/test_nodes.py` | poliza_id injected via LangGraph `InjectedState("poliza_id")` in every tool signature |
| 3 | Tool boundaries (no list_all, no search_*, allowlist per graph state) | DONE+TESTED | `app/features/qa/tools.py:_TOOLS` — three tools only: `get_saldo`, `get_estado`, `get_coberturas`. All scoped to `poliza_id` from state via `InjectedState` | `tests/features/qa/test_tools.py::test_injected_state_poliza_id_is_in_tools_signature` + `tests/features/qa/test_nodes.py` | No `list_all` or `search_*` tool exists. Pydantic schemas enforce strict I/O |
| 4 | Tool output sanitization (field allowlist + injection pattern stripping) | DONE+TESTED | `app/features/qa/tools.py:sanitize_tool_output` — SALDO_ALLOWLIST / ESTADO_ALLOWLIST / COBERTURAS_NESTED_ALLOWLIST; `_INJECTION_STRIP` regex strips `system:`, `instruction:`, `assistant:`, `<|...|>` patterns from all string values | `tests/features/qa/test_tools.py::test_sanitize_tool_output_enforces_allowlist` + `::test_sanitize_tool_output_strips_injection_pattern` + `::test_sanitize_tool_output_none_returns_empty_dict` (3 tests) | RESEARCH marked as PARTIAL; direct code inspection confirms field allowlist + injection pattern strip are both present. DONE. |
| 5 | LLM-as-judge on every outgoing message (8-flag rubric) | DONE+TESTED | `app/security/judge.py:judge_response` + `is_approved` — 8-flag `JudgeRubric` (is_in_scope, leaks_other_polizas, affirms_payment_without_cartera_approval, factually_grounded, no_jailbreak_echo, no_pii_leak, no_external_links, sentiment_appropriate) wired in `node_answer` | `tests/security/test_judge.py` (7 tests) + `tests/security/test_adversarial.py` judge-mock layer (5 tests: JB-01/06/07/08/09) | audit_log captures judge_decision per RESEARCH Pattern 3 |
| 6 | Output firewall (deterministic hardcoded patterns; payment-confirmed path gate) | DONE+TESTED | `app/security/output_firewall.py` — D-28 payment confirmation guard; wired at top of `_send_outbound` and `mirror_outbound` in `app/webhooks/meta.py` | `app/security/tests/test_output_firewall.py` (tested via test_output_firewall) | Audit capture added in 05-06 via emit_task(action="outbound_sent") |
| 7 | HMAC X-Hub-Signature-256 on every Meta webhook | DONE+TESTED | `app/webhooks/meta.py:_verify_signature` — `hmac.compare_digest(expected, header_value)` per D-16; every inbound Meta request passes through before any parsing | `tests/test_webhooks_meta.py` — includes HMAC rejection tests (17 tests total) | D-16 invariant: timing-safe comparison always used |
| 8 | Cartera number allowlist (frozenset, E.164, lru_cache) | DONE+TESTED | `app/webhooks/meta.py:_get_cartera_allowlist` — frozenset from settings, `lru_cache(maxsize=1)`, E.164 normalized; cartera branch is first check in `_dispatch_message` | `tests/test_webhooks_meta_gap2.py` — 3 tests covering cartera allowlist + routing | Messages from non-allowlisted cartera numbers are rejected silently |
| 9 | Idempotency by message_id (Redis nx, 24h TTL) | DONE+TESTED | `app/webhooks/meta.py:_dispatch_message` — `key = f"wa:msg:{msg.id}".encode(); first_see = await redis.set(key, b"1", nx=True, ex=86400)` | `tests/test_webhooks_meta.py` — dedup test cases covered in webhook test suite | Returns 200 OK on duplicate (Meta redelivery safe) |
| 10 | Egress controls (SoftSeguros + Meta + Chatwoot + OpenRouter + LangSmith only) | COMPENSATED | All outbound HTTP through four named integration clients; base URLs from `app/config/settings.py` env-driven settings. `app/integrations/meta_cloud.py:META_BASE_URL` is the only hardcoded external URL (constant API endpoint). | `tests/security/test_egress_allowlist.py::test_no_hardcoded_external_host_outside_allowlist` + `::test_allowed_egress_hosts_contains_required_entries` (2 tests) | Network-level egress not available on Railway hobby plan — app-level compensating controls documented in ADR-006. CI scan catches new hardcoded URLs. |
| 11 | Audit log (append-only Postgres + hash chain + S3/volume sink) | DONE+TESTED | `app/security/audit_log.py:emit` + `emit_task` — SHA-256 hash chain, pg_advisory_xact_lock serialization, fail-open; wired in `node_answer`, `_send_outbound`, `process_attachment`, `node_escalate` | `app/security/tests/test_audit_log.py` (hash chain unit tests) + `tests/security/test_audit_capture.py` (capture point integration tests) | alembic/versions/0003_audit_log.py implements trigger guard for append-only at DB level |
| 12 | Rate limiting (per-phone, per-poliza, global; sliding window Redis Lua) | DONE+TESTED | `app/security/rate_limiter.py:check_rate_limit` — three levels, sliding window Lua script, sha256-truncated Redis keys; wired in `_dispatch_message` AFTER cartera branch (cartera exempted by dispatch order) | `app/security/tests/test_rate_limiter.py` (rate limiter unit + mock Redis tests) | Cartera numbers are structurally exempt (rate_limit check follows cartera branch in dispatch order) |
| 13 | Comprobantes never through LLM vision (relayed directly to cartera) | DONE+TESTED | `app/features/payment/attachment.py` — MIME allowlist, magic-byte check, BLOCKED_EXTENSIONS, 5 MB cap; `app/worker.py:process_attachment` — routes to payment graph, not to LLM with vision; `app/features/payment/storage.py` enforces validate_magic_bytes + size check at storage layer | `app/features/payment/tests/test_attachment.py` (8 tests, existing) + `app/features/payment/tests/test_attachment_hardening.py` (16 new tests: BLOCKED_EXTENSIONS + MZ magic + regression) + ADR-005 (malware scan deferral) | ADR-005 documents ClamAV deferral. Meta re-scans media on delivery. |

---

## Gap Closure Actions Taken in This Phase (05-07)

| Action | Layer | Files | Evidence |
|--------|-------|-------|---------|
| Created `app/security/input_sanitizer.py` (thin re-export) | Layer 1 | `app/security/input_sanitizer.py` | Closes CLAUDE.md structure diagram gap (RESEARCH OQ-4); no logic duplication |
| Added `BLOCKED_EXTENSIONS` + `has_blocked_extension()` to `attachment.py` | Layer 13 | `app/features/payment/attachment.py` | Belt-and-suspenders extension check alongside magic-byte primary control |
| Created `test_attachment_hardening.py` | Layer 13 | `app/features/payment/tests/test_attachment_hardening.py` | 16 tests: extension blocklist, MZ magic rejection, size cap constant, regression |
| Created `test_egress_allowlist.py` (static CI scan) | Layer 10 | `tests/security/test_egress_allowlist.py` | App-level compensating control for Railway egress gap |
| Created ADR-005 (malware scan deferral) | Layer 13 | `.planning/adr/005-malware-scan.md` | Documents v1 attachment defense + revisit triggers |
| Created ADR-006 (egress controls) | Layer 10 | `.planning/adr/006-egress-controls.md` | Documents Railway limitation + compensating controls |

---

## Deferred Items

None structural.  All gaps identified during this audit were either:
1. Closed inline (input_sanitizer.py, BLOCKED_EXTENSIONS, egress scan), or
2. Accepted for v1 with ADR documentation (malware scan, network egress).

Layer 4 (tool output sanitization): RESEARCH marked this PARTIAL, but direct code
inspection confirms `sanitize_tool_output` implements both field allowlisting AND
injection pattern stripping (`_INJECTION_STRIP` regex). The three existing tests in
`tests/features/qa/test_tools.py` verify all three behaviors. DONE.

---

## Verdict

**13/13 layers implemented; 2 with app-level compensating controls documented in ADR
(Layer 10 ADR-006, Layer 13 partial — ClamAV ADR-005).**

All 13 PROJECT.md security layers have at least one automated test verifying the
core behavior.  Infra-limited and deferred deliverables are explicitly documented
with revisit triggers.

---

## Evidence Commands

```bash
# Full suite (includes all 13-layer test coverage)
uv run pytest -m "not integration" --tb=no -q

# Layer-specific verifications
uv run pytest tests/security/test_prompt_firewall.py -q                          # Layer 1
uv run pytest tests/security/test_adversarial.py -m "not integration" -q        # Layers 1,2,5
uv run pytest tests/features/qa/test_tools.py -q                                 # Layers 3,4
uv run pytest tests/security/test_judge.py -q                                    # Layer 5
uv run pytest app/security/tests/test_output_firewall.py -q                      # Layer 6
uv run pytest tests/test_webhooks_meta.py -q                                     # Layers 7,8,9
uv run pytest tests/test_webhooks_meta_gap2.py -q                                # Layer 8
uv run pytest tests/security/test_egress_allowlist.py -q                         # Layer 10
uv run pytest app/security/tests/test_audit_log.py tests/security/test_audit_capture.py -q  # Layer 11
uv run pytest app/security/tests/test_rate_limiter.py -q                         # Layer 12
uv run pytest app/features/payment/tests/ -q                                     # Layer 13

# ADR existence check
python -c "from pathlib import Path; a=Path('.planning/adr/005-malware-scan.md').read_text(encoding='utf-8'); b=Path('.planning/adr/006-egress-controls.md').read_text(encoding='utf-8'); assert 'ClamAV' in a and 'Railway' in b; print('ADRs OK')"
```

*Audit date: 2026-07-04*
