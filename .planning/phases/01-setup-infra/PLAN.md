---
phase: 01-setup-infra
type: phase-overview
plans:
  - 01-01-PLAN.md
  - 01-02-PLAN.md
  - 01-03-PLAN.md
  - 01-04-PLAN.md
  - 01-05-PLAN.md
  - 01-06-PLAN.md
  - 01-07-PLAN.md
  - 01-08-PLAN.md
waves: 5
mvp_mode: false
tdd_mode: false
---

# Phase 1 — Setup infra (Overview)

Toda la infraestructura del microservicio `landa-agent-service` corriendo en Railway, lista para recibir el código del bot que llega en Phases 2-7. Esta fase NO incluye lógica de WhatsApp, LangGraph nodes del bot, SoftSeguros real, ni el security pipeline real — sólo el chasis y un health-check end-to-end con LangSmith + Sentry + OpenRouter funcionando.

## Plan files

| # | Plan | Wave | Autonomous | Goal one-liner |
|---|---|---|---|---|
| 01 | [Repo scaffold + dependencies + tooling](./01-01-PLAN.md) | 1 | yes | `pyproject.toml` + `uv.lock` con todas las deps pinned, vertical slice empty packages, ruff/black/mypy/pytest config, pre-commit hooks, GitHub Actions CI, `.env.example`, `.gitignore` |
| 02 | [Dockerfiles + Settings + structlog + Sentry init](./01-02-PLAN.md) | 2 | yes | Multi-stage `Dockerfile` (FastAPI) + `Dockerfile.worker` (ARQ), Pydantic Settings con `SecretStr`, structlog con redaction PII (key-name + regex), Sentry init con `send_default_pii=False` + `before_send` scrubber, asgi-correlation-id |
| 03 | [DB engine + Alembic async + LangGraph checkpointer + Redis](./01-03-PLAN.md) | 2 | yes | SQLAlchemy 2.0 async engine (asyncpg), Redis async pool, alembic async init + migration inicial (esquema vacío), `AsyncPostgresSaver` lifespan helper (psycopg 3) |
| 04 | [LLM factory + FastAPI app + endpoints + tests](./01-04-PLAN.md) | 3 | yes | `app/integrations/openrouter.py` con `get_llm(role)` factory, `app/main.py` con lifespan wiring de Postgres + Redis + checkpointer, `GET /health` con parallel async probes, `POST /test/llm` que invoca `get_llm("conversation").ainvoke("ping")`, pytest tests sanity |
| 05 | [Railway deploy — agent service group](./01-05-PLAN.md) | 4 | no | Provisionar Postgres + Redis + servicio FastAPI + servicio ARQ worker en Railway (single project group A). Verificar conexión privada, smoke `/health` 200. Human checkpoint para credenciales y dominios. |
| 06 | [Railway deploy — Chatwoot self-hosted en chat.landatech.org](./01-06-PLAN.md) | 4 | no | Desplegar Chatwoot vía template `chatwoot-all-in-one-pgvector` (group B en mismo Railway project), configurar custom domain `chat.landatech.org` con SSL Let's Encrypt, completar onboarding wizard, admin login funcional. **Idle — sin tráfico real**. |
| 07 | [End-to-end smoke verification](./01-07-PLAN.md) | 5 | no | Verificar los 5 success criteria del ROADMAP en orden: `/health` 200, `/test/llm` retorna respuesta, Chatwoot login, trace en LangSmith, evento sintético en Sentry. Human checkpoint para confirmar visualmente cada uno. |
| 08 | [Update CLAUDE.md quick-start to uv](./01-08-PLAN.md) | 1 | yes | Reemplazar el bloque `pip install -r requirements.txt` en CLAUDE.md con instrucciones `uv sync` + `uv run`. Documentar opcional `uv pip compile` fallback. |

## Parallelization

Plans agrupados por wave. Dentro de cada wave los planes corren en paralelo (no comparten `files_modified`).

| Wave | Plans concurrentes | Notas |
|---|---|---|
| **1** | `01-01`, `01-08` | 01 crea scaffolding; 08 edita sólo CLAUDE.md — disjuntos |
| **2** | `01-02`, `01-03` | Ambos dependen de `pyproject.toml` (01) pero tocan archivos disjuntos: 02 en `Dockerfile*`, `app/config/settings.py`, `app/config/logging.py`, `app/config/observability.py`; 03 en `app/config/db.py`, `app/config/redis.py`, `app/config/checkpointer.py`, `alembic/`. Sin overlap. |
| **3** | `01-04` | Único — consume settings (02) + db/redis/checkpointer (03) en `main.py`. No puede paralelizarse con nada más. |
| **4** | `01-05`, `01-06` | Despliegues Railway en service groups separados del mismo project. Toca archivos disjuntos (`railway.toml` o `.railway/` para agent vs `chatwoot/` para Chatwoot template). Human checkpoints distintos. |
| **5** | `01-07` | Smoke E2E final. Depende de 05 y 06 para tener un servicio accesible públicamente. |

