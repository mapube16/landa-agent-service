# Feature Landscape

**Domain:** WhatsApp collections (cobranza) bot, voice-to-WhatsApp handoff, proof-of-payment validation, Chatwoot escalation — DPG Seguros (Colombia)
**Researched:** 2026-06-27

## Context Note

This is a narrow, already-scoped handoff flow, not a generic "build a cobranza bot" project. PROJECT.md already fixes most of the architecture (voice→WhatsApp handoff, human validates proof via internal WhatsApp chat, Chatwoot for traceability/escalation, no OCR in v1). This document evaluates that scope against what collections bots typically do across LATAM, flags what's missing from the stated requirements, and calls out compliance risk specific to Colombia.

## Table Stakes

Features users (debtor, cartera agent, or compliance) expect. Missing = broken flow or regulatory exposure.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Voice-to-WhatsApp handoff with context carryover | Debtor already said "ya pagué" on the call; repeating themselves on WhatsApp feels broken | Medium | Already in PROJECT.md scope. Needs a shared case/context ID passed from Pipecat to the WhatsApp agent so the bot doesn't re-ask basic facts (policy number, name) |
| Request proof of payment (comprobante) | Core of the flow — bot must ask, accept image/PDF, confirm receipt | Low | Already scoped. WhatsApp Business API supports image/document media natively |
| Forward proof to human validator | Someone has to look at the receipt | Low | Already scoped — reuses cartera's existing WhatsApp number as the validation channel |
| Human validation decision drives bot action (close vs escalate) | This is the entire value proposition — cartera stops manually closing/escalating | Medium | Already scoped. Needs reliable mapping from cartera's free-text/button reply back to the correct debtor conversation |
| Closing/confirmation message on valid payment | Debtor needs clear closure ("gracias, tu pago fue confirmado") — leaving a conversation open with no resolution erodes trust and generates repeat contacts | Low | Already scoped but under-specified — define the exact copy and what "closed" means in Chatwoot (resolved status, not just silence) |
| Escalation message to debtor + handoff to Chatwoot human agent | When proof is invalid or debtor asks for a human, both sides need to know a human is now driving | Medium | Already scoped. The human agent must see full prior context (voice summary + WhatsApp messages + the receipt) inside the same Chatwoot conversation, not a new one |
| Full conversation traceability in Chatwoot | Audit requirement explicit in constraints; also needed for dispute resolution and compliance review | Medium | Already scoped. Includes the **internal bot↔cartera validation chat**, not just the debtor-facing one — currently this isn't explicitly stated as logged separately and could get conflated with the customer thread |
| No-answer call → automatic WhatsApp follow-up | Already scoped: if debtor doesn't pick up, send a WhatsApp explaining DPG tried to reach them about their policy | Low | Table stakes for any omnichannel collections flow — voice-only retry without a fallback channel loses contact rate |
| Timeout / no-response handling on the WhatsApp side | If debtor says "ya pagué" but never sends the comprobante, or cartera never responds to the validation request, the conversation must not hang indefinitely | Medium | **Not explicitly in PROJECT.md scope — should be added.** Needs a defined timeout (e.g., reminder at N hours, escalate to human or mark stale after M hours) on both legs: debtor→bot and bot→cartera |
| Reuse of existing WhatsApp 24-hour session / template messaging rules | WhatsApp Business API (Meta) requires approved message templates to initiate outside the 24h customer service window; free-form replies only work within 24h of the last inbound debtor message | High | Critical and currently invisible in PROJECT.md. The "bot called, debtor didn't answer, so bot sends a WhatsApp" step is an **outbound business-initiated message** — it almost certainly requires a pre-approved template, and templates referencing payment/collections face stricter Meta review |
| Identification of bot vs human in conversation | Increasingly expected/required by platform and regulatory guidance; also reduces debtor confusion when a human takes over mid-thread | Low | Should disclose "estás hablando con un asistente automático" at start of WhatsApp leg, and signal clearly when a human (cartera) takes over |
| Respect for contact-hours and frequency rules (Ley 2300 "Dejen de Fregar") | Colombian law restricts collections contact (calls AND messages framed as payment reminders) to Mon–Fri 7am–7pm, Sat 8am–3pm, no Sundays/holidays, and limits channel-switching/frequency per week | Low–Medium | **Compliance gap risk.** The "no-answer call → auto WhatsApp" feature must respect these windows; sending immediately after a missed call outside permitted hours is a violation. Also: per Superintendencia Financiera guidance, even neutral-sounding "reminder of due date" messages count as cobranza activity and fall under the law |
| Opt-out / channel preference honoring | Ley 2300 requires consumers be contacted only through channels they've authorized; consumers can register to limit/avoid contact ("Dejen de Fregar" registry) | Medium | Need to check debtor's authorized channel and any registry opt-out status before the voice bot's WhatsApp fallback fires. This may sit outside this microservice (in `lambda-proyect` or DPG's CRM) but the agent must respect a flag if present |

