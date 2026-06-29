    # Chatwoot deploy notes

## Template

`chatwoot-all-in-one-pgvector` — Railway community template
(https://railway.com/deploy/chatwoot-all-in-one-pgvector).

Image: `chatwoot/chatwoot` running **Rails 7.1.5.2** / **Ruby 3.4.4** / **Puma 7.2.1** /
**Sidekiq 7.3.1** (per runtime boot logs).

## Services in this group

| Service | Role | Service ID |
|---|---|---|
| `Chatwoot` | Rails web (Puma on :8080) + Sidekiq (multirun) | `389261f5-66bc-48b4-8573-e37463343941` |
| `pgvector` | Postgres + pgvector extension | `2e3cb72b-08a6-47f0-91a2-8a04d471c4dd` |
| `Redis-4p8s` | Redis for Sidekiq + Action Cable | `b0f837d1-a24b-41f9-b604-07ca300f4265` |

> The template combines Rails + Sidekiq in a single container using `multirun`. No
> separate `chatwoot-sidekiq` service.

## Env vars wired (Phase 1)

The template **did NOT auto-populate the critical env vars** (`DATABASE_URL`,
`REDIS_URL`, `RAILS_ENV`, etc.) — they landed as empty strings, which made the
container loop-crash on `redis-cli -p $PORT` (because `$PORT` was empty). Manually
set after deploy:

| Var | Value | Notes |
|---|---|---|
| `DATABASE_URL` | `${{pgvector.DATABASE_URL_PRIVATE}}` | Public DATABASE_URL was empty; used the private form |
| `REDIS_URL` | `redis://default:<password>@redis-4p8s.railway.internal:6379` | Redis-4p8s template never populated `REDIS_URL`; constructed manually from `REDIS_PASSWORD` + standard 6379 port |
| `PGHOST` | `${{pgvector.PGHOST_PRIVATE}}` | Same reason as DATABASE_URL |
| `PGPORT` | `${{pgvector.PGPORT_PRIVATE}}` | |
| `PGUSER` / `PGPASSWORD` / `PGDATABASE` | `${{pgvector.*}}` | |
| `RAILS_ENV` | `production` | |
| `NODE_ENV` | `production` | |
| `INSTALLATION_ENV` | `Railway` | |
| `DEFAULT_LOCALE` | `es` | Spanish UI for DPG operators |
| `ACTIVE_STORAGE_SERVICE` | `local` | Use Railway volume mounted at `/app/storage` |
| `ENABLE_ALPINE_PRIVATE_NETWORKING` | `true` | |
| `FORCE_SSL` | `true` | |
| `FRONTEND_URL` | `https://chatwoot-production-d073.up.railway.app` | Will move to `https://chat.landatech.org` when custom domain lands |
| `SECRET_KEY_BASE` | (auto-generated, in Railway) | Template default |
| `MAILER_INBOUND_EMAIL_DOMAIN` | (empty in Phase 1) | F3+ if inbound email is needed |
| `SMTP_*` | (unset in Phase 1) | F3+ if outbound email/notifications needed |
| `TRIGGER_REBUILD` | placeholder | Used to force fresh deploys when settings change (see runbook) |

Internal hostnames (private):

- `pgvector.railway.internal:5432`
- `redis-4p8s.railway.internal:6379`

## Custom domain

**Not configured in Phase 1**. Pending decision and DNS work, alongside `agent.landatech.org`
for the main API service. Once configured, both `chat.landatech.org` (Chatwoot) and
`agent.landatech.org` (FastAPI) will live behind the same `landatech.org` zone.

For the configuration steps when ready, see
[../.planning/phases/01-setup-infra/CHATWOOT_NOTES.md#custom-domain-future](../.planning/phases/01-setup-infra/CHATWOOT_NOTES.md).

## Healthcheck

Reconfigured via Railway GraphQL API (`serviceInstanceUpdate`) because:

- Template's default `healthcheckPath=/api` returns 404 on Chatwoot (no such route)
- Container would loop-crash on healthcheck timeouts even with Rails + Sidekiq running
- Disabled temporarily during onboarding (no valid 200-returning route until DB is initialized + admin created)
- Post-onboarding re-enabled to `healthcheckPath=/`, `healthcheckTimeout=300`

## Admin

First super-admin created during onboarding (Phase 1, plan 01-06):

- **Email**: operator-managed (LANDA Tech)
- **Password**: stored in operator's password manager (NOT in repo, NOT in any env)

First tenant **account**: `DPG Seguros`.

## Status

- **Phase 1**: IDLE — admin login works; account `DPG Seguros` exists; no inboxes, no agents beyond admin, no contacts
- **Phase 3**: first WhatsApp inbox connection, mirroring of bot conversations + DPG cartera agents invited as agents of this account
- **Future (multi-tenant)**: additional accounts (`Cliente #2`, etc.) created here under the same super-admin