## Dependency graph

```
                              Wave 1
                              ──────
                    ┌────────────────────────┐
                    │  01-01 (scaffold/deps) │   ┌──────────────────────────┐
                    │  uv + pyproject + dirs │   │  01-08 (CLAUDE.md → uv)  │
                    └────────────┬───────────┘   └──────────────────────────┘
                                 │
                  ┌──────────────┴───────────────┐
                  │                              │
              Wave 2                         Wave 2
              ──────                         ──────
       ┌──────────────────────┐     ┌────────────────────────────────┐
       │ 01-02 (Dockerfiles + │     │ 01-03 (DB engine + alembic +   │
       │ Settings + structlog │     │ checkpointer + Redis pool)     │
       │ + Sentry)            │     │                                │
       └──────────┬───────────┘     └──────────────┬─────────────────┘
                  │                                │
                  └────────────────┬───────────────┘
                                   │
                               Wave 3
                               ──────
                 ┌────────────────────────────────┐
                 │ 01-04 (LLM factory + main.py   │
                 │ lifespan + /health + /test/llm │
                 │ + pytest)                      │
                 └──────────────┬─────────────────┘
                                │
                  ┌─────────────┴──────────────┐
                  │                            │
              Wave 4                       Wave 4
              ──────                       ──────
       ┌────────────────────┐     ┌─────────────────────────────┐
       │ 01-05 (Railway:    │     │ 01-06 (Railway: Chatwoot    │
       │ agent service grp) │     │ group + chat.landatech.org) │
       └──────────┬─────────┘     └──────────────┬──────────────┘
                  │                              │
                  └──────────────┬───────────────┘
                                 │
                             Wave 5
                             ──────
                 ┌────────────────────────────────┐
                 │ 01-07 (E2E smoke verification) │
                 │ 5 success criteria del ROADMAP │
                 └────────────────────────────────┘
```

## Goal-backward verification

Cada success criterion del ROADMAP / CONTEXT mapea a el(los) plan(es) que lo satisfacen:

| Success criterion (ROADMAP §Phase 1) | Plan(s) responsables | Verificación final en |
|---|---|---|
| `GET /health` responde 200 con info de Postgres + Redis + LangSmith conectados | `01-04` (endpoint), `01-03` (Postgres + Redis disponibles), `01-02` (Settings con env vars) | `01-07` step 1 |
| Endpoint dummy invoca `get_llm("conversation").ainvoke("ping")` y devuelve respuesta de OpenRouter | `01-04` (`POST /test/llm`), `01-02` (`OPENROUTER_API_KEY` en Settings) | `01-07` step 2 |
| Chatwoot panel accesible en `chat.landatech.org`, admin login funcional, posible crear inbox manualmente | `01-06` (deploy + dominio + onboarding) | `01-07` step 3 |
| Trace de la llamada dummy aparece en LangSmith (proyecto `landa-agent-dev`) | `01-02` (env vars `LANGSMITH_*`), `01-04` (`ChatOpenAI` con tracing auto), `01-05` (env vars en Railway) | `01-07` step 4 |
| Error sintético llega a Sentry | `01-02` (Sentry init), `01-04` (endpoint o test que dispara error), `01-05` (`SENTRY_DSN` en Railway) | `01-07` step 5 |

### Deliverables del ROADMAP → planes que los entregan

| Deliverable | Plan |
|---|---|
| Repo scaffold FastAPI + estructura vertical slice | 01-01 |
| `pyproject.toml` + `uv.lock` con dependencias pinned | 01-01 |
| Dockerfile multi-stage FastAPI + ARQ worker | 01-02 |
| Postgres + Redis aprovisionados en Railway | 01-05 |
| LangGraph + `AsyncPostgresSaver` en FastAPI lifespan | 01-03, 01-04 |
| Migración inicial de alembic (esquema vacío + tabla LangGraph checkpoint) | 01-03 |
| Chatwoot desplegado en Railway, dominio `chat.landatech.org` SSL, admin login | 01-06 |
| LangSmith conectado, proyecto `landa-agent-dev` | 01-02 (env), 01-05 (Railway env), 01-07 (verificación) |
| Sentry conectado con `asgi-correlation-id` | 01-02 |
| structlog con PII redaction (key-name + regex teléfonos) y JSON output | 01-02 |
| `app/config/llm.py` con Pydantic Settings y factory `get_llm(role)` | 01-04 (factory en `integrations/openrouter.py` per arch map; `config/llm.py` aloja `LLMSettings` y re-exporta `get_llm`) |
| Endpoint `GET /health` que verifica Postgres + Redis + LangSmith env + OpenRouter | 01-04 |
| Endpoint dummy `POST /test/llm` | 01-04 |
| CI básico (pytest + ruff + black + mypy) | 01-01 |
| Pre-commit hooks (ruff + black) | 01-01 |
| `.env.example` con variables documentadas | 01-01 |
| Update CLAUDE.md quick-start a uv | 01-08 |

