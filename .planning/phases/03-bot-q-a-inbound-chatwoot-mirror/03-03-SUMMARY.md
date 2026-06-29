---
phase: 03-bot-q-a-inbound-chatwoot-mirror
plan: "03"
subsystem: integrations + worker
tags:
  - chatwoot
  - arq
  - mirror
  - httpx
  - redis-cache
dependency_graph:
  requires:
    - 03-01  # ChatwootSettings + chatwoot.py skeleton
  provides:
    - app/integrations/chatwoot.py::ChatwootClient (post_message, get_or_create_conversation, mark_resolved)
    - app/integrations/chatwoot.py::get_chatwoot_client (lru_cache singleton factory)
    - app/worker.py::mirror_inbound (ARQ job)
    - app/worker.py::mirror_outbound (ARQ job)
    - tests/integrations/test_chatwoot.py (7 tests)
    - tests/test_worker.py (4 tests)
  affects:
    - app/worker.py (WorkerSettings.functions: _noop dropped, mirror_inbound + mirror_outbound added)
    - tests/ (new tests/integrations/ subdirectory)
key_files:
  modified:
    - app/integrations/chatwoot.py   # skeleton -> full implementation
    - app/worker.py                  # _noop removed, mirror jobs added
  created:
    - tests/integrations/__init__.py
    - tests/integrations/test_chatwoot.py
    - tests/test_worker.py
decisions:
  - "ChatwootClient.__init__ adds redis: Any | None = None param (plan spec) -- factory sets None; lifespan in 03-05 late-binds via app.state.chatwoot._redis = app.state.redis"
  - "get_or_create_conversation split into 3 private helpers (_create_or_get_contact, _search_contact, _create_conversation) to keep public method readable and test-friendly"
  - "api_access_token header in get_chatwoot_client factory (NOT Authorization: Bearer) per RESEARCH Pattern 5 + Pitfall 4"
  - "mirror_inbound/mirror_outbound use local import of get_chatwoot_client to avoid circular deps + keep cold-start light"
  - "All ARQ kwargs are str primitives (phone, text, wamid) -- ARQ Pitfall 6 compliance enforced by test_mirror_functions_signature_uses_primitive_kwargs_only"
metrics:
  duration: "~45 minutes"
  completed: "2026-06-29"
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 2
---

# Phase 03 Plan 03: ChatwootClient + ARQ Mirror Jobs Summary

Plan 03-03 implements the full `ChatwootClient` integration replacing the 03-01
skeleton stubs, and wires two ARQ jobs (`mirror_inbound`, `mirror_outbound`) that
async-mirror every WhatsApp message to the Chatwoot API Channel inbox. The
lifespan wireup (Chatwoot client + ARQ pool binding) is intentionally deferred
to Plan 03-05 to avoid partial state if any Wave 2 plan fails.

## Tasks Completed

| # | Task | Files |
|---|------|-------|
| 1 | ChatwootClient implementation + factory + tests | app/integrations/chatwoot.py, tests/integrations/__init__.py, tests/integrations/test_chatwoot.py |
| 2 | ARQ worker mirror_inbound + mirror_outbound + tests | app/worker.py, tests/test_worker.py |

## What Was Built

### Task 1: ChatwootClient

**`app/integrations/chatwoot.py`** -- replaces 03-01 skeleton stubs with full implementation:

**`ChatwootClient.__init__(self, http, account_id, redis=None)`**
- Redis optional -- factory leaves `None`; lifespan in 03-05 binds via `app.state.chatwoot._redis = app.state.redis`

**`ChatwootClient.post_message(conversation_id, content, message_type)`**
- POSTs to `/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages`
- Payload: `{"content": content, "message_type": "incoming"|"outgoing"}`
- Logs `content_len` only (never raw content -- T-03-03-01 mitigation)

