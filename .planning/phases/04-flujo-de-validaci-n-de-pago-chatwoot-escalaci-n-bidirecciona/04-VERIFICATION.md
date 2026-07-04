---
phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
verified: 2026-07-03T05:30:00Z
status: passed
score: 8/8 must-haves verified
gaps:
  - truth: "Comprobante fuera de horario llega a cartera cuando abre la ventana laboral"
    status: resolved
    commit: 3bdd829
    fix: |
      (a) nodes.py off-hours branch now sets case.status='awaiting_cartera' alongside
      work_hours_due_at, so check_pending_cases (status='awaiting_cartera' query) finds
      the deferred case. (b) Extracted forward_case_to_cartera() helper used by both
      node_forward_to_cartera and scheduler. (c) scheduler.check_pending_cases now calls
      forward_case_to_cartera for cases with cartera_message_wamid=None (deferred cases
      that never had their media forwarded). Tests: test_off_hours_forward_sets_awaiting_
      cartera_status and test_scheduler_fires_media_forward_for_deferred_case.

  - truth: "Agente humano en Chatwoot puede ver el comprobante del cliente"
    status: resolved
    commit: 230f349
    fix: |
      _handle_comprobante (webhooks/meta.py) now enqueues mirror_inbound after
      successfully enqueuing process_attachment. Caption is
      '[comprobante recibido: {mime_type}]'. Mirror failure is non-fatal: exception
      is caught, warning logged, comprobante path continues. Tests:
      test_comprobante_image_enqueues_mirror_inbound,
      test_comprobante_pdf_enqueues_mirror_inbound,
      test_mirror_inbound_failure_does_not_fail_comprobante_path.

  - truth: "Un fallo de Meta 4xx al reenviar a cartera no deja el caso en estado indefinido"
    status: resolved
    commit: 3bdd829
    fix: |
      upload/send loop extracted to forward_case_to_cartera() helper (nodes.py) which
      wraps the loop in try/except. On exception: logs error, escalates via Chatwoot
      (get_or_create_conversation + post_message private note 'forward a cartera fallo
      — case_id=...'), sets case.status='escalated' + escalated_at, returns
      {"payment_status": "escalated"}. Test: test_send_media_exception_escalates_to_chatwoot.
human_verification:
  - test: "Confirmar estado final en produccion: criterio 1 happy path (comprobante aprobado)"
    expected: "Tras fix de 0db43b6, el cliente recibe 'Tu pago fue confirmado' en < 10s despues del tap 'Aprobar' de cartera"
    why_human: "Smoke de live vivo pendiente segun 04-SMOKE-RESULTS.md criterio 1 (confirmacion al cliente: re-test pendiente)"
  - test: "Template Meta voice_no_answer_followup: verificar estado APPROVED en Meta Business Manager"
    expected: "Template en estado APPROVED; envio de prueba desde /case/handoff/no_answer llega al telefono del cliente"
    why_human: "Estado del template no verificado en vivo segun 04-SMOKE-RESULTS.md (pendientes de infra/ops)"
---

# Phase 04 Verification Report

**Phase Goal:** Cliente puede enviar comprobante por WhatsApp, cartera valida via su numero existente, el bot cierra o escala. Humano en Chatwoot puede tomar control de la conversacion y sus respuestas llegan al cliente.

**Verified:** 2026-07-03T05:30:00Z
**Status:** passed (gaps resolved 2026-07-04)
**Re-verification:** Gap closure — commits 3bdd829 (GAP 1+3) and 230f349 (GAP 2)

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Cliente envia comprobante, llega a cartera (en horario laboral) | VERIFIED | `node_forward_to_cartera` uploads via `meta.upload_media` + `meta.send_media` with 3 action buttons; `_handle_comprobante` enqueues `process_attachment`; smoke criterion 3 OK in live |
| 2 | Cartera responde valido → cliente recibe confirmacion, caso cerrado en Chatwoot | VERIFIED | `node_confirming` emits AIMessage `send_to_client=True`, `payment_approved=True`; `_dispatch_client_message` in cartera.py dispatches via firewall; fix `0db43b6` closed dispatch gap; integration test `test_happy_path_approve` passes |
| 3 | Cartera responde no valido → cliente recibe aviso, caso asignado en Chatwoot | VERIFIED | `node_payment_escalate` opens Chatwoot conv + posts note + emits escalation AIMessage; integration test `test_reject_path_escalates` passes |
| 4 | Numero spoofeado simulando cartera es rechazado silenciosamente | VERIFIED | `_get_cartera_allowlist()` en meta.py verifica antes de rutear a `handle_cartera_message`; spoofed numbers reach client-allowlist or silent drop; smoke criterion 3 OK in live; integration test `test_spoofed_cartera_number_silently_dropped` passes |
| 5 | Agente humano responde desde Chatwoot, mensaje llega al cliente por WhatsApp | VERIFIED | `POST /webhooks/chatwoot` con HMAC correcto (fix `e802947`) + loop-prevention via sender.type filter; `_resolve_and_relay` sends via `meta.send_text`; smoke criterion 4 OK in live ("chatwoot.webhook.relayed", mensaje recibido en telefono) |
| 6 | Comprobante fuera de horario es reenviado a cartera cuando abre la ventana laboral | RESOLVED | Off-hours path setea `status='awaiting_cartera'`; scheduler llama `forward_case_to_cartera` para casos con `cartera_message_wamid=None`. Commit 3bdd829. |
| 7 | Agente humano en Chatwoot puede ver el comprobante del cliente | RESOLVED | `_handle_comprobante` encola `mirror_inbound` con caption `[comprobante recibido: {mime_type}]` tras `process_attachment`. Commit 230f349. |
| 8 | Meta 4xx en forward a cartera no deja el caso indefinido | RESOLVED | `forward_case_to_cartera()` helper envuelve loop en try/except; escala a Chatwoot y setea `status='escalated'`. Commit 3bdd829. |

**Score: 8/8 truths verified**

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/features/payment/graph.py` | Payment subgraph 5 nodos | VERIFIED | Nodes + edges + conditional routing correctos |
| `app/features/payment/nodes.py` | 5 funciones de nodo | VERIFIED (with gaps) | node_receive_comprobante, node_awaiting_cartera, node_confirming, node_payment_escalate implementados; node_forward_to_cartera tiene Bug 1 y Bug 3 |
| `app/features/payment/scheduler.py` | check_pending_cases + cleanup_attachments_90d | VERIFIED (with gap) | Logica de reminder/escalation implementada pero query de status incorrecto para deferred cases |
| `app/features/payment/cartera.py` | parse_button_id + resume_payment_interrupt + handle_cartera_message | VERIFIED | Logic correcta, dispatch de _dispatch_client_message wired con output firewall |
| `app/features/payment/attachment.py` | Magic-byte validator + MIME allowlist | VERIFIED | JPEG/PNG/WebP/PDF; validates y rechaza archivos invalidos |
| `app/features/payment/storage.py` | store_attachment | VERIFIED | Guarda en disco con hash sha256 |
| `app/features/payment/business_hours.py` | is_business_time + next_business_window_after | VERIFIED | Puro, TZ-aware, testeable |
| `app/webhooks/meta.py` | Cartera allowlist + comprobante branch | VERIFIED (with gap) | Routing correcto; comprobante branch enqueues process_attachment pero no mirror_inbound |
| `app/webhooks/chatwoot.py` | HMAC + dedup + relay bidireccional | VERIFIED | HMAC dual-form (fix e802947), loop-prevention, attachment relay |
| `app/webhooks/handoff.py` | POST /case/handoff/no_answer | VERIFIED | Bearer auth, UPSERT idempotente, send_template wired |
| `app/security/output_firewall.py` | check_outbound determinista | VERIFIED | Patron compilado, wired en _send_outbound + mirror_outbound |
| `app/memory/case_store.py` | ORM Case + Attachment | VERIFIED | Ambos modelos con columnas correctas incluyendo status check constraint |
| `tests/integration/test_payment_e2e.py` | 6 integration tests criterios Phase 4 | VERIFIED | 6 tests cubren los 6 success criteria del ROADMAP; 299/299 pasan |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| Meta webhook | process_attachment ARQ job | `_handle_comprobante` → `arq.enqueue_job` | WIRED | webhooks/meta.py:497 |
| process_attachment | payment graph | `build_qa_graph().compile(checkpointer)` | WIRED | worker.py:152 |
| node_forward_to_cartera | meta.send_media | forward_case_to_cartera helper | WIRED (with error handling) | try/except escalates to Chatwoot — 3bdd829 |
| cartera button tap | resume_payment_interrupt | webhooks/meta.py → handle_cartera_message → resume | WIRED | cartera.py:130 |
| resume_payment_interrupt | client dispatch | `_dispatch_client_message` + `check_outbound` | WIRED | cartera.py:151 — fix 0db43b6 |
| Chatwoot human reply | Meta send_text | webhooks/chatwoot.py → `_resolve_and_relay` | WIRED | chatwoot.py:168 |
| off-hours defer | check_pending_cases cron | case.status='awaiting_cartera' predicate | WIRED | nodes.py off-hours sets status; scheduler calls forward_case_to_cartera — 3bdd829 |
| comprobante inbound | mirror_inbound ARQ job | _handle_comprobante → arq.enqueue_job | WIRED | mirror_inbound enqueued after process_attachment — 230f349 |
| check_outbound | _send_outbound | import + gate | WIRED | webhooks/meta.py:149 |
| check_outbound | mirror_outbound | ARQ job gate | WIRED | worker.py:88 |
| POST /case/handoff/no_answer | send_template | meta.send_template | WIRED | handoff.py:87 |

---

### Requirements Coverage

Phase 4 deliverables from ROADMAP.md Phase 4 section:

| Deliverable | Status | Evidence |
|-------------|--------|----------|
| Payment subgraph: awaiting_receipt → forwarded_to_cartera → awaiting_cartera_review → confirming/escalating | SATISFIED | graph.py 5 nodes + edges |
| Attachments descargados de Meta CDN, guardados en storage, NO pasan por LLM | SATISFIED | node_receive_comprobante + store_attachment; D-27 enforced |
| Forward a cartera con caption incluyendo conversation_id, poliza_id y resumen | SATISFIED | nodes.py:315-319 builds caption |
| Allowlist de numeros cartera | SATISFIED | settings.payment.cartera_phone_allowlist + _get_cartera_allowlist() + allowlist check in meta.py:553 |
| Webhook listener de cartera: parsea respuesta, continua interrupt() | SATISFIED | cartera.py handle_cartera_message + resume_payment_interrupt |
| Pago valido: bot envia confirmacion al cliente | SATISFIED | node_confirming + _dispatch_client_message (fix 0db43b6) |
| Pago invalido: bot escala, crea evento en Chatwoot, asigna agente | SATISFIED | node_payment_escalate |
| Agente humano responde en Chatwoot → mensaje al cliente via Meta | SATISFIED | webhooks/chatwoot.py relay en vivo |
| Idempotencia en confirmacion de cartera | SATISFIED | cartera.py:113 terminal-status check before graph.ainvoke |
| POST /case/handoff/no_answer | SATISFIED | webhooks/handoff.py |
| Forward diferido (fuera de horario) | SATISFIED | nodes.py sets status='awaiting_cartera'; scheduler calls forward_case_to_cartera — 3bdd829 |
| Comprobantes espejados a Chatwoot inbox | SATISFIED | _handle_comprobante encola mirror_inbound — 230f349 |
| send_media con manejo de errores Meta 4xx | SATISFIED | forward_case_to_cartera() try/except → Chatwoot escalation — 3bdd829 |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `app/features/payment/nodes.py` | 256-263 | Off-hours path setea `work_hours_due_at` pero no `case.status='awaiting_cartera'` | FIXED | Resolved in 3bdd829 — status now set in off-hours branch |
| `app/features/payment/nodes.py` | 333-335 | `await meta.send_media(...)` sin try/except dentro de session context | FIXED | Resolved in 3bdd829 — forward_case_to_cartera() helper wraps in try/except |
| `app/webhooks/meta.py` | 477-519 | `_handle_comprobante` no encola `mirror_inbound` | FIXED | Resolved in 230f349 — mirror_inbound enqueued after process_attachment |
| `app/features/payment/scheduler.py` | 121 | Cron reminder envia solo botones (`send_buttons`), no reforwardea el media | FIXED | Resolved in 3bdd829 — scheduler calls forward_case_to_cartera for wamid=None cases |
| Multiple | — | `APP_ENV=dev` en produccion (segun smoke) | Info | Health endpoint reporta `env: dev`; no funcional pero confuso en monitoreo |

---

### Human Verification Required

#### 1. Happy path: confirmacion al cliente (re-test pendiente)

**Test:** Enviar imagen de comprobante desde numero de cliente de prueba. Cartera toca "Aprobar". Verificar que el cliente recibe "Tu pago fue confirmado..." en < 10s.

**Expected:** Cliente recibe el mensaje de confirmacion; Chatwoot muestra la conversacion como `resolved`.

**Why human:** Smoke criterio 1 marcado "PARCIAL en vivo" — la confirmacion al cliente fallaba por el gap de dispatch (arreglado en `0db43b6`). Re-test en vivo no ejecutado aun.

#### 2. Template Meta `voice_no_answer_followup`

**Test:** Verificar en Meta Business Manager que el template esta en estado APPROVED. Enviar un POST a `/case/handoff/no_answer` desde un cliente de prueba y confirmar que el mensaje template llega al telefono.

**Expected:** Template APPROVED; mensaje recibido en telefono de prueba.

**Why human:** 04-SMOKE-RESULTS.md nota que el estado APPROVED del template no ha sido verificado en vivo.

---

## Gaps Summary

**3 gaps resueltos. Phase 4 completa (8/8).**

Los 6 bugs de produccion encontrados durante el smoke vivo fueron arreglados (`dd3285e`, `2351efc`, `e802947`, `0db43b6`). Los 3 gaps de rutas alternativas/robustez fueron cerrados en commits `3bdd829` y `230f349`. El suite creció de 299 a 305 tests.

1. **Gap 1 (Deferred forward) — RESUELTO (3bdd829)** — `forward_case_to_cartera()` helper extraido. Off-hours path setea `case.status='awaiting_cartera'`. Scheduler llama el helper para casos con `cartera_message_wamid=None`. Cartera recibe la imagen/PDF cuando abre la ventana laboral.

2. **Gap 2 (Mirror comprobante) — RESUELTO (230f349)** — `_handle_comprobante` encola `mirror_inbound` con caption `[comprobante recibido: {mime_type}]`. Fallo del mirror es no-fatal. Agentes Chatwoot ahora ven el comprobante en el inbox.

3. **Gap 3 (send_media error handling) — RESUELTO (3bdd829)** — `forward_case_to_cartera()` envuelve el loop en try/except. Meta 4xx/error → escalacion a Chatwoot + `status='escalated'` + retorno terminal. No mas casos stranded.

---

*Verified: 2026-07-03T05:30:00Z*
*Verifier: Claude (gsd-verifier)*
