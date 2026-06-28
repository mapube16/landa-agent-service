# Chatwoot (self-hosted, deployed in Railway)

Chatwoot **does not live in this repo**. It is deployed as a service group within
the Railway project `brilliant-perfection` (ex `landa-agent` in earlier docs),
via the community template
[`chatwoot-all-in-one-pgvector`](https://railway.com/deploy/chatwoot-all-in-one-pgvector).

- **Production URL (Phase 1)**: https://chatwoot-production-d073.up.railway.app
- **Custom domain target (future)**: `chat.landatech.org` — deferred to the end of Phase 1 alongside `agent.landatech.org`
- **Owned by**: LANDA Tech (operator: Maxi)
- **Tenant account**: DPG Seguros (created during onboarding)
- **Status (Phase 1)**: IDLE — no WhatsApp inbox connected; first real traffic in Phase 3

Configuration notes (env vars, template quirks, troubleshooting): see
[notes.md](./notes.md) and
[../.planning/phases/01-setup-infra/CHATWOOT_NOTES.md](../.planning/phases/01-setup-infra/CHATWOOT_NOTES.md).

This repo (`landa-agent-service`) will talk to Chatwoot via the REST API client in
`app/integrations/chatwoot.py` (built in Phase 3).
