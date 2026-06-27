# PLAN-VERIFICATION.md — Phase 1: Setup infra

**Verifier:** gsd-plan-checker (pre-execution, goal-backward)
**Date:** 2026-06-27
**Phase dir:** `.planning/phases/01-setup-infra/`
**Files reviewed:** `PLAN.md`, `01-01..01-08-PLAN.md` (8 plans), `CONTEXT.md`, `ROADMAP.md`, `PROJECT.md`, `01-RESEARCH.md`, `CLAUDE.md`

---

## Overall verdict: PASS (with 1 minor WARNING + 6 pre-execution warnings)

The 8 plans, executed in the declared 5-wave order, will deliver the Phase 1 goal "infra del microservicio corriendo en Railway, lista para recibir código". Every ROADMAP success criterion and every locked CONTEXT.md decision has at least one task that implements it. Scope is held tightly to Phase 1 — no leakage into WhatsApp / SoftSeguros / LangGraph-bot / security pipeline / audit log territory. Version pins match CONTEXT.md D-05 exactly.

There is one **WARNING** worth surfacing for the planner: `app/worker.py` stub is referenced in the prose of plan 02 task 1 but not listed in that plan's `files_modified` frontmatter. It is not a blocker — the prose explicitly instructs creation — but tightening this would help the executor.

All other concerns are pre-execution warnings rather than plan defects.

---

## Per-criterion findings

### 1. Goal achievement — PASS

Executing 01-01 + 01-08 (W1) → 01-02 + 01-03 (W2) → 01-04 (W3) → 01-05 + 01-06 (W4) → 01-07 (W5) produces:

- Empty FastAPI repo with vertical-slice packages (01-01)
- Dockerfile + Settings + logging + Sentry init (01-02)
- DB engine + Redis pool + LangGraph checkpointer helper + alembic (01-03)
- `main.py` with lifespan + `/health` + `/test/llm` + `/test/sentry` + tests + `get_llm(role)` factory (01-04)
- Railway agent service group deployed and `/health` returning 200 (01-05)
- Chatwoot service group at `chat.landatech.org` idle (01-06)
- E2E smoke checklist signed off (01-07)

This is exactly the "infra corriendo en Railway, lista para recibir código" goal. No structural gap.

### 2. Success criteria coverage — PASS (5/5)

| ROADMAP success criterion | Covered by | Final check |
|---|---|---|
| `GET /health` 200 with Postgres + Redis + LangSmith info | 01-04 (endpoint), 01-03 (deps), 01-05 (Railway) | 01-07 SC1 |
| `POST /test/llm` invokes `get_llm("conversation").ainvoke("ping")` and returns OpenRouter reply | 01-04 task 2 + task 1, 01-05 (env vars) | 01-07 SC2 |
| Chatwoot at `chat.landatech.org`, admin login, create inbox | 01-06 task 2 (deploy + DNS + wizard) | 01-07 SC3 |
| LangSmith trace appears for dummy call | 01-02 task 2 (settings), 01-04 task 1 (auto-tracing), 01-05 (env on Railway) | 01-07 SC4 |
| Synthetic error reaches Sentry | 01-02 task 3 (init_sentry + scrubber), 01-04 task 2 (POST /test/sentry raises), 01-05 (SENTRY_DSN) | 01-07 SC5 |

### 3. Deliverables coverage — PASS (16/16)

Cross-checked the 16 ROADMAP deliverables against the table in PLAN.md and against each plan's `files_modified` / `<action>`. Every deliverable maps to at least one plan with a concrete task. Notable mappings:

- `app/config/llm.py` deliverable is satisfied through 01-04 task 1: factory lives in `app/integrations/openrouter.py` (per CLAUDE.md vertical-slice convention) and `app/config/llm.py` re-exports `get_llm`. This is an intentional, documented deviation called out in PLAN.md — both import paths work.
- LangGraph checkpointer deliverable is split: helper builder in 01-03, lifespan wiring in 01-04. Coverage is complete because 01-04 explicitly depends on 01-03.

### 4. Locked decisions respected — PASS

