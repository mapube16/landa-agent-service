# Project Research Summary

**Project:** landa-agent-service
**Domain:** WhatsApp collections (cobranza) agent
**Researched:** 2026-06-27
**Confidence:** MEDIUM-HIGH

## Executive Summary

This is a narrow, already-scoped handoff microservice, not a generic chatbot build: a Pipecat voice bot hands a debtor off to a Claude-driven WhatsApp agent (via Twilio today, Meta Cloud API later), which requests proof of payment, relays it to a human cartera validator, and mirrors everything into Chatwoot for traceability and human escalation. Experts build this class of system as a deterministic case/session state machine, not free-form chat routing, sitting behind provider-agnostic adapters (WhatsAppSender, Chatwoot client) so the two known future migrations (Twilio to Meta Cloud API, and cartera's channel) are config swaps, not rewrites. The recommended stack is Python 3.12 plus FastAPI plus Pydantic v2 plus httpx (all async-native), Redis for ephemeral case state, ARQ for background relay jobs, and a hand-rolled thin Chatwoot REST client (the official chatwoot-sdk is too immature to trust for attachment handling in a production collections flow).

The single biggest risk, surfaced consistently across all four research files, is architectural: cartera's "internal" WhatsApp number is a personal/non-Business-API number, not a sanctioned API channel. Automating it via unofficial WhatsApp-Web bridges (Evolution API/Baileys) is the standard market pattern but is a WhatsApp ToS violation with real ban risk, and a ban would break cartera's existing manual workflow too, not just the new automation. This decision (unofficial bridge vs. migrating cartera to a second Business API number) must be made explicitly with the client before the cartera-bridge component is built, though it does not block earlier phases (Chatwoot skeleton, case state machine, debtor-facing WhatsApp flow can all proceed independently).

Secondary but still critical risks are regulatory and flow-integrity issues: Colombia's Ley 2300 ("Dejen de Fregar") restricts collections-related contact hours/frequency and treats payment reminders as cobranza activity requiring compliance; WhatsApp's 24-hour customer-service window will silently break free-form messages whenever cartera takes more than 24h to validate a proof (routine, not edge-case, given human review latency); and without an explicit, persisted state machine with idempotency/locking, race conditions between cartera's verdict and Chatwoot human takeover will produce contradictory or duplicate messages to debtors. All of these are addressable in the initial implementation of their respective phases, none require a hardening pass added later.

## Key Findings

### Recommended Stack

Python 3.12 plus FastAPI (>=0.115, target latest 0.136.x) plus Pydantic v2 plus Uvicorn form the async-native core, justified because the service fans out to 3+ external APIs (Twilio, Chatwoot, internal WA channel) per message and cannot afford blocking sync calls. Outbound HTTP goes through httpx.AsyncClient everywhere (never requests). Conversation/case state and idempotency keys live in Redis (TTL-friendly, shared with the background job queue); ARQ (asyncio-native) handles media relay and retried external calls rather than Celery, since the whole stack is async-only and no second broker is needed. Twilio's official Python SDK handles the debtor-facing WhatsApp channel (signature validation via RequestValidator, never hand-rolled). Chatwoot integration is a hand-rolled httpx client against Chatwoot's documented REST API; the official chatwoot-sdk PyPI package is too new (v0.2.0) with undocumented attachment support to risk on a payment-proof flow.

**Core technologies:**
- FastAPI + Pydantic v2 + Uvicorn: async webhook receiver/validator for Twilio, Chatwoot, and internal WA payloads, industry standard for this exact workload (HIGH confidence)
- Redis + ARQ: case/session state with TTL, idempotency keys, and async background relay jobs sharing one Redis instance (HIGH confidence)
- twilio SDK (official): outbound send + inbound signature validation for the debtor-facing number (HIGH confidence)
- Hand-rolled httpx Chatwoot client (not chatwoot-sdk): full control over attachment uploads and conversation status transitions (MEDIUM-HIGH confidence)
- Evolution API (self-hosted bridge) for cartera's personal number: standard LATAM pattern for driving non-Business-API WhatsApp numbers, flagged as a risk/decision point, not a settled choice (MEDIUM confidence)

### Expected Features

This is already a tightly-scoped flow per PROJECT.md; research mainly confirms the scope is sound and surfaces a few gaps that should be added before launch, plus one significant compliance dimension (Colombian Ley 2300) that isn't yet visible in the stated requirements.

**Must have (table stakes):**
- Voice-to-WhatsApp handoff with full context carryover (no re-asking the debtor what they already said)
- Comprobante request, forward to cartera, human decision, close or escalate (the core loop)
- Closing/confirmation message on valid payment (with Chatwoot conversation marked "resolved," not just silence)
- Escalation to a human Chatwoot agent with full context, when proof is invalid or debtor requests help
- No-answer call to WhatsApp follow-up, using a pre-approved Meta template and respecting Ley 2300 contact-hour windows (Mon-Fri 7am-7pm, Sat 8am-3pm, no Sun/holidays)
- Full Chatwoot traceability for both the debtor-facing thread AND the internal cartera-validation thread (as two separate conversations/inboxes)
- Timeout/no-response handling on both legs (debtor not sending proof; cartera not responding), currently a gap in PROJECT.md, must be added
- Bot self-disclosure ("estas hablando con un asistente automatico") at the start of the WhatsApp leg

**Should have (competitive, later phases):**
- Lightweight Claude-based sentiment/urgency detection to fast-track frustrated debtors to escalation
- Payment-link generation in-chat (only relevant if scope expands beyond confirming already-claimed payment)

**Defer (v2+, already excluded in PROJECT.md and confirmed correct by research):**
- OCR/automated proof validation against SoftSeguros
- New dashboard/UI for comprobante review (Chatwoot already covers this)
- Promise-to-pay capture with scheduled follow-up
- Multi-tenant/generalized product abstraction

### Architecture Approach

Model every debtor interaction as an explicit, persisted Case state machine (awaiting_proof, proof_forwarded, awaiting_cartera_decision, closed_paid, escalated), never inferred from "last message in conversation." Isolate the two volatile external dependencies, WhatsApp provider and Chatwoot, behind adapter interfaces (WhatsAppSender/WhatsAppWebhookParser, ChatwootClient) so the Twilio-to-Meta Cloud API migration and any future cartera-channel migration are config/dependency-injection swaps, not rewrites. Cartera's single personal number must be treated as a multiplexed channel: every outbound relay message embeds unambiguous case identifiers (case ID, name, policy number), and inbound replies are resolved via Claude-based intent interpretation against the most recent undecided case, never strict "SI"/"NO" string matching.

**Major components:**
1. Handoff Receiver, accepts and validates the voice bot's case handoff signal, creates/updates the Case
2. WhatsApp Debtor Conversation Agent, Claude-driven state machine for the debtor-facing thread (not free-form chat)
3. Cartera Bridge, relays proof + case context to cartera, interprets cartera's free-text verdict, isolated behind the same adapter pattern so it can be swapped for an API-backed channel later
4. Conversation/Case State Store, single source of truth (Redis for ephemeral state, Postgres only if durable audit beyond Chatwoot is needed) preventing race conditions
5. Chatwoot Client, mirrors both debtor-facing and internal cartera threads as two separate conversations, manages explicit status transitions (pending to open) for human handoff

### Critical Pitfalls

1. Cartera's personal/non-API WhatsApp number used as an automation endpoint: unofficial automation (Baileys/Evolution API/whatsapp-web.js-style) violates WhatsApp ToS and risks an unannounced ban that would break cartera's existing manual workflow too. Resolve this as an explicit Phase 0/1 architecture decision with the client (unofficial bridge vs. second Business API number) before building the cartera bridge component, does not block earlier phases.
2. 24-hour customer service window expires mid-flow: cartera's human review latency routinely exceeds 24h since the debtor's last inbound message, silently breaking free-form confirmation/escalation sends. Must pre-register and use Meta-approved templates as a first-class fallback path in the initial implementation of debtor-outcome messaging, not a later hardening pass.
3. Voice-to-WhatsApp handoff loses case context: if the handoff is reduced to "send a WhatsApp message" instead of transferring a structured case object (debtor/policy ID, reason, transcript summary), the WhatsApp agent re-asks what the debtor already said, or worse, attaches the wrong debtor's case via shared/stale phone numbers.
4. Race condition between cartera's verdict and Chatwoot human takeover: without a persisted state machine with idempotency/locking around "act on cartera's verdict" and "escalate to human," concurrent triggers produce contradictory or duplicate debtor-facing messages.
5. Payment-proof relay has no delivery guarantee: WhatsApp media URLs are ephemeral; must download and persist proof assets immediately on receipt, tag every relay message with case identifiers, and deduplicate inbound proofs, otherwise the project recreates the exact "cartera juggling unmatched chats" problem it exists to solve.

## Implications for Roadmap

Based on combined research (especially Architecture's "Suggested Build Order" and Pitfalls' phase mapping), suggested phase structure:

### Phase 1: Chatwoot Integration Skeleton
**Rationale:** Foundational and lowest-risk, official, stable, well-documented API; every other component mirrors into it. Building it first also forces the debtor-vs-cartera conversation separation to be correct from day one.
**Delivers:** API-channel inbox setup, contact/conversation/message creation, explicit status-transition handling (pending to open) with reconciliation check.
**Addresses:** Full conversation traceability (FEATURES.md table stakes); separate debtor-facing vs internal cartera inboxes.
**Avoids:** Pitfall 7 (Chatwoot status mismanagement causing escalations to "fall through" unnoticed).

### Phase 2: Case/Session State Machine + Repository
**Rationale:** Core product logic with zero external dependencies; must exist before either WhatsApp leg can be meaningfully tested, and must be designed as an explicit persisted state machine from the start to avoid retrofitting concurrency safety later.
**Delivers:** Case model with explicit states, repository (Redis for ephemeral state, optionally Postgres for durable audit), idempotency/locking primitives.
**Uses:** Redis + ARQ from STACK.md.
**Implements:** Conversation State / Case Store component from ARCHITECTURE.md.
**Avoids:** Pitfall 5 (bot/human race conditions) and the "infer state from last message" technical-debt trap.

### Phase 3: WhatsApp Debtor-Facing Flow (Twilio)
**Rationale:** Uses the already-provisioned Business API number; zero dependency on resolving the cartera architecture risk, so it can proceed in parallel/before that decision is finalized.
**Delivers:** Twilio adapter + webhook receiver (signature-validated), WhatsAppSender/WhatsAppWebhookParser interfaces (built now so Meta Cloud API migration is a later swap), debtor_agent state-machine logic (request proof, confirm receipt, send outcome), 24h-window/template fallback for outcome messages.
**Addresses:** Core comprobante request/confirmation loop, bot self-disclosure requirement.
**Avoids:** Pitfall 3 (24h window silently dropping outcome messages), build the window-check + template fallback here, not later.

### Phase 4: Voice to WhatsApp Handoff
**Rationale:** Can be stubbed/manually triggered until the voice bot integration contract is finalized with whoever owns Pipecat; built once the debtor-facing flow (Phase 3) exists to hand off into.
**Delivers:** Handoff Receiver endpoint, explicit handoff payload contract (debtor/policy ID, reason, transcript summary), Chatwoot system-note logging of the handoff event.
**Avoids:** Pitfall 4 (context loss causing redundant questions or wrong-debtor case attachment).

### Phase 5: Cartera Bridge
**Rationale:** Deliberately built last because it depends on resolving the architecture risk (personal number vs. API-backed channel); until resolved, stub with a "cartera always approves" fake mode to keep the rest of the system end-to-end testable.
**Delivers:** Proof relay with media persistence (no ephemeral Meta URLs forwarded), per-case message tagging, Claude-based intent interpretation of cartera's free-text verdict (not strict string matching), confirmation-back-to-cartera of action taken.
**Addresses:** Core human-validation loop.
**Avoids:** Pitfall 1 (personal-number ban risk), Pitfall 6 (proof relay losing/duplicating/misattributing media).

### Phase 6: Human Escalation Loop (Chatwoot to Debtor)
**Rationale:** Depends on both the Chatwoot skeleton (Phase 1) and debtor flow (Phase 3); can be built/tested independently via a manually-triggered "escalate" path before the cartera bridge produces real escalation triggers.
**Delivers:** Chatwoot webhook handler for agent replies, status-transition verification + reconciliation/alerting job for stuck-pending conversations.
**Avoids:** Pitfall 7 (status mismanagement) reinforced with an operational safety net.

### Phase Ordering Rationale

- Chatwoot-first ordering is dictated by it being the one component every other piece mirrors into, and the only fully stable/official integration in the stack, de-risk early.
- The case state machine must precede both WhatsApp legs because retrofitting concurrency-safe state into an already-built ad hoc flow is the most expensive class of rework identified in PITFALLS.md.
- The cartera bridge is deliberately last because it is gated on an unresolved client-facing architecture decision (ToS risk vs. Business API migration) that should not block shippable progress on the debtor-facing happy path.
- Compliance (Ley 2300 contact-hour windows, opt-out/channel-preference checks) should be designed into Phase 3 (no-answer fallback messaging) from the start, not bolted on, since it governs when/whether a message can legally be sent at all.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 5 (Cartera Bridge):** Needs /gsd-plan-phase --research-phase 5, the unofficial-bridge-vs-Business-API decision is unresolved, Evolution API specifics and current WhatsApp ToS enforcement posture need validation before committing code.
- **Phase 3 (WhatsApp Debtor Flow):** Needs targeted research on Meta template approval process/timeline for the specific templates required (payment confirmation, escalation notice, no-answer fallback), template content categories affect approval risk and timeline.
- **Phase 4 (Voice Handoff):** Needs a coordination check (not deep technical research) with whoever owns the Pipecat voice bot repo to finalize the handoff contract shape.

Phases with standard, well-documented patterns (skip research-phase):
- **Phase 1 (Chatwoot Skeleton):** Official docs are comprehensive and verified HIGH confidence; standard Agent Bot pattern.
- **Phase 2 (Case State Machine):** Standard software engineering pattern, no external API uncertainty.
- **Phase 6 (Escalation Loop):** Builds directly on Phase 1's verified Chatwoot patterns.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM-HIGH | Core framework/SDK choices (FastAPI, Pydantic, Twilio SDK) verified HIGH via PyPI/GitHub release history and official docs. Chatwoot-sdk-avoidance rationale and Evolution API recommendation are MEDIUM (community/ecosystem sources, not official, since the cartera-bridge approach is inherently unofficial). |
| Features | MEDIUM-HIGH | Core flow features confirmed against PROJECT.md's existing scope (HIGH, it's already decided). Colombian Ley 2300 compliance requirements verified against official legal text (HIGH). Vendor-blog sources on cobranza-bot UX patterns are MEDIUM. |
| Architecture | MEDIUM-HIGH | Chatwoot integration patterns (Agent Bot, API Channel, webhooks) verified HIGH against official docs. Voice-to-WhatsApp handoff and dual-channel-via-personal-WhatsApp patterns are MEDIUM/inferred, no direct precedent found for the specific "cartera personal number" architecture. |
| Pitfalls | MEDIUM-HIGH | WhatsApp 24h-window/template policy and Chatwoot status-transition bugs verified against official docs and firsthand GitHub issue reports (HIGH). Project-specific failure modes (race conditions, handoff context loss) are reasoned from architecture, not yet battle-tested in this exact system (MEDIUM). |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **Cartera's number: unofficial bridge vs. Business API migration:** this is an open client-facing decision, not a research gap per se, but it must be resolved explicitly (in writing) before Phase 5 starts. Flag for the client conversation, not just engineering.
- **Ley 2300 enforcement specifics for this exact contact pattern** (voice-bot no-answer to WhatsApp fallback): legal text is verified HIGH confidence, but the precise interaction between "missed call" and "WhatsApp template send" timing/frequency limits should be validated with DPG's compliance/legal team, not assumed from research alone.
- **Meta template approval content/category for collections-related messages:** templates referencing payment/debt face stricter Meta review; exact approved wording and turnaround time are unknown until submitted, so build in a timeline buffer for Phase 3.
- **Concurrent cartera case volume threshold:** Architecture research flags that the single-cartera-number multiplexed-channel pattern breaks down once concurrent open cases get high enough that free-text correlation becomes ambiguous; no specific number is known yet for DPG's actual case volume, monitor in production and revisit if cartera reports confusion.

