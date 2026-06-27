# Pitfalls Research

**Domain:** WhatsApp collections (cobranza) bot with voice handoff, internal human-validation relay over a non-API WhatsApp number, and Chatwoot escalation (DPG Seguros / LANDA)
**Researched:** 2026-06-27
**Confidence:** MEDIUM-HIGH (WhatsApp policy and Chatwoot mechanics verified against current sources; project-specific failure modes inferred from architecture, not yet battle-tested)

## Critical Pitfalls

### Pitfall 1: Internal "cartera" number is a personal/Business-App WhatsApp number being used as an automation endpoint

**What goes wrong:**
The PROJECT.md explicitly states cartera's number is "no Business API" — i.e., the regular WhatsApp Business **App** (or even a personal WhatsApp number), not the Cloud/Business **API**. The plan is for the agent service to programmatically send the payment proof to that number and parse cartera's reply ("sí"/"no") to drive automation. Any non-API automation against the consumer app (browser automation, unofficial libraries like whatsapp-web.js/Baileys, or even manually relaying via a phone someone scripts against) is a direct ToS violation. Meta's detection targets exactly this pattern (unnatural send timing, programmatic session reuse) and the consequence is an unannounced, non-appealable ban of that number — which would simultaneously break cartera's *existing* manual workflow, not just the automation.

**Why it happens:**
Reusing an existing, familiar number feels like zero migration cost ("cartera already uses this chat"). Nobody connects "cartera's number" to "this is the part of the system most likely to get permanently banned" because it's framed as an internal/back-office channel, not customer-facing, so it doesn't get the ToS scrutiny the customer-facing bot gets.

**How to avoid:**
- Never automate sending/receiving on cartera's number via unofficial scraping/automation libraries against personal WhatsApp or the WhatsApp Business App.
- If cartera's number must stay on the consumer WhatsApp Business App, the "automation" must be human-mediated only: the agent forwards the proof via the same WhatsApp Business API number (`+16415416615`) cartera already has visibility into via Chatwoot, OR a dedicated internal inbox in Chatwoot, and a human at cartera clicks/types the response *manually* in their own WhatsApp app — no script touches cartera's session.
- Better alternative: onboard cartera's number to the WhatsApp Business API (or create a second API number) so the bot↔cartera relay is itself a sanctioned API conversation, and surface it inside Chatwoot as its own inbox/conversation rather than literally driving cartera's personal app.
- Flag this decision explicitly to the client as a risk tradeoff before building — "fastest to ship" vs "what happens when that number gets banned mid-collections-cycle."

**Warning signs:**
- Any dependency on an unofficial WhatsApp library (whatsapp-web.js, Baileys, venom-bot, etc.) appears in the stack.
- Cartera's number starts showing "temporarily banned" warnings or message-send failures with no clear API error code (a symptom of WhatsApp Web session bans).
- The integration plan for cartera's number doesn't appear anywhere in the Meta/Twilio account configuration — it's "just WhatsApp on a phone."

**Phase to address:**
Phase 0/1 (architecture decision) — before any code is written. This is a foundational decision, not a later refactor.

---

### Pitfall 2: Treating "bot↔cartera" as a normal business conversation under WhatsApp's messaging policy

**What goes wrong:**
Even if cartera's number is properly migrated to API, the bot-to-cartera relay is internal operational traffic, not customer messaging. Running it through the same compliance model as customer conversations (template categories, 24-hour windows, opt-in) either over-engineers an internal tool or, if skipped entirely on the customer-facing side, risks template rejection/account quality penalties on the customer number.

**Why it happens:**
Teams build one mental model ("send WhatsApp message") and apply it uniformly to both the customer-facing flow and the internal relay, missing that one is governed by strict Meta commerce policy (customer number) and the other is closer to an internal tool that happens to use WhatsApp as the medium.