| Decision | Honored? |
|---|---|
| OpenRouter only (no Anthropic/OpenAI SDK direct) | YES — 01-04 task 1 uses ChatOpenAI(base_url=openrouter); 01-01 explicitly excludes langchain-anthropic, openai SDK, and python-dotenv from deps |
| Vertical slice, no ABCs/Ports | YES — 01-01 task 2 materializes the structure exactly; integrations are concrete classes only |
| uv + pyproject.toml + uv.lock (no requirements.txt) | YES — 01-01 task 1 generates pyproject.toml and uv lock; 01-08 updates CLAUDE.md; fallback uv pip compile documented but not committed |
| Custom multi-stage Dockerfile (not Nixpacks / not Railpack) | YES — 01-02 task 1 + RESEARCH.md Dockerfile recipe; railway.toml in 01-05 documents builder = DOCKERFILE intent |
| Chatwoot self-hosted | YES — 01-06 uses chatwoot-all-in-one-pgvector template, idle in F1 |
| LangSmith free tier, project landa-agent-{env} | YES — 01-02 task 2 sets default landa-agent-dev; 01-05 sets LANGSMITH_PROJECT=landa-agent-dev |
| Railway Hobby, single project two service groups | YES — 01-05 (group A: agent), 01-06 (group B: chatwoot). 01-06 task 1 adds an explicit reconfirmation checkpoint before deploy |
| Version pins exact (CONTEXT.md table) | YES — see criterion 10 below |
| Two separate Postgres pools (asyncpg + psycopg) | YES — 01-03 task 1 (asyncpg via SQLAlchemy), 01-03 task 2 (psycopg via AsyncPostgresSaver), 01-04 task 2 wires both into lifespan |
| __aenter__/__aexit__ explicit | YES — 01-04 task 2 main.py lifespan does await cp_cm.__aenter__() / __aexit__ explicitly |
| asgi-correlation-id + PII redaction | YES — 01-02 task 3 logging.py with PII_KEYS + PHONE_RE; 01-04 main.py adds CorrelationIdMiddleware |
| send_default_pii=False + before_send scrubber | YES — 01-02 task 3 init_sentry() |

### 5. Parallelization correctness — PASS

Wave graph is consistent with depends_on:

- W1: 01-01 (deps=[]) parallel with 01-08 (deps=[]) — disjoint files (01-01 creates pyproject.toml etc.; 01-08 only touches CLAUDE.md). No conflict.
- W2: 01-02 (deps=[01-01]) parallel with 01-03 (deps=[01-01]) — 01-02 touches Dockerfile*, .dockerignore, app/config/{settings,logging,observability}.py; 01-03 touches app/config/{db,redis,checkpointer}.py, alembic/. Verified disjoint.
- W3: 01-04 alone — consumes outputs of 01-02 and 01-03; cannot parallelize.
- W4: 01-05 (deps=[01-04]) parallel with 01-06 (deps=[01-04]) — separate Railway service groups, disjoint files. Different human checkpoints. No conflict.
- W5: 01-07 (deps=[01-05, 01-06]) — pure smoke verification, only touches SMOKE_E2E.md.

No cycles. No forward references. Wave numbers match max(deps)+1.

### 6. Atomicity of plans — PASS

Each plan is a single logical commit:

- 01-01 = repo skeleton + tooling configs + CI
- 01-02 = Docker + Settings + log/obs config
- 01-03 = persistence wiring + alembic
- 01-04 = app entry + endpoints + tests
- 01-05 = Railway agent group deploy
- 01-06 = Railway Chatwoot group deploy
- 01-07 = E2E verification
- 01-08 = CLAUDE.md edit

No plan smuggles unrelated concerns. 01-02 looks dense (3 tasks: docker + settings + logging+sentry) but they are all the "config foundation" layer that the rest of F1 consumes; splitting would force 01-04 to depend on three separate plans instead of one. Acceptable cohesion.

### 7. Verifiability — PASS

Every `<task type="auto">` has a runnable `<verify><automated>` command. Spot-checks:

- 01-01 task 1: uv sync --frozen plus smoke import — runs.
- 01-02 task 1: builds two Dockerfiles and asserts image size <120MB — runs.
- 01-02 task 3: scripted Python that exercises redact_pii, scrub_sentry_event, init_sentry — runs.
- 01-04 task 2: imports app.main:app and asserts routes are registered — runs.
- 01-07 task 1: curl /health + curl /test/llm against real Railway domain — runs.

Human-gated tasks (01-05 task 1, 01-06 tasks 1+2, 01-07 task 2) carry explicit `<resume-signal>` blocks describing what the operator must paste back. No magic verification.

### 8. Critical reminders honored — PASS

PLAN.md "Critical reminders for executors" lists 8 items; all are explicitly enforced in the underlying plans:

| Reminder | Where enforced |
|---|---|
| No requirements.txt | 01-01 task 1 (pyproject.toml + uv.lock); 01-08 (CLAUDE.md edit) |
| No @app.on_event | 01-04 task 2 (lifespan); 01-02 RESEARCH.md State of the Art cited inline |
| AsyncPostgresSaver explicit __aenter__/__aexit__ | 01-04 task 2 main.py lifespan code; 01-03 task 2 helper builder |
| Two pools Postgres (asyncpg + psycopg) | 01-03 task 1 (asyncpg/SQLAlchemy); 01-03 task 2 (psycopg via checkpointer); threat model T-01-10 budgets connections |
| send_default_pii=False no manual SentryAsgiMiddleware | 01-02 task 3 init_sentry; 01-04 task 2 main.py — Sentry init happens before router imports |
| structlog redaction BEFORE renderer | 01-02 task 3 explicit processor order: redact_pii is third-to-last, renderer is last |
| /health always returns HTTP 200 | 01-04 task 2 healthcheck.py — status field carries truth, HTTP always 200 |
| F1 NO bot logic / WhatsApp / SoftSeguros / real security | enforced via files_modified (none of those modules touched) and PLAN.md goal statement |

