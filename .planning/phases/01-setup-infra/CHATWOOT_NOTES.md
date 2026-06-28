# Chatwoot — Phase 1 runbook

Operational runbook for the Chatwoot self-hosted instance deployed in plan 01-06.
See [`chatwoot/README.md`](../../../chatwoot/README.md) for the high-level summary and
[`chatwoot/notes.md`](../../../chatwoot/notes.md) for the env-var reference.

## Identities

| Item | Value |
|---|---|
| Project | `brilliant-perfection` (same as agent service group) |
| Environment | `production` |
| Service IDs | Chatwoot=`389261f5-66bc-48b4-8573-e37463343941`, pgvector=`2e3cb72b-08a6-47f0-91a2-8a04d471c4dd`, Redis-4p8s=`b0f837d1-a24b-41f9-b604-07ca300f4265` |
| Public URL (Phase 1) | https://chatwoot-production-d073.up.railway.app |
| Custom domain target | `chat.landatech.org` — deferred |
| Tenant account #1 | `DPG Seguros` |

## Deploy procedure (executed in plan 01-06)

1. Operator opened https://railway.com/deploy/chatwoot-all-in-one-pgvector → "Deploy to existing project" → `brilliant-perfection`. Railway provisioned `Chatwoot`, `pgvector`, `Redis-4p8s`.
2. **Template did NOT populate critical env vars** — they landed as empty strings. Symptom: container loop-spammed `-p: - no response` (from `redis-cli -p $PORT ping` with `$PORT` empty), exhausting the log rate limit.
3. Fix applied via CLI (`railway variable set --service Chatwoot ...`):
   - `DATABASE_URL=${{pgvector.DATABASE_URL_PRIVATE}}`, plus `PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE` from pgvector's `*_PRIVATE` variants (the public ones were also empty on this template).
   - `REDIS_URL=redis://default:<password>@redis-4p8s.railway.internal:6379` constructed manually (Redis-4p8s's `REDIS_URL` was empty; only `REDIS_PASSWORD` was set).
   - `RAILS_ENV=production`, `NODE_ENV=production`, `INSTALLATION_ENV=Railway`, `DEFAULT_LOCALE=es`, `ACTIVE_STORAGE_SERVICE=local`, `ENABLE_ALPINE_PRIVATE_NETWORKING=true`, `FORCE_SSL=true`, `FRONTEND_URL=<railway domain>`.
4. **Template's healthcheck was wrong** — `healthcheckPath=/api` returns 404 in Chatwoot (no such route). Even with Rails booted and Sidekiq processing cron jobs, healthcheck failed → container killed after 5 min retry window.
5. Healthcheck reconfigured via Railway GraphQL `serviceInstanceUpdate` mutation (the CLI exposes no command for this):
   - Disabled (`healthcheckPath=""`) during onboarding because no path returned 200 before DB was initialized
   - Re-enabled post-onboarding to `healthcheckPath=/`, `healthcheckTimeout=300`
6. Fresh deploys triggered via a dummy env-var change (`TRIGGER_REBUILD`) because `railway redeploy` reuses the cached deployment config and doesn't pick up service-instance setting changes.
7. Container booted, Rails listened on `:8080`, Sidekiq processed cron jobs. Operator opened `/installation/onboarding` and completed the wizard (super admin email + DPG Seguros account name).

## Common operations

```bash
# Service status
railway status

# Runtime logs
railway logs --service Chatwoot

# Restart (without rebuild)
railway service restart Chatwoot

# Force fresh deploy (after config change)
railway variable set --service Chatwoot TRIGGER_REBUILD=$(date +%s)

# Open a shell into the live container
railway ssh --service Chatwoot

# Rails console
railway ssh --service Chatwoot "bundle exec rails console"

# Connect to Chatwoot DB
railway connect --service pgvector
```

## Modifying service-instance settings (healthcheck, start command, etc.)

The Railway CLI has no equivalent to "edit service". Use the GraphQL API directly:

```bash
TOKEN=$(python -c "import json; print(json.load(open(r'C:/Users/<user>/.railway/config.json'))['user']['accessToken'])")
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"mutation($svc:String!,$env:String!,$input:ServiceInstanceUpdateInput!){serviceInstanceUpdate(serviceId:$svc,environmentId:$env,input:$input)}","variables":{"svc":"<SERVICE_ID>","env":"<ENV_ID>","input":{"healthcheckPath":"/","healthcheckTimeout":300}}}'
```

After updating, trigger a fresh deploy with `railway variable set --service <name> TRIGGER_REBUILD=$(date +%s)` — `railway redeploy` won't re-read the updated settings.

## Custom domain (future)

When ready to migrate from `*.up.railway.app` to `chat.landatech.org`:

1. In Railway dashboard: `Chatwoot` service → Settings → Networking → Custom Domain → add `chat.landatech.org`. Railway returns 2 DNS records (CNAME + ACME TXT).
2. In the DNS provider for `landatech.org`:
   - `CNAME chat.landatech.org` → `<railway-target>.up.railway.app`
   - `TXT _acme-challenge.chat.landatech.org` → `<ACME value>`
   - **CRITICAL**: if Cloudflare, leave proxy GRAY (DNS only) until cert is issued
3. Wait 5–15 min for Let's Encrypt cert. If pending >20 min: verify both records resolve (`dig`), confirm TXT matches Railway's value letter-by-letter, check Let's Encrypt rate limit (5 fails/hr).
4. Update env var: `railway variable set --service Chatwoot FRONTEND_URL=https://chat.landatech.org`

## Troubleshooting

### Container loops with `-p: - no response`

`redis-cli -p $PORT` with empty `$PORT`. **Cause**: `REDIS_URL` env var is empty.
**Fix**: re-check `railway variable list --service Chatwoot | grep REDIS_URL`; reset if empty.

### Healthcheck fails but Rails is up in logs

The path probably returns non-200. Check via `curl https://<domain>/path`. Then update
healthcheckPath via GraphQL (see "Modifying service-instance settings" above).

### `PG::UndefinedTable: installation_configs`

Rails initializer queries the table before `db:chatwoot_prepare` ran. Usually a transient
boot warning — `db:chatwoot_prepare` runs as part of the start command's `&&` chain and
the schema lands shortly after. If it persists across deploys, SSH and run manually:

```bash
railway ssh --service Chatwoot "bundle exec rails db:chatwoot_prepare && bundle exec rails db:migrate"
```

### Sidekiq OOM

Symptom: `Sidekiq exited with signal=9`. Bump memory for the Chatwoot service in Railway
dashboard (may require Pro plan). Current observed idle memory: < 1 GB.

### `installation_configs` permanently missing

Worst case — the DB never got the schema. Connect via `railway connect --service pgvector`,
inspect with `\dt`, then `railway ssh --service Chatwoot "bundle exec rails db:chatwoot_prepare"`.

## Rollback

- **DB snapshots**: Railway provides volume snapshots; restore via dashboard
- **Application**: `railway redeploy --service Chatwoot --yes` of a previous deployment (Railway keeps history)
- **Domain**: remove custom domain from Railway service; the `*.up.railway.app` keeps working

## Phase 3 readiness checklist

Before plan F3-01 (first WhatsApp inbox connection):

- [ ] Admin can log in via the Phase 1 URL (or custom domain if migrated)
- [ ] `DPG Seguros` account exists with admin as agent
- [ ] Can create a test inbox manually (any channel) — proves Rails routes/UI work end-to-end
- [ ] API token generated under Profile → Access Tokens — this token is what `app/integrations/chatwoot.py` will use in F3
- [ ] DPG cartera team emails collected → invited as agents to the DPG account
- [ ] Custom domain `chat.landatech.org` configured + SSL valid

## Notes

- Phase 1 is **IDLE**. Memory and CPU usage should stay low (< 1 GB / < 5 % CPU). If usage spikes without traffic, investigate.
- Chatwoot ships with cron jobs (IMAP fetch, snooze reopener, WhatsApp template sync) that run even idle. These show up in logs as `Performed FooJob`.
- Egress controls (F5 work) will eventually restrict Chatwoot's egress to the WhatsApp Cloud API + email providers + LANDA's agent service. Not in scope for Phase 1.
