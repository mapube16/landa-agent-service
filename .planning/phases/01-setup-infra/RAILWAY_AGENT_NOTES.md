# Railway agent service runbook

Operativa del proyecto Railway que aloja `landa-agent-service` + worker + Postgres + Redis.

Documento vivo: **rotá credenciales acá no aplica** — usá Railway UI/CLI para rotar y reflejá nombres/IDs si cambian.

---

## Identidades

| Item | Valor |
|---|---|
| Workspace | "Maximiliano Pulido beltran 's Projects" |
| Project | `brilliant-perfection` (id `c1b25965-9d74-440b-a38b-e3c51bf8d80f`) |
| Environment | `production` (id `10b5dc25-1eb4-48cd-8075-bd1a7d85a48e`) |
| Region | `europe-west4-drams3a` |
| GitHub repo conectado | `mapube16/landa-agent-service@main` |

> El proyecto se llama `brilliant-perfection` (autogenerado al init) en vez del `landa-agent` que sugería el plan. Renombrar via dashboard (Settings → General → Project Name) cuando convenga; no es bloqueante.

## Services

| Service | id | role | source |
|---|---|---|---|
| `landa-agent-service` | `78b41599-7890-4a26-8f9b-4347305cd653` | FastAPI app + LangGraph checkpointer | GitHub auto-deploy (`Dockerfile`) |
| `agent-worker` | `d4d39933-967d-4ece-96eb-8f6e7eb590d6` | ARQ background worker | Local upload via `railway up` (`Dockerfile.worker` via `RAILWAY_DOCKERFILE_PATH` env) |
| `Postgres` | `622f283d-0129-474c-b252-f58e8dec9809` | LangGraph checkpoint store + alembic + future audit_log | Railway PostgreSQL template |
| `Redis` | `8896d122-790e-4301-b50a-55f6d1bdfd48` | ARQ queue + SoftSeguros cache + idempotency keys | Railway Redis template |

Internal hostnames (privadas, no resuelven desde tu laptop):

- `postgres.railway.internal:5432`
- `redis.railway.internal:6379`

Public domain de la app:

- `https://landa-agent-service-production.up.railway.app`

Custom domain `chat.landatech.org` u otro queda pendiente (DNS + Railway custom domain).

## Comandos comunes

Todos asumen que estás en `landa-agent-service/` con el proyecto linkeado (`railway link --project c1b25965-9d74-440b-a38b-e3c51bf8d80f --environment 10b5dc25-1eb4-48cd-8075-bd1a7d85a48e`).

```bash
# Status de los 4 services
railway status

# Logs runtime de un service
railway logs --service landa-agent-service
railway logs --service agent-worker

# Logs de build (último deploy)
railway logs --service landa-agent-service --build

# Conectarse a Postgres
railway connect --service Postgres

# Conectarse al container vivo del agent service
railway ssh --service landa-agent-service

# Listar env vars de un service
railway variable list --service landa-agent-service

# Setear / actualizar env var (triggera redeploy salvo --skip-deploys)
railway variable set --service landa-agent-service KEY=value
railway variable set --service landa-agent-service --skip-deploys KEY=value

# Redeploy manual (mismo commit)
railway redeploy --service landa-agent-service --yes

# Upload de código local + build (necesario para agent-worker que NO está conectado a git)
railway up --service agent-worker --detach
```

## Variables de entorno

### `landa-agent-service` y `agent-worker` (compartidas)