### Decision coverage (CONTEXT.md → tasks)

| Locked decision | Plan que la implementa |
|---|---|
| D-01 Railway Hobby single project two service groups | 01-05 (group A), 01-06 (group B) |
| D-02 uv + pyproject.toml + uv.lock | 01-01 |
| D-03 Custom multi-stage Dockerfile (NOT Nixpacks/Railpack) | 01-02 |
| D-04 LangSmith free tier, proyecto `landa-agent-{env}` | 01-02 (settings), 01-05 (env vars Railway), 01-07 (verify) |
| D-05 Stack versions pinned exact | 01-01 (`pyproject.toml`) |
| D-06 `AsyncPostgresSaver` en lifespan con `__aenter__`/`__aexit__` + dos pools separados (asyncpg + psycopg) | 01-03 (checkpointer helper + db engine), 01-04 (lifespan wiring) |
| D-07 `asgi-correlation-id` + PII redaction key-name + regex teléfonos | 01-02 |
| Chatwoot template `chatwoot-all-in-one-pgvector` | 01-06 |
| `app/config/settings.py` con `env_prefix` por dominio + `extra="ignore"` | 01-02 |
| `send_default_pii=False` + scrubber Sentry `before_send` | 01-02 |

### Source audit summary

- **GOAL items** (5 success criteria): todos cubiertos por planes 01-04, 01-05, 01-06, 01-07.
- **REQ items** (16 deliverables ROADMAP): todos cubiertos — tabla arriba.
- **RESEARCH items** (8 patrones, 8 pitfalls, stack pin, Dockerfile, CI, pre-commit, alembic async): cubiertos por 01-01/02/03/04.
- **CONTEXT items** (D-01..D-07 + 3 open items): D-01..D-07 cubiertos; open items 1-3 son acknowledged risk (no requieren tarea, son monitoreo en F7).

No gaps. Listo para ejecutar.

## Critical reminders for executors

1. **No `requirements.txt`** — uv + pyproject.toml es la fuente de verdad. Si CI/CD necesita un `requirements.txt` para compatibilidad, generar vía `uv pip compile pyproject.toml -o requirements.txt` como artefacto, no fuente.
2. **No `@app.on_event`** — usar `lifespan=` context manager (FastAPI 0.93+, Starlette 0.36+ obligatorio).
3. **AsyncPostgresSaver con `__aenter__`/`__aexit__` explícito** en lifespan — no `async with` block (CONTEXT.md D-06; RESEARCH.md Pattern 1 / Pitfall 1).
4. **Dos pools Postgres separados**: asyncpg vía SQLAlchemy para app data, psycopg 3 para LangGraph checkpointer (CONTEXT.md D-06).
5. **`send_default_pii=False`** en Sentry, **no** wrap manual con `SentryAsgiMiddleware` (auto-detection en sentry-sdk 2.x — RESEARCH.md Pattern 4 / Pitfall noted in anti-patterns).
6. **structlog PII redaction debe correr ANTES del renderer JSON** y antes de cualquier sink Sentry (RESEARCH.md Pattern 3 / Pitfall 7).
7. **Endpoint `/health` siempre devuelve HTTP 200** — el campo `status` cuenta la verdad. Railway saca de rotación servicios con 503 (RESEARCH.md Pattern 5).
8. **Phase 1 NO toca lógica de bot, WhatsApp, SoftSeguros, security pipeline real**. Placeholders OK donde aplique (CONTEXT.md "Out of scope for Phase 1").

## Success criteria for the phase

Verificable al final de `01-07`:

- [ ] `curl https://<agent-railway-domain>/health` retorna 200 + JSON con `components.postgres.ok=true`, `components.redis.ok=true`, `components.openrouter.ok=true`, `components.langsmith_env.ok=true`
- [ ] `curl -X POST https://<agent-railway-domain>/test/llm` retorna 200 + JSON `{"reply": "<respuesta del modelo>", "model": "google/gemini-2.0-pro"}` (o el default configurado en env)
- [ ] `https://chat.landatech.org` carga panel Chatwoot, login con admin creado funciona, posible crear inbox manualmente (no se conecta a WhatsApp todavía)
- [ ] LangSmith proyecto `landa-agent-dev` muestra al menos un trace de la llamada `/test/llm`
- [ ] Sentry proyecto correspondiente muestra al menos un evento sintético (disparado vía endpoint de prueba o `pytest` que lance excepción)
- [ ] CI verde en main: `ruff check`, `black --check`, `mypy --strict`, `pytest` todos pasan
- [ ] Pre-commit hooks instalados localmente y funcionando

---

*Generated by `/gsd-plan-phase 01-setup-infra` on 2026-06-27. Edit only via `/gsd-plan-phase --revise 01-setup-infra` to keep wave + dependency graph consistent.*