### 9. Out-of-scope discipline — PASS

Spot-check for scope creep, none found:

- No app/features/qa/graph.py, no tools.py, no prompts.py (those are F3).
- No app/integrations/softseguros.py or meta_cloud.py (F2).
- No app/security/*.py real modules — only empty __init__.py placeholders (F3-F5).
- No app/memory/case_store.py or debtor_flags.py (F6).
- No audit log table or hash chain (F5).
- No POST /case/handoff endpoint (F6).
- app/worker.py stub is created with empty WorkerSettings.functions = [] — minimal so Dockerfile.worker has a valid entrypoint, no actual jobs.

01-06 also explicitly states Chatwoot is IDLE; no inboxes, no agents — does not push into F3 integration.

### 10. Version pin compliance — PASS

01-01 task 1 lists each runtime + dev dep with the exact CONTEXT.md D-05 version. Verified line-by-line:

| Package | CONTEXT.md D-05 | 01-01 task 1 | Match |
|---|---|---|---|
| Python | 3.12 | requires-python = ">=3.12,<3.13" + .python-version=3.12 | yes |
| FastAPI | 0.138.1 | fastapi==0.138.1 | yes |
| LangGraph | 1.2.6 | langgraph==1.2.6 | yes |
| langgraph-checkpoint-postgres | 3.1.0 | langgraph-checkpoint-postgres==3.1.0 | yes |
| langchain-openai | 1.3.3 | langchain-openai==1.3.3 | yes |
| pydantic | 2.13.4 | pydantic==2.13.4 | yes |
| asyncpg | 0.31.0 | asyncpg==0.31.0 | yes |
| psycopg | 3.3.4 | psycopg[binary,pool]==3.3.4 | yes |
| sqlalchemy | 2.0.51 | sqlalchemy==2.0.51 | yes |
| alembic | 1.18.5 | alembic==1.18.5 | yes |
| structlog | 26.1.0 | structlog==26.1.0 | yes |
| sentry-sdk | 2.63.0 | sentry-sdk[fastapi]==2.63.0 | yes |
| redis | 8.0.1 | redis==8.0.1 | yes |
| ruff | 0.15.20 | ruff==0.15.20 | yes |
| mypy | 2.1.0 | mypy==2.1.0 | yes |

All 15 CONTEXT-pinned versions match. Additional research-pinned versions (uvicorn==0.49.0, pydantic-settings==2.14.2, langchain==1.3.11, langsmith==0.9.3, arq==0.28.0, httpx==0.28.1, asgi-correlation-id==5.0.1, orjson==3.11.9, python-multipart==0.0.32, tenacity==9.1.4, pybreaker==1.4.1, pytest==9.1.1, pytest-asyncio==1.4.0, black==26.5.1, pre-commit==4.6.0) match RESEARCH.md Standard Stack exactly.

---

## Gaps (non-blocking, but worth tightening)

### WARNING — app/worker.py stub is created inside 01-02 task 1 but not declared in files_modified

- **Where:** 01-02-PLAN.md frontmatter files_modified lists Dockerfile, Dockerfile.worker, .dockerignore, app/config/settings.py, app/config/logging.py, app/config/observability.py — but NOT app/worker.py.
- **What the prose says (lines 184-189):** "En F1 NO existe app/worker.py todavía ... crear un stub app/worker.py minimal with class WorkerSettings: functions = []".
- **Why it matters:** The frontmatter is the contract the executor and the post-execution verifier rely on. A file created but not listed means the post-execution check for wave conflict may give false reads, and the changelog/commit summary may omit it.
- **Severity:** WARNING (the prose is explicit, the file will be created; only the frontmatter is out of sync).
- **Fix hint:** Add app/worker.py to 01-02-PLAN.md files_modified list. One-line edit.

No other gaps. Every requirement, decision, and pitfall is addressed.

---

## Suggestions (nice-to-have, not blockers)

1. **01-04 task 3 tests/test_health.py** stubs the probes. Consider adding one integration-style test that uses a real LifespanContext (with httpx ASGITransport(lifespan="on") or LifespanManager from asgi-lifespan) so the lifespan wiring is exercised by CI at least once. Not blocking — F1 deliberately keeps tests offline — but worth a sub-issue for F2 entry.

2. **01-03 task 3 migration 0001** offers two implementation alternatives (hardcoded DDL vs asyncio.run(setup()) inside upgrade()). The plan defers the choice to the executor. Either path is defensible; the planner could indicate a default preference (recommend: hardcoded DDL because it removes the asyncio.run inside Alembic which some linters flag and is harder to reason about during downgrade).

3. **01-05 task 2** railway service create/connect CLI commands assume the modern Railway CLI 4.x syntax. If the operator CLI version differs, the commands need adjustment. Adding a railway --version precondition check in Task 1 (HUMAN) would prevent late discovery.

4. **01-06 task 2** correctly calls out Pitfall 6 (Cloudflare proxy off until cert issues). Consider adding an explicit dig verification block at the end of the procedure so the operator can self-debug without re-reading RESEARCH.md.

5. **01-07 task 1** uses a regex to pull the agent domain out of 01-05-SUMMARY.md. If the SUMMARY uses Markdown table formatting or wraps the domain in backticks, the regex still works, but documenting the expected SUMMARY format in 01-05 output block would make it more robust.

6. PLAN.md "Goal-backward verification" tables use a GOAL-1.x requirement nomenclature that is not formally declared anywhere (ROADMAP does not use IDs; CONTEXT.md uses D-0x). The mapping is intuitive but a one-line legend in PLAN.md would help the executor and verifier.

---

## Pre-execution warnings (operator must know)

These are not plan defects; they are operational realities the executor and Maxi should anticipate.

1. **OpenRouter quota risk during smoke**: 01-04 and 01-07 each invoke get_llm("conversation").ainvoke(...) end-to-end against OpenRouter using the real API key (no stub at that layer). Cost is negligible (~$0.001 per call) but credentials must be live before 01-05 Task 1 ends. If the OPENROUTER_API_KEY in Task 1 is wrong, 01-07 SC2 fails and the loop is long (rebuild + redeploy + retry).

2. **LangSmith free tier ceiling**: RESEARCH.md Pitfall 4 — 5,000 traces/month cap. With LANGSMITH_TRACING=true enabled from F1, each /health call that happens to invoke any langchain primitive (none do in F1) and each /test/llm call eats from the budget. F1 alone is fine; just be aware before adding heavy traffic in F2.

3. **Chatwoot RAM pressure on Railway Hobby**: RESEARCH.md Pitfall 5 — chatwoot-rails needs at least 4GB. CONTEXT.md acknowledges Hobby plan 8GB total budget. If the agent group + chatwoot group together exceed 8GB at idle, 01-06 task 2 may surface OOMs after the wizard completes. Have the upgrade path to Pro ($20/mo) pre-approved with Maxi.

4. **DNS and Let's Encrypt rate limiting**: RESEARCH.md Pitfall 6 — repeated retries can hit a 5-failure-per-hour lockout. Operator should not test-then-retest the domain config. 01-06 Task 2 calls this out but it is easy to miss under stress.

5. **/test/llm and /test/sentry are deliberately unauthenticated** in F1 (threat T-01-16 / T-01-17 — accepted). The Railway agent domain will be a public URL that anyone on the internet could hit to burn quota. Plan acknowledges F5 hardening. If the public domain leaks before F5 cutover, consider adding a quick INTERNAL_TOKEN header check as a follow-up.