**`ChatwootClient.get_or_create_conversation(phone)`**
- Redis cache key `chatwoot:conv:{phone_hash}` with 7-day TTL
- Cache hit: returns `int(cached.decode())` directly (no HTTP calls)
- Cache miss: two-step create -- POST /contacts -> POST /conversations
- 422 on duplicate contact recovered via GET /contacts/search
- Cache failure bypasses cleanly (bypass-on-cache-down, BLE001 pattern)
- Uses `_hash_phone` from `meta_cloud.py` (import, no redeclaration)

**`ChatwootClient.mark_resolved(conversation_id)`**
- POSTs to `/api/v1/accounts/{account_id}/conversations/{conversation_id}/toggle_status`
- Body: `{"status": "resolved"}`

**`get_chatwoot_client()`** factory (`@lru_cache(maxsize=1)`)
- Header: `{"api_access_token": settings.chatwoot.api_key.get_secret_value()}`
- NOT `Authorization: Bearer` (RESEARCH Pattern 5 + Pitfall 4)
- `httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=30.0)`
- `httpx.Timeout(10.0, connect=3.0, read=10.0, write=3.0, pool=2.0)`

**Chatwoot API response shapes confirmed:**
- Contact create: `{"payload": {"contact": {"id": <int>, ...}, ...}}`
- Conversation create: `{"id": <int>, "status": "open", ...}`
- Contact search: `{"payload": [{"id": <int>, ...}, ...]}`

### Task 2: ARQ Worker Jobs

**`app/worker.py`** -- drops `_noop`, adds two mirror jobs:

```python
async def mirror_inbound(ctx, *, phone: str, text: str, wamid: str) -> None
async def mirror_outbound(ctx, *, phone, text: str, wamid: str) -> None
```

Both jobs:
1. `get_chatwoot_client()` (singleton via lru_cache)
2. `await chatwoot.get_or_create_conversation(phone)` -> conv_id
3. `await chatwoot.post_message(conv_id, text, message_type="incoming"|"outgoing")`

All kwargs are `str` primitives -- ARQ Pitfall 6 compliance (JSON-serializable).

**`WorkerSettings.functions = [mirror_inbound, mirror_outbound]`** -- `_noop` removed.

## Tests

| Test file | Count | Covers |
|-----------|-------|--------|
| `tests/integrations/test_chatwoot.py` | 7 | post_message incoming/outgoing, cache hit/miss, mark_resolved, api_access_token header, singleton identity |
| `tests/test_worker.py` | 4 | WorkerSettings.functions membership, mirror_inbound incoming type, mirror_outbound outgoing type, ARQ Pitfall 6 primitive kwargs |

## Security Posture (03-PLAN.md threat register)

| Threat | Mitigation |
|--------|-----------|
| T-03-03-01: Phone/content in logs | `_hash_phone` for phone, `content_len` only (never raw content) |
| T-03-03-02: API key in error repr | `SecretStr` renders `**********`; httpx does not log header values |
| T-03-03-03: Pydantic model as ARQ kwarg | `test_mirror_functions_signature_uses_primitive_kwargs_only` fails build on violation |
| T-03-03-04: Full response on raise_for_status | `raise_for_status()` raises `HTTPStatusError`; callers log `type(exc).__name__` only |
| T-03-03-05: Chatwoot down -> queue accumulation | Accept -- mirror is non-critical; ARQ 3-retry default; 03-05 makes mirror fire-and-forget |
| T-03-03-06: conv_id cache key derivable | Accept -- Redis behind Railway internal network |

## What Is NOT Done Here (deferred to 03-05)

- `app/main.py` lifespan wireup: ARQ pool create, `app.state.chatwoot = get_chatwoot_client()`, late-bind `app.state.chatwoot._redis = app.state.redis`
- `app/webhooks/meta.py` ARQ enqueue calls (`arq.enqueue_job("mirror_inbound", ...)`)
- Chatwoot client teardown in lifespan finally block

## Deviations from Plan

None. All must_haves implemented verbatim. The `get_or_create_conversation` was split into 3 private helpers for readability -- this is an additive improvement, not a deviation.
