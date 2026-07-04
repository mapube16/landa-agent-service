---
phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
plan: "04"
subsystem: payment
tags: [payment, comprobantes, langgraph, interrupt, storage, arq, cartera]
dependency_graph:
  requires: [04-01, 04-02, 04-03]
  provides: [storage.py, nodes.py, payment graph in qa graph, process_attachment ARQ job]
  affects: [app/features/qa/graph.py, app/worker.py, app/features/qa/state.py]
tech_stack:
  added: []
  patterns: [TDD red-green, module-level provider for DI, asynccontextmanager session, os.replace atomic write]
key_files:
  created:
    - app/features/payment/storage.py
    - app/features/payment/tests/test_storage.py
    - app/features/payment/tests/test_nodes.py
  modified:
    - app/features/payment/nodes.py
    - app/features/payment/graph.py
    - app/features/qa/graph.py
    - app/features/qa/state.py
    - app/worker.py
decisions:
  - "Payment nodes integrated directly into QA graph (vertical extension) per RESEARCH Open Question #1 — avoids cross-graph state serialisation complexity"
  - "Session dependency injected via module-level _session_factory_fn() returning an asynccontextmanager — clean monkeypatch target for tests without needing ABC"
  - "wamid sanitised via regex stripping non-[A-Za-z0-9_-] chars before use in filename (T-04-04-03 path traversal mitigation)"
  - "cartera_phone_allowlist empty → escalate via Chatwoot with structlog error; v1 sends to first entry only (ponytail comment documents multi-cartera ADR needed for Phase 5+)"
metrics:
  duration_minutes: 90
  completed_date: "2026-07-03"
  tasks_completed: 2
  files_modified: 7
---

# Phase 04 Plan 04: Payment Subgraph + Storage Helper Summary

Storage helper with deterministic path + atomic write and 5 payment nodes (receive/forward/await/confirm/escalate) wired into the QA graph with an ARQ async job for comprobante processing.

## Objective

Implement the payment subgraph and inbound comprobante pipeline: download from Meta CDN with magic-byte gate, store to Railway volume, persist case + attachment rows, forward to cartera with buttons on the last file, and suspend at `node_awaiting_cartera` via LangGraph `interrupt()`. Wire the entry router so the QA graph dispatches into the payment flow when state says so.

## What Was Built

### Task 1: storage.py (prerequisite — not in repo, built here)

`app/features/payment/storage.py`:
- `build_attachment_path(case_id, wamid, mime, now)` — deterministic path at `<volume>/<case_id>/<ts>-<wamid>.<ext>`, wamid sanitised to prevent path traversal (T-04-04-03)
- `store_attachment(data, case_id, wamid, mime)` — size gate (5 MB), magic-byte gate, atomic write via `os.replace` from `.partial` sibling
- Raises `ValueError("attachment_too_large")` or `ValueError("magic_byte_mismatch")` — no disk write on gate failure

12 tests in `test_storage.py` — all green.

### Task 2: 5 payment nodes + build_payment_subgraph + ARQ job

**`app/features/payment/nodes.py`** — 5 async LangGraph node functions:

- `node_receive_comprobante`: downloads from Meta CDN, magic-byte gate via `store_attachment`, inserts `cases` + `attachments` rows. D-07: reuses open case. D-09: opens new case if prior is terminal. Rejection text sent to client on gate failure; returns `awaiting_receipt` without DB mutation (D-27: bytes never reach LLM).
- `node_forward_to_cartera`: D-13 outside-hours short-circuit (ack to client, defer to `work_hours_due_at`). During hours: `upload_media` + `send_media` for each attachment, buttons only on last (`aprobar|{case_id}`, `rechazar|{case_id}`, `info|{case_id}`). Saves `cartera_message_wamid`.
- `node_awaiting_cartera`: `interrupt({"waiting_for":"cartera_tap","case_id":...})` — graph suspends, checkpoint persisted. On resume: routes to approved/escalated/awaiting based on action.
- `node_confirming`: sets `payment_approved=True` in AIMessage `additional_kwargs` + state (D-28 — ONLY node allowed to emit this flag). Updates `cases.status="approved"`.
- `node_payment_escalate`: `get_or_create_conversation` on Chatwoot + private note for agent. Updates `cases.status="escalated"`.