6. **AsyncPostgresSaver.setup() runs twice** in 01-03 + 01-04: once from alembic migration 0001 (DDL hardcoded variant), once from lifespan startup (await app.state.checkpointer.setup() in 01-04 task 2 main.py). Both calls are idempotent (CREATE TABLE IF NOT EXISTS), so this is safe — RESEARCH.md Pitfall 1 only warns against per-request setup(). Worth noting that 01-04 SUMMARY.md should record observed behavior so we know the alembic version stays in sync if langgraph-checkpoint-postgres upgrades.

---

## Verdict summary

| Criterion | Result |
|---|---|
| 1. Goal achievement | PASS |
| 2. Success criteria coverage (5/5) | PASS |
| 3. Deliverables coverage (16/16) | PASS |
| 4. Locked decisions respected | PASS |
| 5. Parallelization correctness | PASS |
| 6. Plan atomicity | PASS |
| 7. Verifiability | PASS |
| 8. Critical reminders honored | PASS |
| 9. Out-of-scope discipline | PASS |
| 10. Version pin compliance | PASS |

**Final: PASS** — plans are ready for execution. Address the one WARNING (add app/worker.py to 01-02-PLAN.md files_modified) and the executor can proceed without further revision. The Suggestions and Pre-execution warnings are informational.

---

*Generated by gsd-plan-checker on 2026-06-27. Pre-execution gate satisfied.*
