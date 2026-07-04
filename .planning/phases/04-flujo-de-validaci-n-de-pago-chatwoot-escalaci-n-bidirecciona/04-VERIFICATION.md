---
phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona
verified: 2026-07-03T05:30:00Z
status: gaps_found
score: 5/8 must-haves verified
gaps:
  - truth: "Comprobante fuera de horario llega a cartera cuando abre la ventana laboral"
    status: failed
    reason: |
      node_forward_to_cartera off-hours path (nodes.py:256-263) sets work_hours_due_at
      on the case but never sets case.status = 'awaiting_cartera'. The case remains
      status='forwarded' (set in node_receive_comprobante:188). check_pending_cases
      (scheduler.py:99-100) queries Case.status == 'awaiting_cartera', so deferred
      cases are never found by the cron. The forward never fires.
      Additionally, the reminder branch in check_pending_cases (scheduler.py:121) calls
      meta.send_buttons() — buttons only, no media re-forward — so even if the status
      bug were fixed the comprobante itself would not be re-sent to cartera.
    artifacts:
      - path: "app/features/payment/nodes.py"
        issue: "Off-hours path (line 256-263) sets work_hours_due_at but omits case.status = 'awaiting_cartera'"
      - path: "app/features/payment/scheduler.py"
        issue: "check_pending_cases queries status='awaiting_cartera' (line 100); deferred cases stay 'forwarded' and are never picked up. Also reminder sends only buttons, not the attachment (line 121)"
    missing:
      - "nodes.py off-hours block: add `case.status = 'awaiting_cartera'` after setting work_hours_due_at (line ~260)"
      - "scheduler.py reminder block: re-upload and re-send the attachment(s) via send_media instead of (or in addition to) send_buttons, so cartera can actually see the comprobante when the window opens"

  - truth: "Agente humano en Chatwoot puede ver el comprobante del cliente"
    status: failed
    reason: |
      _handle_comprobante (webhooks/meta.py:477-519) only enqueues process_attachment.
      It never enqueues mirror_inbound. The mirror_inbound ARQ job is only called
      from _handle_text_message (webhooks/meta.py:460-466). Therefore, images and
      PDF comprobantes sent by clients are never mirrored to the Chatwoot inbox.
      A human agent taking over the conversation has no visibility into what file
      the client submitted.
    artifacts:
      - path: "app/webhooks/meta.py"
        issue: "_handle_comprobante (line 477) does not call mirror_inbound for the attachment; agents see no file in Chatwoot"
    missing:
      - "In _handle_comprobante, after enqueue_job('process_attachment'), also enqueue mirror_inbound with a caption like '[comprobante: {mime_type}]' so the Chatwoot agent sees the media event"
      - "Ideally mirror the media URL (Meta CDN) as a Chatwoot attachment or at minimum a text placeholder with mime type and wamid"

  - truth: "Un fallo de Meta 4xx al reenviar a cartera no deja el caso en estado indefinido"
    status: failed
    reason: |
      node_forward_to_cartera (nodes.py:333-335) calls await meta.send_media(...) inside
      the session context manager with no try/except. If Meta returns a 4xx (e.g. 24-hour
      messaging window expired for cartera number, or rate limit), the exception propagates
      out of the node. The graph crashes, the ARQ job fails (and retries via ARQ retry
      policy), but no fallback to Chatwoot escalation is performed and the case stays in
      status='forwarded' indefinitely with the node never reaching its return statement
      that would set status='awaiting_cartera'.
    artifacts:
      - path: "app/features/payment/nodes.py"
        issue: "send_media call at lines 333-335 has no error handling; Meta 4xx crashes the node inside the session context"
    missing:
      - "Wrap the send_media loop (lines 311-335) in try/except Exception; on failure log the error, escalate via Chatwoot (call get_or_create_conversation + post_message), update case.status = 'escalated', and return {'payment_status': 'escalated'} so the graph reaches a terminal state"
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
**Status:** gaps_found
**Re-verification:** No — initial verification

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
| 6 | Comprobante fuera de horario es reenviado a cartera cuando abre la ventana laboral | FAILED | Off-hours path en `node_forward_to_cartera` no setea `case.status='awaiting_cartera'`; caso queda en `'forwarded'`; cron `check_pending_cases` filtra por `status='awaiting_cartera'`; forward nunca dispara. Ver Gap 1. |
| 7 | Agente humano en Chatwoot puede ver el comprobante del cliente | FAILED | `_handle_comprobante` solo encola `process_attachment`; no encola `mirror_inbound`; imagen/PDF nunca llega al inbox de Chatwoot. Ver Gap 2. |
| 8 | Meta 4xx en forward a cartera no deja el caso indefinido | FAILED | `send_media` en `node_forward_to_cartera:333-335` no tiene try/except; excepcion de Meta crashea el nodo dentro del session context; caso queda en `'forwarded'` sin ruta a escalacion. Ver Gap 3. |

