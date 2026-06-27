# Stack Research

**Domain:** WhatsApp business-messaging agent (collections/cobranza bot) integrating Twilio WhatsApp, an internal non-Business-API WhatsApp number, and Chatwoot, built as a standalone FastAPI microservice
**Researched:** 2026-06-27
**Confidence:** MEDIUM-HIGH (core framework/SDK choices HIGH; internal-number channel and chatwoot-sdk maturity LOW-MEDIUM — flagged below)

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.12 | Runtime | Already mandated by project constraints (landa-agent-service). 3.12 has mature async perf improvements over 3.10/3.11; no reason to deviate. |
| FastAPI | >=0.115,<0.137 (pin to latest 0.13x, e.g. 0.136.x) | Web framework / webhook receiver | De facto standard for Python webhook services in 2025/2026 — async-native (critical for I/O-bound webhook fan-out to Twilio/Chatwoot/internal WhatsApp), automatic OpenAPI docs, Pydantic v2 validation for incoming Twilio/Meta/Chatwoot payloads. Used in virtually every current Twilio+Python tutorial and production WhatsApp bot reference architecture found in research. (HIGH confidence — verified via PyPI release history, Apr 2026 release 0.136.1) |
| Pydantic | >=2.9 (FastAPI's current floor) | Data validation / webhook payload schemas | FastAPI 0.115+ requires Pydantic v2; v1 is unsupported going forward. v2's Rust core (pydantic-core) gives ~17x validation speed vs v1 — relevant since every inbound webhook (Twilio, Chatwoot, internal WA) needs strict schema validation before touching business logic. (HIGH) |
| Uvicorn (with `--workers` behind a process manager) or Hypercorn | uvicorn>=0.32 | ASGI server | Standard FastAPI pairing. Use `uvicorn[standard]` for uvloop + httptools. Run under Gunicorn/Uvicorn workers or directly under Railway's process model (matches existing Railway deployment for Chatwoot). (HIGH) |
| httpx | >=0.27 | Outbound HTTP client (Chatwoot REST API, Twilio fallback calls, internal WA gateway calls) | Async-native, same author ecosystem as FastAPI/Starlette. Preferred over `requests` because the whole service is async; mixing sync `requests` calls inside async route handlers blocks the event loop. (HIGH) |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `twilio` (official Python SDK) | >=9.10 (latest 9.10.9, May 2026) | Sending/receiving WhatsApp messages via Twilio, validating webhook signatures | Use for ALL interaction with the existing `+16415416615` Twilio WhatsApp number — both outbound `messages.create()` calls and inbound webhook signature validation via `RequestValidator`. Do not hand-roll Twilio signature validation. (HIGH — confirmed current on PyPI) |
| Chatwoot integration: **raw `httpx` client against Chatwoot REST API**, NOT `chatwoot-sdk` | n/a (hand-rolled thin wrapper) | Creating/updating conversations, posting messages (incl. attachments), changing conversation status (open/resolved/pending) for handoff | See "What NOT to Use" — the official `chatwoot-sdk` PyPI package is too new/unproven (v0.2.0, Feb 2026, attachment support undocumented) to trust for a production collections flow. Chatwoot's REST API is simple, well-documented, and stable; a 100-150 line internal client (`ChatwootClient` class wrapping `httpx.AsyncClient`) gives full control over attachment uploads (`multipart/form-data`), conversation status transitions, and webhook event parsing, with zero dependency-freshness risk. (MEDIUM-HIGH) |
| **Evolution API** (self-hosted, Docker) | latest stable (track GitHub releases) | Sending/receiving messages on the **internal cartera WhatsApp number** (the one cartera already uses manually — NOT a Meta Business API number) | This is the most important non-obvious finding: Twilio's WhatsApp API and Meta Cloud API both require a **Meta-approved WhatsApp Business number**. The internal cartera number is described as "el número de WhatsApp normal que cartera ya usa" — a personal/non-Business WhatsApp account. Twilio/Meta Cloud API CANNOT send/receive on that number. The standard 2025/2026 way to programmatically drive an existing personal WhatsApp number is via a WhatsApp-Web-protocol bridge: Evolution API (open-source, Node/Baileys under the hood, exposes a clean REST API + webhooks, widely used in the Chatwoot/LATAM ecosystem, self-hostable on Railway alongside Chatwoot) is the standard choice over raw Baileys/whatsapp-web.js because it gives a stable REST/webhook contract instead of requiring you to maintain a Node WebSocket client yourself. Flag this for deeper phase-specific research before committing — confirm with the user whether cartera's number could instead be onboarded as a second real Meta WhatsApp Business number (cleaner, ToS-safe, but requires cartera to give up their current number/app). (MEDIUM — pattern is well-established in market, but not verified against this specific project's risk tolerance for WhatsApp ToS) |
| Redis | 7.x | Conversation/session state (multi-step flow: awaiting-proof → proof-received → awaiting-cartera-validation → resolved), idempotency keys for webhook retries | WhatsApp/Twilio webhooks can be retried/duplicated; a multi-step flow (debtor ↔ bot ↔ cartera ↔ Chatwoot) needs a place to park "what state is this case in" keyed by conversation/case ID. Redis is the standard lightweight choice — avoid building this in Postgres unless the project already has a DB for other reasons (see below). TTL support is a free win for auto-expiring stale "awaiting proof" states. (HIGH — universally standard pattern for this kind of bot) |
| PostgreSQL (optional, if durable audit trail beyond Chatwoot is needed) | 15+ | Persisting case records (debtor, proof file ref, cartera decision, timestamps) independent of Chatwoot | Project constraint says Chatwoot is the system of record for traceability, so Postgres may be unnecessary for v1 — Redis (ephemeral, with reasonable TTL) plus Chatwoot (permanent log) likely suffices. Add Postgres only if you need queryable case history outside Chatwoot's UI/API (e.g., reporting). Defer unless a concrete need emerges. (MEDIUM — judgment call, not a hard requirement) |
| ARQ | >=0.26 | Background job queue (downloading/re-uploading media between Twilio/Chatwoot/Evolution API, retrying failed relays) | Modern asyncio-native alternative to Celery. Use for: fetching a payment-proof media file from Twilio's media URL and re-uploading it to Chatwoot/internal WA without blocking the webhook response (Twilio/Meta expect fast 200 OKs, typically <10-15s). Prefer ARQ over Celery here because the whole stack is async-first and ARQ shares the Redis instance you already need for state — no second broker (RabbitMQ) needed. Use FastAPI's built-in `BackgroundTasks` only for trivial, non-retriable fire-and-forget work; use ARQ for anything that touches external APIs and needs retry semantics (media relay, Chatwoot posting). (HIGH for the pattern, MEDIUM for ARQ specifically vs Celery — both are valid, ARQ is the better fit for this async-only stack) |
| `python-magic` or `filetype` | latest | Detecting MIME type of payment-proof uploads (image vs PDF) before relaying | Twilio gives you a `MediaContentType` already, but validate it server-side (don't trust client-declared MIME blindly) before forwarding to cartera/Chatwoot, especially since this accepts arbitrary inbound files from debtors. (MEDIUM) |
| `tenacity` | >=9.0 | Retry logic for outbound calls (Chatwoot API, Evolution API, Twilio) | Standard, well-known retry/backoff library for Python. Wrap all outbound HTTP calls with bounded retries (network blips, Chatwoot self-hosted on Railway can cold-start/restart). (HIGH) |
| `structlog` or stdlib `logging` with JSON formatter | latest | Structured logging across the webhook → state → relay → Chatwoot pipeline | With 3+ external systems in the loop (Twilio, Evolution API, Chatwoot) and a multi-step state machine, structured logs keyed by `case_id`/`conversation_id` are essential for debugging "where did this case get stuck." (MEDIUM — good practice, not domain-specific) |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `ruff` | Linting + formatting | Replaces Black+Flake8+isort in one fast Rust-based tool; current standard for Python projects in 2025/2026. |
| `mypy` or `pyright` | Static typing | Pydantic v2 + FastAPI type hints make this low-friction; catches webhook payload shape mismatches early. |
| `pytest` + `pytest-asyncio` + `respx` (httpx mock) | Testing | `respx` is the standard way to mock outbound `httpx` calls to Twilio/Chatwoot/Evolution API in tests without hitting real services. |
| Docker / Dockerfile | Containerization | Matches existing Railway deployment pattern used for Chatwoot; keep `landa-agent-service` deployable the same way. |
| ngrok or Cloudflare Tunnel | Local webhook testing | Twilio/Chatwoot/Evolution API all need a public HTTPS URL to deliver webhooks during local development. |

## Installation

```bash
# Core
pip install "fastapi[standard]>=0.136,<0.137" "uvicorn[standard]>=0.32" "pydantic>=2.9"

# Integrations
pip install "twilio>=9.10" "httpx>=0.27" "tenacity>=9.0"

# State / background jobs
pip install "redis>=5.0" "arq>=0.26"

# Media handling
pip install "python-magic" 

# Dev dependencies
pip install -D "ruff" "mypy" "pytest" "pytest-asyncio" "respx"
```

(No `chatwoot-sdk` — build a thin internal `httpx`-based client instead; see rationale above.)

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|--------------------------|
| FastAPI | Flask | Only if the team has zero async experience and wants total simplicity — but you lose native async, which matters once you're fanning out to 3 external APIs per message. Not recommended here. |
| Raw `httpx` Chatwoot client | `chatwoot-sdk` (official PyPI package) | Revisit once the SDK has more releases/adoption (currently v0.2.0, Feb 2026) and you've confirmed it supports attachment uploads end-to-end. For now, too immature to bet a production collections flow on. |
| ARQ + Redis | Celery + Redis/RabbitMQ | If the team already runs Celery elsewhere in the LANDA stack (check `lambda-proyect`) and wants one queue technology org-wide — operational consistency can outweigh ARQ's simplicity. |
| Evolution API for internal cartera number | Direct Baileys/whatsapp-web.js integration in this same FastAPI service | Only if you want to avoid running a second service and are comfortable maintaining a Node WebSocket client inside/alongside a Python service — adds complexity for no real benefit; Evolution API already solves this. |
| Evolution API for internal cartera number | Onboard cartera's number as a second real Meta WhatsApp Business number (via Twilio or Meta Cloud API directly) | If cartera is willing to switch to a Business-API-backed number, this is the cleanest, most ToS-compliant, most future-proof option and avoids depending on an unofficial WhatsApp-Web bridge entirely. Strongly consider raising this with the client before building on Evolution API. |
| Redis for state | Postgres-backed state machine | If the team wants every state transition durably queryable/auditable independent of Chatwoot (e.g., for compliance reporting), or if Redis isn't already part of the LANDA infra. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `chatwoot-sdk` (PyPI) as the sole Chatwoot integration | Brand new (v0.2.0, Feb 2026), undocumented attachment support, small community, unverified production track record — a payment-proof relay flow cannot afford a half-supported attachment API. | Thin internal `httpx.AsyncClient` wrapper directly against Chatwoot's documented REST API (`developers.chatwoot.com`). |
| Celery for this service, by default | Heavier ops footprint (separate broker concerns, sync worker model) than needed for a service that's async end-to-end; adds onboarding overhead if the team isn't already running Celery elsewhere. | ARQ (asyncio-native, shares Redis already needed for state) — unless org-wide consistency with an existing Celery deployment is a stronger driver. |
| Twilio WhatsApp Sandbox or a second Twilio WhatsApp Business number for the internal cartera chat | Twilio/Meta WhatsApp Business numbers require Meta Business verification and cannot impersonate/take over an existing personal WhatsApp account cartera already uses day-to-day; you cannot "add Twilio" on top of a number already registered as a normal personal WhatsApp account without migrating/losing that account's history. | Evolution API (WhatsApp-Web bridge) for the internal number, OR negotiate migrating cartera to a true second Business API number if acceptable to the client. |
| Hard-coding Twilio-specific message-sending calls throughout business logic | Directly calling `twilio_client.messages.create(...)` from route handlers/state-machine code ties your domain logic to Twilio's API shape, making the planned Meta Cloud API migration require touching every call site. | A `WhatsAppSender` protocol/interface with a `TwilioWhatsAppSender` implementation today and a `MetaCloudAPISender` implementation later (see Architecture note below). |
| `requests` (sync) inside FastAPI async route handlers | Blocks the event loop on every outbound call, defeating the purpose of using FastAPI/async in the first place; under load this serializes requests that should be concurrent. | `httpx.AsyncClient`, used consistently everywhere. |

## Stack Patterns by Variant

**If the internal cartera number stays a personal (non-Business) WhatsApp account (current assumption):**
- Use Evolution API (self-hosted, e.g. on Railway alongside Chatwoot) as the bridge for that one channel only.
- Treat it as just another "channel adapter" behind the same internal `WhatsAppSender` interface used for the debtor-facing channel — the state machine and business logic should not need to know or care that one channel is Twilio-backed and the other is Evolution-API-backed.
- Isolate this dependency clearly in code/docs as the "unofficial/bridge" channel, since it carries different reliability/ToS risk than the Twilio/Meta-backed debtor channel — flag for monitoring and have a fallback manual process if it goes down.

**If migrating Twilio WhatsApp -> Meta Cloud API directly (planned future work for the debtor-facing number):**
- Define a `WhatsAppSender` abstract base (or `Protocol`) with methods like `send_text()`, `send_media()`, and a corresponding `WhatsAppWebhookParser` to normalize inbound payloads into one internal message schema (regardless of whether the wire format came from Twilio or Meta Cloud API).
- Today: implement `TwilioWhatsAppSender` (wraps `twilio` SDK) and `TwilioWebhookParser` (parses Twilio's form-encoded webhook body, validates via `RequestValidator`).
- Later: implement `MetaCloudAPISender` (wraps Meta's Graph API HTTP calls via `httpx`) and `MetaWebhookParser` (parses Meta's JSON webhook body, validates via the `X-Hub-Signature-256` HMAC header) — swap via config/dependency injection, with zero changes to the state machine, Chatwoot relay logic, or internal cartera channel.
- Keep all Twilio-specific and Meta-specific payload shapes confined to their respective adapter modules; the rest of the codebase should only see your internal normalized message/event schema (a Pydantic model).

**If a durable audit trail beyond Chatwoot's own history becomes a requirement:**
- Add Postgres (e.g. `asyncpg` + `SQLAlchemy 2.0` async ORM, or just raw `asyncpg` for a handful of tables) for case records, keeping Redis purely for ephemeral in-flight state/locks.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|------------------|-------|
| `fastapi>=0.115` | `pydantic>=2.7` (project should target `>=2.9` per FastAPI's current floor) | Confirmed via FastAPI's PyPI/GitHub release notes (checked Apr-Jun 2026 releases); do not pin Pydantic v1, it's unsupported. |
| `twilio>=9.x` | Python 3.7–3.13 | No conflict with Python 3.12 target. |
| `arq` | `redis-py>=5.0` (async client) | ARQ uses `redis.asyncio` under the hood; ensure the redis-py version installed supports the async API (5.x does). |
| Evolution API | Self-hosted, independent service (any language client via REST/webhooks) | No Python package dependency — integrate purely over HTTP/webhooks like any other external API; version-pin the Evolution API Docker image itself, not a Python lib. |

## Sources

- [twilio/twilio-python GitHub releases](https://github.com/twilio/twilio-python/releases) — confirmed current version 9.10.9 (May 2026), Python 3.7-3.13 support. Confidence: HIGH.
- [PyPI: twilio](https://pypi.org/project/twilio/) — version/support confirmation. Confidence: HIGH.
- [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/) and [PyPI: fastapi](https://pypi.org/project/fastapi/) — confirmed 0.136.1 (Apr 2026), Pydantic v2-only, floor raised to pydantic>=2.9.0. Confidence: HIGH.
- [Twilio: Build a Secure Twilio Webhook with Python and FastAPI](https://www.twilio.com/en-us/blog/build-secure-twilio-webhook-python-fastapi) — confirms `RequestValidator` signature-validation pattern as the official best practice. Confidence: HIGH (official Twilio source).
- [PyPI: chatwoot-sdk](https://pypi.org/project/chatwoot-sdk/) — confirmed v0.2.0 (Feb 2026), Python 3.11+, sync/async clients, attachment support undocumented. Confidence: MEDIUM (verified via fetch, but recency/immaturity itself is the finding).
- [Chatwoot Developer Docs - Create a conversation](https://developers.chatwoot.com/api-reference/conversations-api/create-a-conversation) and [Chatwoot Developer Docs - Chatwoot APIs overview](https://developers.chatwoot.com/contributing-guide/chatwoot-apis) — confirms REST API is the stable, documented integration surface. Confidence: HIGH (official docs).
- [Chatwoot - How to use webhooks](https://www.chatwoot.com/hc/user-guide/articles/1677693021-how-to-use-webhooks) and [How to use Agent bots](https://www.chatwoot.com/hc/user-guide/articles/1677497472-how-to-use-agent-bots) — confirms webhook event model (`message_created`, etc.) and AgentBot pending→open handoff pattern relevant to the escalation flow. Confidence: HIGH (official docs).
- [WhiskeySockets/Baileys GitHub](https://github.com/WhiskeySockets/Baileys) and [Evolution API ecosystem coverage](https://gurusup.com/blog/evolution-api-whatsapp) — confirms WhatsApp-Web-protocol bridges (Baileys-based, with Evolution API as the REST-wrapped standard) are the established 2025/2026 pattern for driving non-Business-API WhatsApp numbers programmatically, and that this approach is unofficial/ToS-grey-area. Confidence: MEDIUM (community/ecosystem sources, not official WhatsApp docs — this is inherently an unofficial approach, so "official" verification isn't available; flagged for client risk discussion).
- [Twilio - Migrate phone numbers and WhatsApp senders](https://www.twilio.com/docs/whatsapp/migrate-numbers-and-senders) — confirms Twilio/Meta WhatsApp senders require formal registration/migration, reinforcing that a personal WhatsApp number can't simply be "added" to Twilio. Confidence: HIGH (official Twilio docs).
- ARQ vs Celery comparison: [Managing Background Tasks in FastAPI: BackgroundTasks vs ARQ + Redis](https://davidmuraya.com/blog/fastapi-background-tasks-arq-vs-built-in/) — Confidence: MEDIUM (single blog source, but consistent with general async-Python community consensus).

---
*Stack research for: WhatsApp collections agent (DPG Seguros) integrating Twilio, Chatwoot, and an internal non-Business-API WhatsApp channel*
*Researched: 2026-06-27*
