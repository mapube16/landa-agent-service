# Architecture Research

**Domain:** WhatsApp collections (cobranza) bot, integrated with voice bot and Chatwoot agent inbox, human-in-the-loop payment validation
**Researched:** 2026-06-27
**Confidence:** MEDIUM-HIGH (Chatwoot integration patterns HIGH/official docs; voice→WhatsApp handoff and dual-channel-via-personal-WhatsApp patterns MEDIUM/inferred from general bot-handoff and Chatwoot agent-bot conventions, no direct precedent found for the "cartera personal number" piece)

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         OUT OF SCOPE (exists)                        │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Voice Bot (Pipecat + Twilio Voice + Claude)                  │    │
│  │  Calls debtor → detects "ya pagué" / "ayuda humana" / no-show │    │
│  └───────────────────────────┬────────────────────────────────────┘   │
└──────────────────────────────┼────────────────────────────────────────┘
                                │ handoff signal (case_id, debtor phone,
                                │ outcome: paid_claim | wants_human | no_answer)
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                landa-agent-service (THIS REPO — to build)            │
│  ┌────────────────┐   ┌─────────────────────┐   ┌─────────────────┐  │
│  │ Handoff         │   │ WhatsApp Debtor      │   │ Cartera Bridge  │  │
│  │ Receiver        │──▶│ Conversation Agent   │◀─▶│ (internal chat) │  │
│  │ (FastAPI route) │   │ (Claude-driven)      │   │                 │  │
│  └────────────────┘   └──────────┬──────────┘   └────────┬────────┘  │
│                                   │                        │           │
│                         ┌─────────┴─────────┐    ┌─────────┴───────┐  │
│                         │ Conversation State  │    │ Chatwoot Client │  │
│                         │ Store (case/session)│    │ (REST + webhook)│  │
│                         └─────────────────────┘    └─────────────────┘  │
└────────┬──────────────────────────────────────────────────┬────────────┘
         │                                                   │
         ▼                                                   ▼
┌────────────────────────┐                      ┌─────────────────────────┐
│ WhatsApp Business API  │                      │ Chatwoot (self-hosted,  │
│ (Twilio today,         │                      │ Railway)                │
│ Meta Cloud API later)  │                      │ - Inbox for debtor conv │
│ ↔ Debtor's WhatsApp     │                      │ - Inbox/log for cartera │
└────────────────────────┘                      │   internal chat         │
                                                  │ - Human agent takeover  │
                                                  └────────────┬────────────┘
                                                                │
                                                                ▼
                                                  ┌─────────────────────────┐
                                                  │ Cartera's PERSONAL      │
                                                  │ WhatsApp number         │
                                                  │ (NOT Business API —     │
                                                  │  architecture risk,     │
                                                  │  see below)             │
                                                  └─────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| Handoff Receiver | Accept the voice bot's signal that a call ended with a relevant outcome (payment claim, human request, no-answer); create or look up the debtor's case/session | FastAPI webhook endpoint, validates a shared secret/internal token from the voice bot, writes initial session record |
| WhatsApp Debtor Conversation Agent | Drives the WhatsApp conversation with the debtor: requests proof of payment, interprets debtor replies via Claude, sends confirmation or escalation messages | LLM-driven state machine (not free-form chat) — explicit states: awaiting_proof, proof_sent_to_cartera, awaiting_cartera_decision, closed_paid, escalated |
| Cartera Bridge | Forwards the proof-of-payment (image/PDF + case context) to cartera's chat, parses cartera's yes/no decision, routes outcome back to the debtor agent | Outbound WhatsApp message to a fixed cartera number; inbound webhook from same channel correlated by case_id embedded in the message or by sender+timestamp matching, since it's a single open-ended chat, not a structured form |
| Conversation State / Case Store | Single source of truth for case status across voice→WhatsApp→cartera→Chatwoot; prevents race conditions (e.g., debtor messages again while cartera is still deciding) | Lightweight DB (Postgres/SQLite) keyed by case_id and debtor phone number; one row per active case with a status enum |
| Chatwoot Client | Mirrors every message (debtor-facing and internal cartera chat) into Chatwoot conversations for traceability; toggles conversation status to hand off to a human agent when escalation is needed | Chatwoot Application API (Bearer token) for creating contacts/conversations/messages; Chatwoot Agent Bot webhook for receiving human agent replies sent from inside Chatwoot back to the debtor |
| WhatsApp Channel Adapter | Normalizes inbound/outbound messages across Twilio WhatsApp today and Meta Cloud API later behind one interface | Adapter/strategy pattern: `WhatsAppSender.send(to, body, media)` implemented once per provider, selected by config |