**`app/features/payment/graph.py`** — updated with `build_payment_subgraph()`:
- `StateGraph(QAState)` with all 5 nodes, receive→forward→awaiting linear chain, conditional edges from awaiting (approved/escalated/info-loop).

**`app/features/qa/graph.py`** — entry router extended:
- `_route_entry` dispatches to `NODE_RECEIVE_COMPROBANTE` when `payment_status in {"awaiting_receipt","forwarded","awaiting_cartera"}` or `_inbound_media` present
- All 5 payment nodes + their edges added to `build_qa_graph()`

**`app/features/qa/state.py`** — new fields added:
- `_inbound_media: NotRequired[dict[str, str] | None]` — transient media payload from ARQ job
- `cliente_nombre: NotRequired[str | None]` — for D-08 caption

**`app/worker.py`** — `process_attachment` ARQ job:
- Resolves or builds compiled graph with checkpointer
- Injects `_inbound_media` + `payment_status="awaiting_receipt"` into state via `aupdate_state`
- Calls `ainvoke(None, config)` — graph runs to `interrupt()` at `node_awaiting_cartera` and suspends
- Appended to `WorkerSettings.functions`

8 tests in `test_nodes.py` — all green.

## Verification Evidence

```
253 passed, 3 warnings in 14.73s
```

```
ruff check: All checks passed!
black --check: 0 files to reformat
```

Spot checks:
- `grep interrupt nodes.py` >= 1
- `grep payment_approved.*True nodes.py` >= 1
- `grep process_attachment worker.py` = 3 (function def + docstring ref + functions list)
- Python import check: `from app.features.payment.graph import NODE_RECEIVE_COMPROBANTE; ... print('OK')` — OK

## Deviations from Plan

### Auto-built Issues

**1. [Rule 3 - Missing file] storage.py not present in repo**
- **Found during:** Task 1 verification
- **Issue:** Objective stated Task 1 was "already complete and merged to main" but `storage.py` did not exist in the repo (previous 3 agent sessions were interrupted before commit)
- **Fix:** Implemented `storage.py` + `test_storage.py` from scratch per plan spec before proceeding to Task 2
- **Files modified:** `app/features/payment/storage.py`, `app/features/payment/tests/test_storage.py`
- **Commit:** 2b57324

**2. [Rule 1 - Pattern] Session DI via asynccontextmanager factory**
- **Found during:** Task 2 implementation
- **Issue:** Plan spec shows `_session_factory_fn` as an async generator; LangGraph node context required an async context manager pattern compatible with monkeypatching
- **Fix:** `_session_factory_fn()` returns an `asynccontextmanager` result; tests patch it with a factory that yields mock session. All tests pass.

## Commits

| Hash | Message |
|------|---------|
| 2b57324 | feat(04-04): storage helper — deterministic path + atomic write |
| dd21234 | test(04-04): add failing tests for payment nodes (TDD RED) |
| 0559415 | feat(04-04): 5 payment nodes — receive/forward/await/confirm/escalate |
| 7adf3f7 | feat(04-04): payment subgraph + QA entry router + ARQ process_attachment |
| 03e7c0f | chore(04-04): ruff + black clean — fix unused var, long lines, import sort |

## Next Plan

Plan 04-05: cartera button-tap handler — receives the `aprobar|{case_id}` button reply from cartera, calls `graph.aupdate_state` to resume the interrupt, drives `node_confirming` or `node_payment_escalate`.

## Self-Check

See self-check section below.