## Sources

### Primary (HIGH confidence)
- FastAPI release notes / PyPI
- Twilio official Python SDK releases (GitHub)
- Twilio: Build a Secure Twilio Webhook with Python and FastAPI
- Twilio: Key Concepts for WhatsApp Business Platform
- Twilio: Migrate phone numbers and WhatsApp senders
- Chatwoot Developer Docs (Conversations API, API overview, API Introduction)
- Chatwoot official user guide (Webhooks, Agent Bots, API Channel inbox)
- chatwoot/chatwoot GitHub Issues #12754, #12720 (agent-bot status bugs)
- Ley 2300 de 2023, official legal text (Funcion Publica, Alcaldia de Bogota)
- Ambito Juridico: Superintendencia Financiera position on reminder messages as cobranza activity
- developers.facebook.com: Pricing on the WhatsApp Business Platform
- TechCrunch: WhatsApp changes terms to bar general-purpose chatbots (Oct 2025)

### Secondary (MEDIUM confidence)
- PyPI: chatwoot-sdk (v0.2.0, immaturity finding)
- WhiskeySockets/Baileys GitHub + Evolution API ecosystem coverage
- respond.io: Not All Chatbots Are Banned, WhatsApp's 2026 AI Policy
- bot.space, kaaryaai: WhatsApp API vs unofficial tools risk analyses
- smsmode, Enchant: WhatsApp 24-hour window/template explainers
- Cobranza-bot vendor blogs (Blip, Aunoa, Atomchat, Moveo.AI, BankBuddy, Webio), consistent escalation/confirmation pattern descriptions
- Camacol summary of Ley 2300 contact-hour rules
- ARQ vs Celery comparison (davidmuraya.com blog)

### Tertiary (LOW confidence)
- Conferbot, MoltFlow vendor blogs on WhatsApp chatbot disclosure/compliance rules for 2026, flagged for follow-up verification against Meta's official policy if template approval becomes a blocker
- FormBeep WhatsApp pricing aggregator, Colombia rates need verification against Meta's live rate card before billing decisions

---
*Research completed: 2026-06-27*
*Ready for roadmap: yes*