## Differentiators

Features that go beyond the stated v1 scope but are common in mature cobranza-bot products and worth flagging for later phases.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| OCR / automated proof validation against policy system (SoftSeguros) | Removes human bottleneck entirely for clear-cut cases | High | Explicitly out of scope for v1 per PROJECT.md — correctly deferred. Revisit once base flow is validated |
| Payment link generation in-chat | Lets debtor pay directly instead of paying elsewhere and sending proof | Medium–High | Not in current scope (this flow assumes payment already happened before bot contact). Could be a strong v2 differentiator if DPG wants proactive collection, not just confirmation of claimed payment |
| Promise-to-pay (PTP) capture with scheduled follow-up | Common cobranza pattern: debtor commits to a date, bot reminds automatically | Medium | Not in scope; debtor in this flow has already claimed payment, not promised future payment. Worth considering if voice bot also handles "I'll pay next week" cases |
| Sentiment/urgency detection to fast-track escalation | Catches frustrated/angry debtors before they have a bad experience with a bot | Medium | Claude already powers decisions per PROJECT.md constraints — a lightweight sentiment check before defaulting to "request proof" could improve UX cheaply |
| Analytics/dashboard on validation turnaround time, resolution rate | Cartera lead visibility into how fast cases close | Medium | Explicitly out of scope for v1 (no new dashboard) — correctly deferred since Chatwoot already provides conversation-level visibility |
| Multi-language support | Not relevant — single market (Colombia, Spanish) | N/A | Skip |

## Anti-Features