## Recommended Project Structure

```
src/
├── api/
│   ├── voice_handoff.py       # receives handoff signal from Pipecat voice bot
│   ├── whatsapp_webhook.py    # inbound WhatsApp messages (debtor + cartera channel)
│   └── chatwoot_webhook.py    # inbound events from Chatwoot (agent replies, status changes)
├── core/
│   ├── case.py                # Case/session model + status state machine
│   ├── case_repository.py     # persistence (Postgres/SQLite)
│   └── routing.py             # decides debtor-vs-cartera message routing by case_id
├── agents/
│   ├── debtor_agent.py        # Claude-driven logic for debtor-facing conversation
│   └── prompts/               # prompt templates per conversation state
├── integrations/
│   ├── whatsapp/
│   │   ├── base.py            # WhatsAppSender interface
│   │   ├── twilio_adapter.py  # current implementation
│   │   └── meta_cloud_adapter.py  # future implementation
│   └── chatwoot/
│       ├── client.py          # REST calls: create contact/conversation/message, toggle status
│       └── mapper.py          # maps internal Case <-> Chatwoot conversation/contact
├── config.py                  # phone numbers, tokens, feature flags (Twilio vs Meta)
└── main.py                    # FastAPI app, route registration
```

### Structure Rationale

- **api/**: thin layer per inbound trigger source (voice bot, WhatsApp provider, Chatwoot) — keeps each webhook's auth/validation isolated from business logic.
- **core/**: the case state machine is the actual product logic; it must be provider-agnostic so swapping Twilio→Meta or changing the cartera channel later doesn't touch it.
- **integrations/**: isolates the two volatile external dependencies (WhatsApp provider, Chatwoot) behind interfaces so the Twilio→Meta migration and any future "real Business API for cartera" migration are swap-in changes, not rewrites.

## Architectural Patterns

### Pattern 1: Case/Session State Machine, Not Free-Form Chat Routing

**What:** Model each debtor interaction as a `Case` with an explicit status (`awaiting_proof`, `proof_forwarded`, `awaiting_cartera_decision`, `closed_paid`, `escalated`, `closed_no_answer`). All inbound messages (from debtor or from cartera) are first resolved to a `case_id`, then the state machine decides what to do — not ad hoc "if message contains X" logic.
**When to use:** Always here — this is a transactional workflow (collections), not an open-ended assistant. Determinism matters for compliance/traceability with an insurance client.
**Trade-offs:** More upfront design than a pure chatbot, but prevents the two most likely production bugs: double-forwarding the same proof to cartera, and routing cartera's "sí" to the wrong debtor case.

### Pattern 2: Single Cartera Number as a Multiplexed Channel

**What:** Because cartera uses one personal WhatsApp number for all active cases, every outbound message to cartera must embed unambiguous case identification (e.g., "Caso #1234 — Juan Pérez — póliza 5678" plus the proof image), and every inbound reply from cartera must be matched back to the most recent un-decided case unless cartera explicitly references a case number.
**When to use:** Required given the current constraint (no Business API for cartera, no structured UI). This is a v1 compromise, not best practice in general.
**Trade-offs:** Works for low concurrent case volume (a handful of open cases waiting on cartera at once); breaks down if cartera ever has many simultaneous pending validations, because correlating freeform replies ("sí, ese pago está bien") to the correct case becomes ambiguous. Mitigate by requiring cartera to reply in-thread/quote the original message if the provider supports it, or by reducing concurrent open cases.

### Pattern 3: Chatwoot as Mirror + Handoff Switch (Agent Bot Pattern)

**What:** Chatwoot's standard "Agent Bot" integration model: the bot owns the conversation (status `pending` or `bot`-equivalent) while automated, posts every message via the Application API for traceability, and flips the conversation to `open` when a human agent must take over. Humans reply inside Chatwoot; those replies are delivered back to the debtor over WhatsApp via a webhook→send-message loop. Source: [Chatwoot Agent Bots docs](https://www.chatwoot.com/hc/user-guide/articles/1677497472-how-to-use-agent-bots).
**When to use:** For the debtor-facing conversation, this is the right pattern and matches the requirement "misma conversación, sin perder contexto."
**Trade-offs:** Chatwoot's API channel model requires creating a contact + conversation per debtor (keyed on phone number) and posting messages with the correct `message_type` (incoming/outgoing) so both directions render correctly. Mis-tagging direction is a common integration mistake (see Anti-Patterns).

**Example:**
```python
# Pseudocode for mirroring + handoff
async def mirror_to_chatwoot(case: Case, direction: str, body: str, media=None):
    conversation_id = await chatwoot.get_or_create_conversation(case.debtor_phone, case.case_id)
    await chatwoot.create_message(conversation_id, body, media=media,
                                   message_type="incoming" if direction == "from_debtor" else "outgoing")

async def escalate_case(case: Case):
    case.status = "escalated"
    await chatwoot.set_conversation_status(case.chatwoot_conversation_id, "open")  # surfaces to human agents
    await whatsapp.send(case.debtor_phone, "Un asesor humano va a continuar tu caso.")
```

## Data Flow

### End-to-End Flow

```
1. VOICE → WHATSAPP HANDOFF
   Pipecat voice bot detects outcome (paid_claim / wants_human / no_answer)
       ↓ (internal webhook call, authenticated, includes debtor phone + case context)
   landa-agent-service: voice_handoff.py creates/updates Case
       ↓
   Case status = "awaiting_proof" (if paid_claim) | "escalated" (if wants_human) | "closed_no_answer" (if no_answer)

2. WHATSAPP DEBTOR CONVERSATION
   Debtor receives first WhatsApp message (requesting proof, or "no te pudimos contactar")
       ↓ (debtor replies with image/PDF via WhatsApp Business API — Twilio today)
   whatsapp_webhook.py resolves Case by debtor phone number
       ↓
   debtor_agent.py confirms receipt to debtor, mirrors message to Chatwoot

3. PROOF → CARTERA
   Cartera Bridge sends proof + case summary to cartera's personal WhatsApp number
       ↓
   Case status = "awaiting_cartera_decision"
   (this message ALSO mirrored to Chatwoot for traceability, ideally a separate
    "internal" inbox/conversation, not the debtor-facing one)

4. CARTERA DECISION → BACK TO DEBTOR
   Cartera replies "sí"/"no" (+ optional notes) on their personal WhatsApp number
       ↓
   whatsapp_webhook.py resolves which Case this reply belongs to (see Pattern 2)
       ↓
   IF valid:  debtor_agent sends thank-you/confirmation → Case status = "closed_paid"
              → mirrored to Chatwoot, debtor-facing Chatwoot conversation resolved
   IF invalid: debtor_agent tells debtor a human will continue
              → Chatwoot conversation status → "open" (visible to human agents)
              → Case status = "escalated"
              → cartera notified to go review in Chatwoot

5. HUMAN ESCALATION IN CHATWOOT
   Human agent opens the SAME Chatwoot conversation (same debtor number/contact)
       ↓
   Agent replies inside Chatwoot
       ↓ (Chatwoot webhook: message_created, message_type=outgoing, sender=agent)
   chatwoot_webhook.py receives event → whatsapp adapter sends message to debtor
       ↓
   Debtor receives human agent's reply over the same WhatsApp thread (no context loss)
```

### Key Data Flows

1. **Identity correlation:** The debtor's phone number is the join key across voice bot, WhatsApp, and Chatwoot contact. The case_id is the join key for the cartera-side multiplexed channel, since cartera's number has no notion of "contact per debtor" — it's one freeform chat with the bot covering many debtors.
2. **Dual mirroring into Chatwoot:** The debtor-facing conversation and the internal cartera conversation should be two separate Chatwoot conversations/inboxes (e.g., "DPG Cartera Clients" and "DPG Cartera Internal") so a human reviewing escalations sees full debtor context plus a clear internal validation trail, without conflating the two audiences in one thread.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Pilot (DPG only, dozens of active cases/day) | Single FastAPI service, single DB table for cases, synchronous webhook handling is fine. No queue needed. |
| Moderate (hundreds of cases/day, still DPG only) | Introduce a lightweight queue (e.g., Redis/RQ or just async background tasks) to avoid blocking webhook responses on Chatwoot/WhatsApp API calls; add idempotency keys on inbound webhooks (providers retry on slow responses). |
| Multi-tenant (future, out of scope for v1) | Case model needs a `client_id`/tenant field from day one even if unused, since the PROJECT.md explicitly flags multi-tenant as deferred-not-impossible; avoids a painful migration later. |

### Scaling Priorities

1. **First bottleneck:** Cartera's single personal-number channel. As case volume grows, ambiguous correlation of cartera's replies to cases (Pattern 2) becomes the practical ceiling long before any technical infrastructure limit. This is a process/UX bottleneck, not a code one.
2. **Second bottleneck:** Webhook response latency to Twilio/Meta and Chatwoot if Claude calls are made synchronously inside the webhook handler. Move LLM calls to background tasks once volume increases, returning 200 immediately to the webhook sender.

## Anti-Patterns

### Anti-Pattern 1: Treating the Cartera Channel as a Structured API

**What people do:** Assume cartera's replies can be parsed reliably as structured commands (e.g., expecting always "SI" or "NO" exactly).
**Why it's wrong:** It's a normal personal WhatsApp chat with a human; replies will be free text ("listo ya quedó", "no ese no sirve", voice notes, delays, replying to the wrong message). Strict parsing will silently drop or misroute decisions.
**Do this instead:** Use Claude to interpret cartera's reply intent (valid/invalid/unclear) against the specific pending case, and if intent is ambiguous, ask cartera a clarifying follow-up rather than guessing. Always confirm back to cartera what action was taken ("Marcado como pago válido para Caso #1234").

### Anti-Pattern 2: One Chatwoot Conversation for Both Debtor and Cartera Traffic

**What people do:** Mirror both the debtor-facing chat and the internal cartera validation chat into the same Chatwoot conversation thread to save integration effort.
**Why it's wrong:** Mixes two audiences and contexts; a human agent picking up an escalation would see internal cartera back-and-forth interleaved with debtor messages, hurting clarity and potentially leaking internal review notes into what looks like the client-facing thread.
**Do this instead:** Two separate Chatwoot inboxes/conversations per case (client-facing + internal), cross-linked by case_id in conversation metadata/labels, so traceability is preserved without confusing audiences.

### Anti-Pattern 3: Building the Cartera Bridge as a "Temporary Hack" With No Migration Path

**What people do:** Hardcode logic against the personal WhatsApp number's quirks (manual regex parsing, no abstraction) because "it's just v1."
**Why it's wrong:** The PROJECT.md context flags this as an open architecture risk likely to be revisited (e.g., migrating cartera to a structured Business API number, or a dashboard later). A hardcoded integration means a full rewrite later.
**Do this instead:** Put the cartera channel behind the same `WhatsAppSender`/adapter abstraction used for the debtor channel, and isolate the "parse cartera intent" logic in one module (`integrations/chatwoot` adjacent or a dedicated `cartera_bridge.py`) so it can be swapped for a structured form/dashboard or a Business API number later without touching the debtor-facing agent or Chatwoot mirroring logic.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Pipecat Voice Bot | Inbound webhook/internal call from voice bot to this service at call end | Needs a defined contract (case_id or debtor phone + outcome enum) — coordinate this contract with whoever maintains the voice bot, since it's out of scope to build but in scope to integrate with |
| Twilio WhatsApp (current) | Twilio webhook for inbound messages, Twilio API for outbound, status callbacks for delivery | Sandbox/number `+16415416615` already provisioned per PROJECT.md; templates required for session-initiated messages outside the 24h window per WhatsApp Business policy |
| Meta Cloud API (future) | Direct Graph API webhook + send endpoint, replacing Twilio as a thin swap behind the WhatsAppSender adapter | Build the adapter interface now so this migration doesn't touch business logic |
| Cartera's personal WhatsApp number | Same WhatsApp provider/number type as debtor channel OR a separate provider if the personal number can't be onboarded to Business API | **Architecture risk** — see dedicated section below |
| Chatwoot (self-hosted, Railway) | Application API (Bearer token) for outbound (create contact/conversation/message, set status); Chatwoot Agent Bot or generic webhook for inbound (human agent replies, status changes) | Use Chatwoot's API Channel inbox type (not a native WhatsApp inbox in Chatwoot) since the WhatsApp connection itself is owned by `landa-agent-service`, not by Chatwoot directly — Chatwoot is purely the inbox/log/escalation UI here, per [Chatwoot API Channel docs](https://www.chatwoot.com/hc/user-guide/articles/1677839703-how-to-create-an-api-channel-inbox) |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Voice bot ↔ landa-agent-service | HTTP webhook, authenticated (shared secret or signed token) | Define this contract early — it's the literal seam between "already built" and "to build" |
| debtor_agent ↔ cartera_bridge | In-process, via shared Case object/state machine | Not a network boundary; both live in this same FastAPI service |
| landa-agent-service ↔ Chatwoot | HTTP REST (outbound) + HTTP webhook (inbound) | Treat Chatwoot as an external system even though both may be managed by the same team — version-pin the API, handle Chatwoot downtime gracefully (queue/retry rather than blocking the debtor flow) |
| landa-agent-service ↔ WhatsApp provider(s) | HTTP REST (outbound) + HTTP webhook (inbound), abstracted via adapter | Two physical numbers in play (debtor-facing Business API number, cartera's personal number) — likely two different webhook URLs/route handlers even if same provider |

## Architecture Risk: Cartera's Personal (Non-Business-API) WhatsApp Number

This is the single biggest open architecture question for this project and directly affects build order and component design:

- **What it means technically:** WhatsApp Business API (Twilio or Meta Cloud API) requires onboarding a number as a Business API number, with provider-side webhooks, templates, and rate/quality rules. A normal personal WhatsApp number has none of this — there is no official API to send/receive programmatically. In practice this is typically bridged via unofficial automation tools (e.g., Baileys/whatsapp-web.js-style libraries that drive a WhatsApp Web session) or by physically using the WhatsApp Business app with a human-operated client, not a server-to-server API.
- **Why it's a risk:** Unofficial WhatsApp Web automation is against WhatsApp's Terms of Service and carries a real risk of the number being banned, especially if message volume looks automated. It is also far less reliable (session drops, QR re-auth, no delivery guarantees) than the official Business API.
- **Implication for this project:** The cartera bridge component cannot assume the same integration pattern as the debtor-facing Business API channel. Two realistic paths exist:
  1. **Unofficial bridge (fastest, riskiest):** Use an unofficial WhatsApp Web client library to automate cartera's existing personal number. Isolate this completely behind the adapter pattern (Anti-Pattern 3) so it can be ripped out without touching the rest of the system. Treat this as explicitly temporary and flag it to the client as a known risk (number ban, no SLA).
  2. **Migrate cartera to a Business API number (safer, more setup):** Provision a second Business API number (Twilio or Meta) specifically for the internal cartera chat, and have cartera switch to using that number/a simple WhatsApp client pointed at it. This is the architecturally clean answer but requires a process change for the cartera team and possibly client (DPG) buy-in, which the PROJECT.md indicates has NOT yet been decided ("flagged as an open architecture question").
- **Recommendation for roadmap:** This decision (unofficial bridge vs. second Business API number) should be resolved before or during the phase that builds the cartera bridge component — it changes the integration code, the risk profile communicated to the client, and possibly the timeline (Business API number provisioning takes Meta/Twilio approval time). Building the debtor-facing WhatsApp flow does NOT require this decision to be made first, which is why it should not block early phases.

## Suggested Build Order

Given the voice bot already exists and is out of scope, and the cartera channel carries the architecture risk above, the dependency-driven build order is:

1. **Chatwoot integration skeleton** (API channel inbox, contact/conversation/message create, status toggle) — foundational, every other component mirrors into it, and it's a known/stable integration (official API).
2. **Case/session state machine + repository** — core logic, no external dependency, needed before either WhatsApp leg can be meaningfully tested.
3. **WhatsApp debtor-facing flow** (Twilio adapter, webhook receiver, debtor_agent with Claude) — uses the already-provisioned Business API number; this is the "happy path" half of the product and has zero dependency on resolving the cartera architecture risk.
4. **Voice → WhatsApp handoff endpoint** — can be stubbed/tested with a manual trigger until the actual voice bot integration contract is finalized with whoever owns Pipecat; should be built once the debtor-facing flow exists to hand off into.
5. **Cartera bridge** — built last because it depends on resolving the personal-number architecture risk; until that decision is made, this can be stubbed (e.g., a fake "cartera always approves" mode) to keep the rest of the system testable end-to-end.
6. **Human escalation loop (Chatwoot → debtor)** — depends on both Chatwoot skeleton (1) and debtor flow (3); finalize once escalation triggers from the cartera bridge (5) are real, but can be built/tested independently using a manually-triggered "escalate" path.
7. **Meta Cloud API migration** — deferred; build behind the adapter from day one (step 3) so this becomes a config/adapter swap rather than a phase of its own.

## Sources

- [Chatwoot Agent Bots — official user guide](https://www.chatwoot.com/hc/user-guide/articles/1677497472-how-to-use-agent-bots) — HIGH confidence, official docs, confirms pending/open status handoff pattern
- [Chatwoot API Channel inbox — official user guide](https://www.chatwoot.com/hc/user-guide/articles/1677839703-how-to-create-an-api-channel-inbox) — HIGH confidence, official docs, confirms contact/conversation/message creation flow for non-native-channel integrations
- [Chatwoot Developer Docs — API Introduction](https://developers.chatwoot.com/api-reference/introduction) — HIGH confidence, official, Application API vs Client API distinction
- [Chatwoot Webhooks — official user guide](https://www.chatwoot.com/hc/user-guide/articles/1677693021-how-to-use-webhooks) — HIGH confidence, official, message_created/message_updated events
- General chatbot human-handoff pattern literature (trigger detection, routing, context transfer) — MEDIUM confidence, multiple consistent sources, standard industry pattern not specific to this stack
- WhatsApp Business API vs. unofficial Web automation risk — MEDIUM confidence, based on well-established general knowledge of WhatsApp ToS and known unofficial-client risk (number bans); no single authoritative source cited because this is a risk characterization, not a factual API claim — recommend validating current WhatsApp ToS enforcement posture before committing to an unofficial bridge approach

---
*Architecture research for: WhatsApp collections bot with voice handoff, human-in-the-loop payment validation, and Chatwoot escalation*
*Researched: 2026-06-27*
