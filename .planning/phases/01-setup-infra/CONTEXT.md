# Phase 1 — Setup infra: Context

**Status**: Pending
**Mode**: YOLO, coarse, parallel planning enabled
**Generated**: 2026-06-27

## Locked decisions (from discuss + research)

### Railway plan & deployment topology

- **Plan**: Railway **Hobby** ($5/mo + usage), 8GB RAM + 8 vCPU shared per project
- **Topology**: Single Railway project with two service groups
  - **Group A — landa-agent-service**: FastAPI app + ARQ worker + Postgres + Redis
  - **Group B — chatwoot**: Rails + Sidekiq + Chatwoot-Postgres + Chatwoot-Redis (separate from agent's stores)
- **Risk acknowledged**: Chatwoot can burst to 4GB under load; total project budget is 8GB. If memory pressure hits in prod, upgrade to Pro ($20/mo). v1 traffic expectation is low enough that Hobby is acceptable.
- **Private networking** between services within the project: yes, free, use Railway internal DNS

### Build & deployment

- **Build tool**: Custom **multi-stage Dockerfile** with `python:3.12-slim` + uv (NOT Nixpacks — maintenance mode — NOT Railpack — beta)
- Target: cached builds <15s, final image ~80MB
- One Dockerfile for agent FastAPI service, one for ARQ worker, both share base layer
- Chatwoot uses the community Railway template `chatwoot-all-in-one-pgvector` as starting point (DB integrated; we may split later if needed)

### Dependency management

- **`pyproject.toml` + `uv.lock`** (NOT `requirements.txt`)
- uv as the package manager throughout (`uv sync`, `uv lock`, `uv run`)
- CLAUDE.md to be updated in this phase to reflect this (replaces the temporary `requirements.txt` reference in the quick-start section)

### Observability

- **LangSmith free tier** for v1 (5,000 traces/mo, 14-day retention)
- Acknowledged cap: ~1,600 WhatsApp messages/mo at 3 LLM calls per turn (conversation + judge + intent classifier). Equivalent to ~50 msg/day
- **If volume exceeds cap**: upgrade to LangSmith Plus ($39/mo, 10K traces) — escala a ~100 msg/día. Migrar a Langfuse self-hosted solo si privacy de DPG lo exige
- **LangSmith project naming**: `landa-agent-{env}` → `landa-agent-dev`, `landa-agent-staging`, `landa-agent-prod`

### Stack versions (verified against PyPI on 2026-06-27)

Pin these exactly in `pyproject.toml`:

| Package | Version |
|---|---|
| Python | 3.12 |
| FastAPI | 0.138.1 |
| LangGraph | 1.2.6 |
| langgraph-checkpoint-postgres | 3.1.0 |
| langchain-openai | 1.3.3 |
| pydantic | 2.13.4 |
| asyncpg | 0.31.0 |
| psycopg | 3.3.4 |
| sqlalchemy | 2.0.51 |
| alembic | 1.18.5 |
| structlog | 26.1.0 |
| sentry-sdk | 2.63.0 |
| redis | 8.0.1 |
| ruff | 0.15.20 |
| mypy | 2.1.0 |

### LangGraph + Postgres integration

- `AsyncPostgresSaver` must live in FastAPI's `lifespan` context manager with explicit `__aenter__`/`__aexit__` (NOT the documented `async with` block — that traps long-running servers)
- **Two separate Postgres connection pools required**:
  - asyncpg via SQLAlchemy 2.0 → for app data (case_store, debtor_flags, audit_log)
  - psycopg 3 → for LangGraph checkpointer
- Both pools point to the same Postgres database; different connections, different drivers

### Logging & correlation

- **`asgi-correlation-id` middleware** to generate `X-Request-ID` per request
- Request ID flows into:
  - structlog via `bind_contextvars`
  - Sentry as `transaction_id` (automatic)
  - LangSmith trace metadata (so the same request_id correlates across all three)
- **PII redaction**: hybrid approach — key-name redaction (more reliable, redact by field name like `phone`, `cedula`, `monto`) + targeted regex (phone numbers in free text). Comprehensive PII redaction (Presidio-class) is deferred to Phase 5 (Security + audit log)

### Out of scope for Phase 1

- WhatsApp / Meta Cloud API integration (Phase 2)
- SoftSeguros client (Phase 2)
- LangGraph nodes / tools / state machines for the bot (Phase 3)
- KB content auditor (Phase 3, stub in Phase 1 OK)
- Security pipeline implementation — placeholders OK, real wiring in their respective phases
- Audit log schema with hash chain (Phase 5)
- Voice handoff (Phase 6)
- Production cutover, smoke tests, runbooks (Phase 7)

## Open items (acknowledged, not blockers)

1. **PII redaction completeness**: F1 establishes the pattern (key-name + targeted regex). Full Presidio-class redaction is F5 work.
2. **DPG volume estimate**: not yet provided. v1 ships on free tier; monitor LangSmith usage in F7 and upgrade if needed.
3. **Chatwoot scaling**: Hobby plan accepted as v1 default. Mark a runbook item in F7 to monitor memory pressure and upgrade trigger.

## Success criteria for Phase 1 (from ROADMAP)

- `GET /health` responde 200 con info de Postgres + Redis + LangSmith conectados
- Endpoint dummy invoca `get_llm("conversation").ainvoke("ping")` y devuelve respuesta de OpenRouter
- Chatwoot panel accesible en `chat.landatech.org`, admin login funcional, posible crear inbox manualmente
- Trace de la llamada dummy aparece en LangSmith
- Error sintético llega a Sentry