Features to explicitly NOT build, consistent with PROJECT.md's existing exclusions plus risks observed in the broader ecosystem.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Automated/OCR payment validation in v1 | Already excluded — automating against SoftSeguros adds integration risk and false-positive/negative risk before the base flow is proven | Keep human-in-the-loop validation via the existing cartera WhatsApp number, as scoped |
| New dashboard/UI for comprobante review | Already excluded — adds tool-switching cost for cartera, duplicates Chatwoot's purpose | Route validation through the existing WhatsApp number cartera already uses |
| Letting the bot make the final valid/invalid call on a payment | Legal/financial risk if bot wrongly closes a case that wasn't actually paid; erodes trust in the system | Human always makes the validation decision (already a hard constraint in PROJECT.md) |
| Aggressive multi-channel chasing (call + WhatsApp + SMS same day) | Violates Ley 2300's channel-switching/frequency restrictions; also a known driver of "block" reactions on WhatsApp generally | Single fallback channel (WhatsApp) per missed-call event, with hour/day constraints respected |
| Treating the no-answer WhatsApp message as a free-form/non-template send | Will fail or get the number flagged once the 24h session window isn't open (no prior inbound message from debtor that day) | Use a Meta-approved template specifically for this notification, reviewed against WhatsApp's commerce/collections policy |
| Generic marketing-style follow-up nudges (multiple reminders in short window) | Ecosystem evidence shows aggressive nudge sequences (5-6 messages) drive opt-outs/blocks; also conflicts with Colombian frequency rules | One bounded reminder/timeout escalation per stalled step, not a drip campaign |
| Building this as a multi-tenant/generalized cobranza product now | Already excluded — DPG-specific paths (cartera's number, SoftSeguros references, DPG copy) would need abstraction work not justified until proven | Hardcode DPG-specific config for v1; generalize only if a second client appears |

## Feature Dependencies

```
Voice bot handoff (context ID) → WhatsApp agent continues conversation
WhatsApp agent requests comprobante → Forward to cartera validation chat → Cartera responds
Cartera responds "valid" → Close conversation + confirmation message to debtor (requires Chatwoot "resolved" state)
Cartera responds "invalid" / debtor asks for human → Escalate to Chatwoot human agent (requires full context already logged in same conversation)
No-answer on call → WhatsApp fallback message (requires: pre-approved Meta template + contact-hours check + channel-preference check)
Timeout on debtor (no comprobante sent) → Reminder or stale-mark (requires defined timeout policy — not yet in PROJECT.md)
Timeout on cartera (no validation response) → Reminder or auto-escalate (requires defined timeout policy — not yet in PROJECT.md)
All of the above → Logged as traceable Chatwoot conversation (requires distinguishing debtor-facing thread from internal bot↔cartera thread)
```

## MVP Recommendation

Prioritize (aligned with PROJECT.md's "Active" requirements):
1. Voice→WhatsApp handoff with context carryover
2. Comprobante request → forward to cartera → human decision → close or escalate (the core loop)
3. No-answer call → WhatsApp notification, **using a pre-approved Meta template and respecting Ley 2300 contact-hour windows**
4. Full Chatwoot traceability for both the debtor-facing and internal cartera-validation threads

Add to scope before launch (currently gaps, not in PROJECT.md's Active list):
- Timeout/no-response handling on both legs (debtor not sending proof; cartera not responding) — without this the flow can silently stall
- Bot self-disclosure ("estás hablando con un asistente automático") at the start of the WhatsApp leg
- A contact-hours/frequency check before sending the no-answer fallback message, to comply with Ley 2300

Defer:
- OCR/automated validation — already correctly deferred per PROJECT.md
- Payment links / promise-to-pay capture — out of scope for this flow (debtor has already claimed payment)
- New dashboards/analytics — Chatwoot already covers this need

## Sources

- [Mensajes de cobranza por WhatsApp — Blip](https://www.blip.ai/blog/es/chatbots/mensajes-de-cobranza-por-whatsapp/) — MEDIUM confidence, vendor blog, consistent with other sources
- [Cómo reclamar pagos por WhatsApp con un chatbot — Aunoa](https://aunoa.ai/blog/chatbot-con-ia-para-reclamar-cobros-por-whatsapp/) — MEDIUM confidence
- [Procesos de cobranza en WhatsApp — Atomchat](https://blog.atomchat.io/procesos-de-cobranza-en-whatsapp/) — MEDIUM confidence
- [WhatsApp Debt Collection and Omnichannel — Moveo.AI](https://moveo.ai/blog-new/whatsapp-debt-collection) — MEDIUM confidence, describes escalation and confirmation patterns consistent across sources
- [WhatsApp for Debt collection — BankBuddy](https://bankbuddy.ai/blogs/WhatsApp-for-Debt-collection) — MEDIUM confidence
- [Why WhatsApp Is Successful for Debt Collection — Webio](https://www.webio.com/blog/why-whatsapp-is-successful-for-debt-collection-customer-conversations) — MEDIUM confidence
- [Ley 2300 de 2023 — Función Pública (texto oficial)](https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=213990) — HIGH confidence, official legal text
- [Ley 2300 de 2023 — Alcaldía de Bogotá (texto oficial)](https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=143903) — HIGH confidence, official legal text
- [Camacol summary of Ley 2300 measures](https://camacol.co/sites/default/files/descargables/Ley%202300%20de%202023-Medidas%20para%20la%20Gesti%C3%B3n%20de%20Cobranza%20y%20Env%C3%ADo%20de%20Mensajes%20Publicitarios%20y%20Comerciales.pdf) — MEDIUM-HIGH confidence, industry association summary of contact-hour rules
- [Mensajes que recuerdan obligaciones son gestiones de cobranza — Ámbito Jurídico](https://www.ambitojuridico.com/noticias/mercantil/mensajes-que-recuerdan-obligaciones-y-fechas-de-vencimiento-son-gestiones-de) — HIGH confidence, confirms Superintendencia Financiera position that reminder messages count as collections activity under Ley 2300
- [Ley Dejen de Fregar registry — Infobae](https://www.infobae.com/colombia/2025/03/25/si-es-moroso-en-colombia-la-ley-dejen-de-fregar-le-permite-evitar-llamadas-y-mensajes-no-deseados-asi-puede-inscribirse/) — MEDIUM confidence, news summary
- [WhatsApp Chatbot Rules 2026 — Conferbot](https://www.conferbot.com/blog/whatsapp-chatbot-rules-2026) — LOW-MEDIUM confidence, vendor blog on disclosure/escalation norms
- [WhatsApp AI Bot Ban 2026 — MoltFlow](https://molt.waiflow.app/blog/whatsapp-2026-ai-chatbot-compliance) — LOW confidence, vendor blog, not independently verified against Meta's official policy — flagged for follow-up verification against Meta's official WhatsApp Business Policy if template approval becomes a blocker