**How to avoid:**
Architecturally separate the two flows even if they share code: customer-facing messages must respect the 24-hour customer service window and template rules; the internal cartera relay should be designed independently of Meta's customer messaging policy (since it's not really a "customer" conversation) — but only after Pitfall 1 is resolved (i.e., it's on a properly authorized number/channel).

**Warning signs:**
Code paths for "send to debtor" and "send to cartera" share the same message-sending function with no distinction of conversation type, template requirements, or window state.

**Phase to address:**
Phase 1 (architecture) — define the two message flows as distinct subsystems early.

---

### Pitfall 3: 24-hour customer service window expires mid-collections-flow, silently dropping the bot's ability to reach the debtor

**What goes wrong:**
The flow is: voice call ends → bot hands off to WhatsApp → debtor may not respond immediately → bot waits for proof → cartera takes time to validate → bot needs to send a confirmation/escalation message back to the debtor. If more than 24 hours pass since the debtor's last inbound message at any point, the bot can no longer send free-form text (e.g., "tu comprobante fue validado, gracias") — it must use a pre-approved template, or the message silently fails/queues.

**Why it happens:**
The 24-hour rule is "per inbound message from the user," not "per conversation." Developers test happy-path flows that complete in minutes and never hit the window boundary, so production failures only appear when cartera takes hours to respond (which collections review, that depends on a human checking a chat, often does) or when a debtor goes quiet after sending the proof.

**How to avoid:**
- Treat "session window expired" as a first-class state, not an edge case. Before sending any free-form message to the debtor, check time-since-last-inbound and branch to a pre-approved template (e.g., a "utility" category template: "Tu comprobante de pago fue recibido y será confirmado pronto" / "Hemos confirmado tu pago, gracias") if the window is closed.
- Pre-register and get Meta approval for the small set of templates needed for: payment confirmation, escalation notice, and "we tried to call you" (the no-answer fallback already in requirements) — all of these can land outside the 24h window.
- Build a queue/retry layer so a message that fails due to window closure doesn't just disappear — it should fall back to template send or get flagged for cartera/ops visibility.

**Warning signs:**
Debtor never receives the final confirmation/escalation message even though logs show the bot "sent" it; messages with `message_undeliverable` or template-required error codes from the WhatsApp API; cartera reports "the bot didn't close the case" complaints that correlate with cartera's own response latency.

**Phase to address:**
Phase covering "agent sends payment outcome to debtor" — must include window-check + template fallback as part of the initial implementation, not a hardening pass.

---

### Pitfall 4: Voice→WhatsApp handoff loses case context, so the WhatsApp bot starts from zero

**What goes wrong:**
The voice bot (Pipecat) decides to hand off mid-call ("ya pagué" or help request), but if the handoff only triggers "send a WhatsApp message" without passing structured context (debtor identity, policy/case ID, what was said on the call, why it escalated), the WhatsApp agent has to re-derive everything from a blank conversation, ask redundant questions, or — worse — pattern-match the wrong debtor record if phone number alone isn't a reliable case key (shared family phones, wrong/old numbers in SoftSeguros).

**Why it happens:**
Voice and WhatsApp are built as separate systems (literally separate repos per PROJECT.md context — voice already exists, WhatsApp is new). The "integration" gets reduced to "trigger a WhatsApp send" rather than "transfer a case object," because that's the minimum to satisfy the requirement on paper.

**How to avoid:**
- Define an explicit handoff payload/contract between the voice bot and the WhatsApp agent service: debtor ID, case/policy ID, reason for handoff (claimed payment vs requested human help vs no-answer), call transcript summary, timestamp. This should be a real API call or shared data store write, not an inferred state.
- Resolve case identity by debtor/policy ID, not solely by phone number — phone number should be used for routing the WhatsApp message, but the case binding must be explicit so two different people answering the same shared phone don't collide.
- Log the handoff event itself in Chatwoot as a system note so a human reviewing the conversation later sees "this came from a voice call about case X" without digging through other systems.

**Warning signs:**
WhatsApp agent's first message to the debtor asks something the debtor already explained on the call; cases where the wrong debtor's case gets attached to a WhatsApp thread; no record in Chatwoot of *why* a conversation started (looks like an inbound WhatsApp message with no originating context).

**Phase to address:**
Phase covering voice→WhatsApp handoff — design the handoff contract before implementing either side's trigger logic.

---

### Pitfall 5: Race condition between bot auto-close and cartera's/human's manual override

**What goes wrong:**
The bot auto-closes the debtor conversation when cartera replies "sí" (valid), but cartera might also (a) reply late after the bot already escalated to Chatwoot for inactivity, (b) reply twice (e.g., correct a "sí" to "no" after realizing a mistake), or (c) reply while a human agent is *already* mid-conversation with the debtor in Chatwoot after an earlier escalation. Without a single source of truth for "current state of this case," the bot can send a "your payment is confirmed" message to a debtor whose case a human just escalated for a different reason, or double-send confirmation/escalation messages.

**Why it happens:**
The design has two independent triggers that can both act on the same conversation: cartera's reply (via the internal relay) and Chatwoot agent actions (human taking over). If these aren't coordinated through one state machine with locking/idempotency, both paths can fire near-simultaneously, especially when cartera takes their time and a human or automated timeout has already started escalating.

**How to avoid:**
- Model the case as an explicit state machine (e.g., `awaiting_proof → awaiting_cartera_review → confirmed | escalated → closed`) persisted in a database, not inferred from conversation status alone.
- Before acting on cartera's reply, check current case state — if already escalated/closed by another path, cartera's reply should be logged but not trigger a duplicate customer-facing message; instead it should be visible as a note to the human agent now owning the case.
- Use idempotency keys / locking (e.g., a DB transaction or Redis lock) around the "act on cartera's verdict" and "escalate to human" code paths so concurrent triggers can't both win.
- Make the "send confirmation to debtor" action idempotent — check it hasn't already been sent before sending again.

**Warning signs:**
Debtor receives two different bot messages that contradict each other ("your payment is confirmed" followed by "a human will continue your case"); Chatwoot shows an agent already typing in a conversation that the bot then auto-closes underneath them; cartera complains "I already answered this one" on a case that was independently escalated.

**Phase to address:**
Phase covering the bot↔cartera decision loop and Chatwoot escalation — needs an explicit state machine design, not ad hoc status flags, before either trigger path is implemented.

---

### Pitfall 6: Payment-proof relay has no delivery guarantee — image lost, duplicated, or attributed to the wrong case

**What goes wrong:**
The proof (usually an image/PDF) travels: debtor → WhatsApp agent → forwarded to cartera's number → cartera replies → agent acts. Each hop is a place where the artifact can be lost (media download from Meta's API expires/fails), duplicated (debtor sends the same proof twice, or a retry re-forwards it), or misattributed (cartera reviews several debtors' proofs in the same internal chat with no per-case framing, then replies "sí" — ambiguous to which case).

**Why it happens:**
WhatsApp media URLs are not permanent — Meta's media API requires downloading the asset promptly and re-hosting it; if the integration just relays the Meta media ID/URL without persisting the asset, it can expire before cartera even opens the relay message. Plain-text relay to a single internal chat without per-message case framing recreates the exact "cartera juggling chats manually" problem the project is meant to fix, just one level removed.

**How to avoid:**
- Download and persist the proof media (store in your own storage, e.g., S3/Railway volume + DB record) immediately on receipt — never rely on relaying Meta's ephemeral media URL/ID to the internal channel.
- Every message forwarded to cartera must be unambiguously tagged with case/debtor identifiers (name, policy number, case ID) directly in the message text, not just the image alone — and cartera's reply-matching logic must use reply-to/quote-message references (if available) or a case ID cartera echoes back, not just "the most recent message in the chat."
- Deduplicate inbound proofs from the debtor (hash the image, check time-window) before triggering a new relay to cartera, so a debtor resending the same photo doesn't create two parallel reviews.
- Log every hop (received from debtor, forwarded to cartera, cartera's verdict, action taken) with timestamps and message IDs in your own DB — Chatwoot's transcript is for human visibility, not a substitute for an internal audit trail your code can query for the state machine.

**Warning signs:**
Cartera's verdict ("sí"/"no") arrives with no clear back-reference, and code falls back to "whatever case is currently open" guesses; cases where the proof image is reported missing if cartera opens the relay chat more than ~5-30 minutes after forwarding (Meta media URL expiry); two confirmations issued for one proof.

**Phase to address:**
Phase covering proof capture + relay to cartera — build persistence and per-message case tagging as part of the MVP, not deferred hardening.

---

### Pitfall 7: Chatwoot agent-bot status semantics misused, causing conversations to "fall through" without notifying a human

**What goes wrong:**
Chatwoot's agent-bot model creates conversations in `pending` status while the bot owns them, and the bot must explicitly flip status to `open` to hand off to a human. If the integration only sends messages but never correctly manages conversation status (or relies on a Chatwoot version/webhook flow with known bugs around bot-triggered status transitions), an escalated conversation can sit in `pending`/bot-owned state indefinitely — invisible in agents' normal queues — even though the debtor is waiting for a human. Chatwoot itself has had reported issues where conversations get marked `open` unexpectedly "due to an error with the agent bot," and inconsistent behavior switching between `pending` and `open` across versions.

**Why it happens:**
Teams build the "escalate to Chatwoot" feature as "post a message into the Chatwoot conversation" and treat that as equivalent to "a human will see this," without verifying the conversation's `status` field is actually `open` (or assigned) afterward. Chatwoot's agent-bot webhook/status-transition behavior has had real bugs (see chatwoot/chatwoot#12754, #12720) that can silently break this assumption even with correct code.

**How to avoid:**
- After sending the escalation message, explicitly call Chatwoot's conversation update API to set status to `open` (and ideally assign to a team/agent) — never assume posting a message is sufficient.
- Add a reconciliation check: periodically verify that conversations the agent believes it escalated are actually `open`/assigned in Chatwoot, and alert ops if a mismatch is found.
- Pin and test against a specific Chatwoot version; check the Chatwoot changelog for agent-bot/status-related fixes before upgrading self-hosted Railway instance, since this is an area with known regressions.
- Add a Chatwoot-side automation rule (separate from the agent-bot integration) as a safety net — e.g., "if conversation has been pending > N minutes with no agent reply, notify supervisor" — so a missed status transition doesn't go unnoticed indefinitely.

**Warning signs:**
Debtor or cartera reports "nobody answered" on a case the bot logs show as "escalated"; Chatwoot inbox shows conversations stuck in pending with bot-sent messages but no human follow-up; mismatches between your own state machine's "escalated" status and Chatwoot's actual conversation status field.

**Phase to address:**
Phase covering Chatwoot escalation integration — must include status-transition verification and a reconciliation/alerting mechanism, not just "post message to Chatwoot."

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|-----------------|------------------|
| Relay proof to cartera using Meta's raw media URL instead of persisting the asset | Saves a storage integration in week 1 | Lost proofs once URL expires; no audit trail for disputes | Never beyond a throwaway demo |
| Infer case state from "last message in conversation" instead of an explicit DB state machine | Faster to prototype | Race conditions (Pitfall 5), unrecoverable ambiguous states | Acceptable only for a single-debtor manual test, never in any shared/production environment |
| Skip window/template fallback logic, assume bot always replies within 24h | Simpler send-message code | Silent message failures whenever cartera or debtor response is slow | Never — collections review delay makes this routine, not rare |
| Treat cartera's number as "just another WhatsApp chat" without an explicit per-case tagging convention | No extra UI/message formatting work | Cartera's manual matching errors persist — the exact problem this project exists to solve | Never — defeats the project's core value |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|-----------------|-------------------|
| WhatsApp Business API (Twilio, migrating to Meta Cloud API) | Assuming Twilio and Meta Cloud API behave identically for templates/media/window handling | Treat the Twilio→Meta migration as a compatibility-testing phase; verify template approval, media handling, and webhook payload shapes independently on both, don't assume parity |
| Chatwoot Agent Bot API | Posting messages without managing conversation `status`/assignment | Always pair message send with explicit status/assignment update; verify via reconciliation check |
| WhatsApp media API | Relaying ephemeral media URLs/IDs to a second hop (cartera) | Download and persist media to your own storage immediately on receipt |
| Pipecat voice bot → WhatsApp agent handoff | Triggering WhatsApp send with only a phone number, no case context | Pass an explicit handoff payload (case ID, reason, transcript summary) via API/shared store |
| Cartera's internal WhatsApp number | Automating sends/reads on a personal/Business-App number via unofficial libraries | Migrate to API-backed channel, or keep entirely human-mediated with no scripted access to that session |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|-----------------|
| Single internal WhatsApp chat for all cartera review, no per-case framing | Cartera has to scroll/search to match replies to cases | Tag every relayed message with case ID/policy number in text; consider one Chatwoot conversation per case even internally | Breaks immediately once more than a handful of concurrent cases are in review at once (likely day 1 in production, not a scale issue) |
| No rate limiting/backoff on debtor-facing template sends after window closes | Hitting Meta rate limits or template pacing tiers during bulk no-answer notification campaigns | Respect Meta's messaging tier limits (e.g., 250/1K/10K/unlimited business-initiated conversations per 24h based on phone number quality tier) and stagger sends | Breaks when daily debtor volume approaches your number's current messaging tier limit |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Storing payment proof images without access control / encryption at rest | Sensitive financial/PII documents (often include bank account info) exposed if storage bucket misconfigured | Store proofs in access-controlled storage, restrict to service role, avoid public URLs even temporarily |
| Trusting cartera's free-text reply ("sí"/"no") without bounding intent recognition | A typo, ambiguous message, or off-topic reply from cartera misinterpreted as a verdict, triggering wrong customer-facing action | Use a constrained reply format (keywords, button-style quick replies, or explicit case ID echo) and fail safe (escalate to human review) on ambiguous input rather than guessing |
| No webhook signature verification on inbound Meta/Twilio/Chatwoot webhooks | Spoofed webhook could trigger false payment confirmations or fake escalations | Verify webhook signatures (Meta App Secret signature, Twilio request validation, Chatwoot HMAC if configured) on every inbound webhook handler |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-------------------|
| Debtor left in silence while cartera reviews proof, no acknowledgment | Debtor thinks the bot is broken or ignoring them, may call back or complain | Send an immediate "recibimos tu comprobante, te confirmamos pronto" acknowledgment the moment proof is received, separate from the final verdict message |
| Voice bot says "te seguimos por WhatsApp" but WhatsApp message arrives minutes later or not at all | Debtor feels abandoned mid-process, may not realize they need to check WhatsApp | Trigger the WhatsApp handoff message synchronously/near-real-time from the call, and have the voice bot only promise the handoff after confirming the WhatsApp send succeeded |
| Escalation message to debtor is generic ("a human will continue") with no expectation-setting on timing | Debtor doesn't know if that means minutes or days, may re-contact repeatedly | Set a concrete expectation in the escalation template (e.g., business hours, approximate response time) |

## "Looks Done But Isn't" Checklist

- [ ] **Voice→WhatsApp handoff:** Often missing structured case context transfer — verify the WhatsApp agent's first message doesn't ask the debtor to repeat what they told the voice bot.
- [ ] **Payment confirmation flow:** Often missing 24-hour window/template fallback — verify by testing a flow where cartera takes >24h to respond and confirming the debtor still receives a message.
- [ ] **Proof relay to cartera:** Often missing per-case tagging and media persistence — verify by sending two different debtors' proofs to cartera in quick succession and confirming cartera's "sí"/"no" reply maps unambiguously to the correct case.
- [ ] **Chatwoot escalation:** Often missing explicit status transition — verify by checking Chatwoot's conversation `status` field (not just message presence) after an escalation, and confirm a real agent gets a queue/notification.
- [ ] **Cartera's internal number compliance:** Often "looks fine" because nothing has broken yet — verify by confirming explicitly whether that number is on the WhatsApp Business API/Cloud API or the consumer app, and documenting the ban risk if it's the latter.
- [ ] **Race condition handling:** Often missing entirely until two events arrive close together in real usage — verify with a deliberate concurrent test: trigger a Chatwoot human takeover and a cartera "sí" reply on the same case within seconds of each other.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|-----------------|
| Cartera's number gets banned | HIGH | Re-onboard cartera to WhatsApp Business API on a new/recovered number, rebuild the internal relay as a sanctioned channel, communicate downtime to cartera team, audit which cases were mid-review when ban hit |
| Lost payment proof (expired media URL) | MEDIUM | Re-request proof from debtor via template message ("no pudimos procesar tu comprobante, ¿puedes reenviarlo?"), add persistence fix to prevent recurrence |
| Duplicate confirmation/escalation messages sent to debtor | LOW-MEDIUM | Send a clarifying follow-up message, manually verify case state, add idempotency check before next deploy |
| Conversation stuck in pending in Chatwoot, never surfaced to human | MEDIUM | Add a scheduled job that audits long-pending bot-owned conversations and force-escalates/alerts ops, backfill missed cases manually |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|---------------|
| Personal/non-API number used for automation (Pitfall 1) | Phase 0/1 — architecture decision | Confirm in writing which channel type (API vs consumer app) cartera's number uses; if consumer app, confirm zero scripted access to that session |
| Customer vs internal message-policy conflation (Pitfall 2) | Phase 1 — architecture | Code review confirms separate handling paths for customer-facing vs internal relay messages |
| 24h window expiry mid-flow (Pitfall 3) | Phase covering debtor outcome messaging | Test case: delay cartera's response >24h, confirm debtor still receives templated message, not a silent failure |
| Voice→WhatsApp context loss (Pitfall 4) | Phase covering voice-to-WhatsApp handoff | Test case: debtor's WhatsApp first message references call context without debtor repeating themselves |
| Bot/human race condition (Pitfall 5) | Phase covering bot↔cartera decision loop + Chatwoot escalation | Concurrency test: simulate simultaneous cartera reply and human takeover, confirm no duplicate/contradictory debtor messages |
| Proof relay reliability (Pitfall 6) | Phase covering proof capture + relay | Test case: two debtors' proofs forwarded in sequence, cartera's replies map to correct case 100% of the time; media persisted and retrievable after 1 hour |
| Chatwoot status mismanagement (Pitfall 7) | Phase covering Chatwoot escalation integration | After escalation test, query Chatwoot API directly to confirm conversation status is `open`/assigned, not just that a message was posted |

## Sources

- [smsmode: WhatsApp Business API 24-hour window and templates](https://www.smsmode.com/en/whatsapp-business-api-customer-care-window-ou-templates-comment-les-utiliser/) — MEDIUM confidence (third-party, consistent with Meta's documented behavior)
- [Twilio: Key Concepts for WhatsApp Business Platform](https://www.twilio.com/docs/whatsapp/key-concepts) — HIGH confidence (official partner docs)
- [Enchant: WhatsApp Business Platform 24 Hour Rule](https://www.enchant.com/whatsapp-business-platform-24-hour-rule) — MEDIUM confidence
- [bot.space: WhatsApp API vs Unofficial Tools risk analysis](https://www.bot.space/blog/whatsapp-api-vs-unofficial-tools-a-complete-risk-reward-analysis-for-2025) — MEDIUM confidence
- [kaaryaai: Is WhatsApp Automation Safe?](https://www.kaaryaai.com/blog/is-whatsapp-automation-safe/) — MEDIUM confidence
- [TechCrunch: WhatsApp changes terms to bar general-purpose chatbots](https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform/) — HIGH confidence (reputable tech press, corroborated by multiple sources)
- [respond.io: Not All Chatbots Are Banned — WhatsApp's 2026 AI Policy](https://respond.io/blog/whatsapp-general-purpose-chatbots-ban) — MEDIUM-HIGH confidence (consistent with TechCrunch reporting)
- [Chatwoot user guide: How to use Agent bots](https://www.chatwoot.com/hc/user-guide/articles/1677497472-how-to-use-agent-bots) — HIGH confidence (official docs)
- [chatwoot/chatwoot GitHub Issue #12754 — conversation marked open due to agent bot error](https://github.com/chatwoot/chatwoot/issues/12754) — HIGH confidence (firsthand bug report on the exact mechanism this project depends on)
- [chatwoot/chatwoot GitHub Issue #12720](https://github.com/chatwoot/chatwoot/issues/12720) — HIGH confidence (corroborating bug report)
- [developers.facebook.com: Pricing on the WhatsApp Business Platform](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing) — HIGH confidence (official Meta docs, confirms July 2025 shift to per-message pricing)
- [FormBeep: Meta WhatsApp Business API Pricing 2026](https://formbeep.com/whatsapp-api-pricing/) — MEDIUM confidence (third-party aggregator, Colombia rates need verification against Meta's live rate card before billing decisions)

---
*Pitfalls research for: WhatsApp collections bot with voice handoff and Chatwoot escalation (DPG Seguros / LANDA)*
*Researched: 2026-06-27*
