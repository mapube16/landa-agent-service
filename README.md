# landa-agent-service

LANDA Tech WhatsApp agent for DPG Seguros — Q&A inbound for policy info +
payment-receipt validation flow with human escalation through Chatwoot.

## Quick start

```bash
cp .env.example .env   # fill credentials
uv sync --frozen
uv run uvicorn app.main:app --reload
uv run pytest -q
uv run ruff check . && uv run black --check . && uv run mypy app
```

## Where things live

- `app/features/{qa,payment,escalation,handoff}/` — vertical-slice features
- `app/integrations/` — external clients (SoftSeguros, Chatwoot, Meta Cloud, OpenRouter)
- `app/security/` — 13-layer defense-in-depth pipeline
- `knowledge/` — static KB injected into system prompt (DPG cartera, FAQs)

See `.planning/PROJECT.md` for scope, `.planning/ROADMAP.md` for phase plan,
and `CLAUDE.md` for conventions and locked decisions.