**Score: 5/8 truths verified**

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
| node_forward_to_cartera | meta.send_media | direct await | WIRED (no error handling) | nodes.py:333 — Bug 3 |
| cartera button tap | resume_payment_interrupt | webhooks/meta.py → handle_cartera_message → resume | WIRED | cartera.py:130 |
| resume_payment_interrupt | client dispatch | `_dispatch_client_message` + `check_outbound` | WIRED | cartera.py:151 — fix 0db43b6 |
| Chatwoot human reply | Meta send_text | webhooks/chatwoot.py → `_resolve_and_relay` | WIRED | chatwoot.py:168 |
| off-hours defer | check_pending_cases cron | case.status='awaiting_cartera' predicate | NOT WIRED | scheduler.py:100 queries wrong status — Bug 1 |
| comprobante inbound | mirror_inbound ARQ job | _handle_comprobante → arq.enqueue_job | NOT WIRED | No mirror_inbound call in _handle_comprobante — Bug 2 |
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
| Forward diferido (fuera de horario) | PARTIAL — FAILED | nodes.py off-hours path no setea status='awaiting_cartera'; scheduler nunca encuentra el caso |
| Comprobantes espejados a Chatwoot inbox | FAILED | _handle_comprobante no encola mirror_inbound |
| send_media con manejo de errores Meta 4xx | FAILED | nodes.py:333 sin try/except |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `app/features/payment/nodes.py` | 256-263 | Off-hours path setea `work_hours_due_at` pero no `case.status='awaiting_cartera'` | Blocker | Deferred forward nunca dispara; caso queda stranded en 'forwarded' |
| `app/features/payment/nodes.py` | 333-335 | `await meta.send_media(...)` sin try/except dentro de session context | Blocker | Meta 4xx crashea el nodo; caso nunca llega a awaiting_cartera; ARQ retries sin fallback a escalacion |
| `app/webhooks/meta.py` | 477-519 | `_handle_comprobante` no encola `mirror_inbound` | Warning | Comprobantes invisibles para agentes Chatwoot; degrada handoff humano |
| `app/features/payment/scheduler.py` | 121 | Cron reminder envia solo botones (`send_buttons`), no reforwardea el media | Warning | Incluso tras fix del status bug, cartera no ve el comprobante al abrir ventana laboral |
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

**3 gaps bloquean la completitud del objetivo de Phase 4.**

Los 6 bugs de produccion encontrados durante el smoke vivo fueron arreglados (`dd3285e`, `2351efc`, `e802947`, `0db43b6`) y el core del flujo — intake de comprobante, forward a cartera en horario laboral, aprobacion/rechazo, relay bidireccional Chatwoot ↔ WhatsApp, handoff no_answer — funciona en produccion. Los 299 tests del suite pasan.

Los 3 gaps restantes son todos rutas alternativas o comportamientos de robustez:

1. **Gap 1 (Deferred forward)** — Bug de status incorrecto en `node_forward_to_cartera` off-hours path. El caso queda en `'forwarded'` en vez de `'awaiting_cartera'`, haciendo que el cron no lo encuentre. Fix: una linea adicional `case.status = "awaiting_cartera"` en `nodes.py:260`. Adicionalmente el reminder del cron deberia reforwardear el media, no solo botones.

2. **Gap 2 (Mirror comprobante)** — `_handle_comprobante` no encola `mirror_inbound`. Un agente humano que toma control desde Chatwoot no puede ver el comprobante que envio el cliente. Fix: agregar `arq.enqueue_job("mirror_inbound", ...)` con caption del tipo del archivo en `_handle_comprobante`.

3. **Gap 3 (send_media error handling)** — `send_media` en `node_forward_to_cartera` no tiene try/except. Un error de Meta (ej. 24h window expirado) crashea el nodo sin fallback a escalacion. Fix: envolver el loop de send_media en try/except y escalar a Chatwoot en caso de falla.

Los 3 gaps son independientes entre si y cada uno tiene un fix acotado y claro. Ninguno afecta el flujo principal (horario laboral, cartera responde en tiempo).

---

*Verified: 2026-07-03T05:30:00Z*
*Verifier: Claude (gsd-verifier)*
