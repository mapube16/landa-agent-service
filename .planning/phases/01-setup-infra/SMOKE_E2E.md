# Phase 1 — End-to-end smoke verification

**Executed**: 2026-06-28
**Agent domain**: `https://landa-agent-service-production.up.railway.app`
**Chatwoot domain**: `https://chatwoot-production-d073.up.railway.app`
**Operator**: Maxi (LANDA Tech)
**Bot service commit at execution**: `11bed52` (`origin/main`)

> Custom domains `agent.landatech.org` + `chat.landatech.org` deferred to the end of Phase 1. Smoke uses Railway-provided `*.up.railway.app` URLs.

---

## Results

| SC | Criterion | Status | Evidence |
|---|---|---|---|
| SC1 | GET /health returns 200 + healthy | ✅ PASS | 5/5 retries `healthy`, all components OK. One initial flake (`openrouter` 1000.8 ms vs 1 s timeout) — see notes. |
| SC2 | POST /test/llm returns OpenRouter reply | ✅ PASS | `reply="pong"`, model=`google/gemini-2.5-pro` (real OpenRouter slug, not the `2.0-pro` from the original plan which OpenRouter rejects), 7225 ms latency. |
| SC3 | Chatwoot panel accessible + admin login + create inbox | ✅ PASS | Login confirmed (wizard, plan 01-06). Operator created API inbox `smoke-test` end-to-end: "¡Tu bandeja de entrada está lista!" — confirmed in UI, then deleted. |
| SC4 | LangSmith trace appears for /test/llm call | ✅ PASS | Verified via API: 1 `ChatOpenAI [success]` run in project `landa-agente-dpg` (workspace `Workspace 1`) at 23:55:36 UTC. |
| SC5 | Sentry receives synthetic error from /test/sentry | ✅ PASS | Operator confirmed visual: `transaction=app.main.test_sentry`, `handled=no`, trace `000ab284d23f4171b53a275a1fc7c3bd`. |

---

## SC1 — GET /health

**Command**:
```bash
curl -sS https://landa-agent-service-production.up.railway.app/health
```

**Response (a stable retry)**:
```json
{
  "status": "healthy",
  "components": {
    "postgres":      {"ok": true, "latency_ms": ~30-90},
    "redis":         {"ok": true, "latency_ms": ~10-25},
    "openrouter":    {"ok": true, "latency_ms": ~500-900},
    "langsmith_env": {"ok": true}
  },
  "version": "0.1.0",
  "env": "dev"
}
```

**Stability check** (5 sequential probes):

| Try | Status | OpenRouter latency |
|---|---|---|
| 1 | healthy | 872 ms |
| 2 | healthy | 625 ms |
| 3 | healthy | 542 ms |
| 4 | healthy | 700 ms |
| 5 | healthy | 783 ms |

**Known flake**: the first call of the smoke run reported `degraded` with `openrouter.latency_ms=1000.8 ms` (1 ms over the 1 s timeout). OpenRouter's median is ~700 ms but the tail occasionally crosses 1 s. Documented in `01-05-SUMMARY.md` as expected behavior; F5 may revisit the timeout budget when egress controls land. **Not a Phase 1 blocker.**

Result: **PASS**

---

## SC2 — POST /test/llm

**Command**:
```bash
curl -sS -X POST https://landa-agent-service-production.up.railway.app/test/llm \
  -H "Content-Type: application/json" \
  -d '{"text":"Responde con exactamente la palabra: pong. Marca trace smoke-2026-06-28-final"}'
```

**Response**:
```json
{
  "reply": "pong",
  "model": "google/gemini-2.5-pro",
  "role": "conversation",
  "latency_ms": 7225.5
}
```

> Plan 01-07's `must_haves` originally required `model=google/gemini-2.0-pro` but that slug **does not exist in OpenRouter's catalog** (validated via `/api/v1/models`, see plan 01-05 deviations). The real slug is `google/gemini-2.5-pro`. `app/config/settings.py`, `.env.example`, tests, and CLAUDE.md all realigned to the 2.5-* family in plan 01-05.

Result: **PASS**

---

## SC3 — Chatwoot panel accessible + admin login + create inbox

**Operator-verified steps** (plan 01-06):

| Step | Result |
|---|---|
| Navigate `https://chatwoot-production-d073.up.railway.app/` | 200 OK |
| `/installation/onboarding` → wizard | rendered, completed |
| Super-admin created (LANDA email + password) | confirmed in operator's password manager |
| First tenant account `DPG Seguros` | created via wizard |
| Re-login as admin → dashboard | confirmed |
| Create test inbox (any channel) → dashboard "Inboxes" → "Add inbox" → API channel → submit → confirm visible → delete | ✅ confirmed. API channel `smoke-test` created with placeholder webhook URL; success page "¡Tu bandeja de entrada está lista!" rendered; inbox subsequently deleted to leave Chatwoot idle. |

Result: **PASS**

---

## SC4 — LangSmith trace