| Var | Valor | Notas |
|---|---|---|
| `APP_ENV` | `dev` | cambiar a `prod` para subir a producción real |
| `APP_VERSION` | `0.1.0` | bumpear con cada release |
| `APP_PUBLIC_URL` | `https://${{RAILWAY_PUBLIC_DOMAIN}}` | Railway expande server-side |
| `POSTGRES_URL` | `${{Postgres.DATABASE_URL}}` | referencia interna, no copies el valor expandido |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | idem |
| `OPENROUTER_API_KEY` | secreto | rotar en [openrouter.ai/keys](https://openrouter.ai/keys), después `railway variable set` |
| `LLM_MODEL_CONVERSATION` | `google/gemini-2.5-pro` | OpenRouter slug real (NO `2.0-pro` — no existe) |
| `LLM_MODEL_JUDGE` | `google/gemini-2.5-flash` | temp=0.0 hardcoded en factory |
| `LLM_MODEL_INTENT` | `google/gemini-2.5-flash` | |
| `LLM_MODEL_SUMMARIZER` | `google/gemini-2.5-flash` | |
| `LANGSMITH_API_KEY` | secreto | actual da 403 — regenerar en [smith.langchain.com](https://smith.langchain.com) → Settings → API Keys |
| `LANGSMITH_PROJECT` | `landa-agent-dev` | el proyecto puede no existir todavía en LangSmith UI; al primer trace válido se crea |
| `LANGSMITH_TRACING` | `true` | poner `false` si querés desactivar tracing sin tocar la key |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | US region |
| `SENTRY_DSN` | secreto | DSN del proyecto Sentry `landa-agent-service` |

### Solo `agent-worker`

| Var | Valor | Notas |
|---|---|---|
| `RAILWAY_DOCKERFILE_PATH` | `Dockerfile.worker` | Railway lee esta env-var al buildear para usar el Dockerfile alterno |

## Rotación de credenciales

1. **OpenRouter**: nueva key en [openrouter.ai/keys](https://openrouter.ai/keys), luego:
   ```bash
   railway variable set --service landa-agent-service OPENROUTER_API_KEY="sk-or-v1-..."
   railway variable set --service agent-worker OPENROUTER_API_KEY="sk-or-v1-..."
   ```
   El redeploy automático actualiza ambos.

2. **LangSmith**: nueva key en [smith.langchain.com](https://smith.langchain.com) → Settings → API Keys. Mismo pattern de `railway variable set` en ambos services.

3. **Sentry DSN**: regenerar en sentry.io → Settings (proyecto) → Client Keys → "New Key", luego revocar la vieja. `railway variable set SENTRY_DSN="https://..."` en ambos services.

4. **Postgres / Redis**: las credenciales las gestiona Railway; rotar via Railway dashboard (Service → Variables → Roll). La referencia `${{Postgres.DATABASE_URL}}` re-resuelve automáticamente en los services consumidores tras el roll.

## Decisión sobre migraciones alembic

**Decisión actual**: `alembic stamp head` ejecutado una vez via `railway ssh`. Las tablas LangGraph (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) ya las crea `AsyncPostgresSaver.setup()` en el lifespan startup del agent (idempotente). El stamp deja registrado `alembic_version=0001` para que el próximo `alembic upgrade head` salga clean.

**Por qué no `alembic upgrade head` real**: el migration `0001_initial_schema.py` hace `asyncio.run(_apply_checkpointer_setup())` pero alembic-async ya está dentro de un loop corriendo, lo que dispara `RuntimeError: asyncio.run() cannot be called from a running event loop`. Es un bug del migration heredado de plan 01-03, pendiente de fix (probablemente migrarlo a sync psycopg + ejecutar las migrations explícitamente desde MIGRATIONS de langgraph-checkpoint-postgres).

**Por qué no `preDeployCommand=alembic upgrade head`**: hasta arreglar el migration el bootstrap fallaría en cada deploy. Mientras tanto el lifespan se ocupa de las tablas. **Es seguro porque setup() es idempotente.**

**TODO**: cuando se arregle el migration, evaluar setear `preDeployCommand` para que cada deploy aplique migraciones automáticamente.

## Healthcheck

`Dockerfile` declara `HEALTHCHECK CMD curl /health || exit 1` a nivel Docker — la usa el daemon del container, no Railway routing.

Railway-level healthcheck (campo `healthcheckPath` en service config) está **vacío** en este momento. Esto significa: Railway considera el container "deployed" apenas arranca, sin esperar a que `/health` responda 200. **No es crítico para F1** porque el lifespan startup ya gatea (si Postgres/Redis no conectan, el container crashea y Railway reintenta).

Setear desde dashboard cuando quieras gating real:
- Service `landa-agent-service` → Settings → Deploy → Healthcheck Path = `/health`, Healthcheck Timeout = `30`.

## Egress

F1 no restringe egress — el container puede llamar cualquier host. La restricción real (allowlist a SoftSeguros + Meta + Chatwoot + OpenRouter + LangSmith) es trabajo de F5 (plan futuro).

## Costos observados

Plan hobby. Postgres + Redis + 2 services compute ≈ \$5/mes c/u aproximado al usage actual (idle). Monitorear en Railway dashboard.

## Outstanding gaps para plan 01-07 (smoke E2E final)

- Sentry: el endpoint `/test/sentry` ya retornó 500 una vez — verificar en sentry.io que el evento aparece en el proyecto `landa-agent-service`.
- LangSmith: la key actual da 403 → traces no llegan. Regenerar y verificar que aparezcan en el dashboard.
- Custom domain: pendiente decisión (`agent.landatech.org` o similar).
- alembic migration fix: backlog item para fixear el `asyncio.run` nesting.