**Verification via LangSmith API** (since the Railway agent's logs no longer show the `Failed to multipart ingest runs: 403` warnings after wiring `LANGSMITH_WORKSPACE_ID`):

```bash
curl -X POST -H "x-api-key: $LANGSMITH_API_KEY" \
  -H "X-Tenant-Id: 40a45125-1d79-4d2d-9120-d5f7bee3675b" \
  -H "Content-Type: application/json" \
  "https://api.smith.langchain.com/api/v1/runs/query" \
  -d '{"session":["f0fb637e-8db7-49e4-b744-d92771104ac7"],"limit":5,"order":"desc"}'
```

**Response (redacted)**:
```
1 runs found
  ChatOpenAI [success] @ 2026-06-28T23:55:36.131594
```

**Project**: `landa-agente-dpg` (not `landa-agent-dev` as the original plan assumed — operator-named project created in LangSmith UI when registering the account; env var `LANGSMITH_PROJECT` retargeted).

**Key insight that unblocked tracing**: the `lsv2_sk_*` API key (Service Key, ORG-scoped) requires a `X-Tenant-Id` header pointing to a specific workspace. The langsmith Python SDK reads `LANGSMITH_WORKSPACE_ID` and injects this header automatically. Without that env var, every trace ingest returned 403 even though basic API calls worked.

Result: **PASS**

---

## SC5 — Sentry synthetic error

**Trigger**:
```bash
curl -sS -X POST https://landa-agent-service-production.up.railway.app/test/sentry
# → HTTP 500 (expected — endpoint raises RuntimeError)
```

**Operator-verified in Sentry UI**:

| Field | Value |
|---|---|
| Project | `landa-agent-service` (org `landa-0m`) |
| `transaction` | `app.main.test_sentry` |
| `handled` | `no` (unhandled `RuntimeError`) |
| `level` | `error` |
| `url` | `http://landa-agent-service-production.up.railway.app/test/sentry` |
| Trace ID | `000ab284d23f4171b53a275a1fc7c3bd` |
| PII check | **clean** — no phone numbers, no API keys in request payload (verified by operator) |

Stack trace present, points to `app/main.py`. `scrub_sentry_event` filter from plan 01-02 is doing its job (no PII visible in frame locals, headers, or request body).

Result: **PASS**

---

## Phase 1 closure

All 5 success criteria PASS. **Phase 1 (Setup infra) is done.**

### What works now

- Repo scaffolded with vertical-slice structure (`app/{features,integrations,security,memory,models,webhooks,config}/`)
- Dependencies pinned in `pyproject.toml` + `uv.lock`
- Dockerfiles produce runtime images (no BuildKit cache mount; Railway's BuildKit fork rejected the upstream cache id syntax)
- Postgres + Redis provisioned on Railway with private networking (`*.railway.internal`)
- LangGraph `AsyncPostgresSaver` wired into FastAPI lifespan with explicit `__aenter__/__aexit__`
- alembic migration `0001` registered (stamped — the migration's `asyncio.run()` nesting bug is captured as a follow-up; the schema itself was idempotently created by `checkpointer.setup()`)
- Chatwoot self-hosted at `https://chatwoot-production-d073.up.railway.app` (custom domain `chat.landatech.org` deferred), admin login + tenant account `DPG Seguros`, idle
- LangSmith tracing active in project `landa-agente-dpg` (workspace `Workspace 1`) — keys: `LANGSMITH_API_KEY` rotated, `LANGSMITH_WORKSPACE_ID` set
- Sentry receives errors with PII-scrubbed payloads (`scrub_sentry_event` from plan 01-02)
- structlog JSON logs with `correlation_id` (via `asgi-correlation-id`) + PII redaction
- OpenRouter LLM factory `get_llm(role)` cached per role, models: `google/gemini-2.5-pro` (conversation) / `google/gemini-2.5-flash` (judge/intent/summarizer)
- `GET /health` verifies stack health across 4 components in parallel with 1 s budget each
- `POST /test/llm` round-trips through OpenRouter
- CI green: ruff + black + mypy `--strict` + pytest (12/12)

### What is explicitly NOT done (deferred)

- **Custom domains** `agent.landatech.org` + `chat.landatech.org` — DNS + Let's Encrypt work scheduled for end of Phase 1 milestone close
- **alembic migration `0001` body fix** — `asyncio.run()` nesting in `_apply_checkpointer_setup`; workaround = `alembic stamp head` + lifespan `checkpointer.setup()`. **Should land as a small backlog item.**
- **Chatwoot healthcheck path** — currently `/` (set post-onboarding via GraphQL workaround); `/api` is wrong for Chatwoot
- WhatsApp / Meta Cloud API integration (F2)
- SoftSeguros client (F2)
- LangGraph state machine for the bot (F3)
- KB content auditor (F3)
- Security pipeline (F3–F5)
- Audit log immutable (F5)
- lambda-proyect handoff (F6)
- Production cutover, real DPG traffic (F7)

### Next

```
/gsd-plan-phase 02-softseguros-meta-cloud
```

(Per ROADMAP — Phase 2: Integración SoftSeguros + WhatsApp Cloud API.)
