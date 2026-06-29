# Phase 4: Flujo de validación de pago + Chatwoot escalación bidireccional - Context

**Gathered:** 2026-06-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Cliente envía comprobante de pago por WhatsApp → bot lo reenvía al número de cartera ya existente con botones interactivos → cartera aprueba/rechaza/pide más info → bot cierra (confirmación al cliente) o escala (Chatwoot, agente humano). Cuando un agente humano responde desde Chatwoot, la respuesta sale al cliente por el mismo número de WhatsApp Business. Adicionalmente: endpoint `POST /case/handoff/no_answer` para que el voice agent (lambda-proyect) dispare un template Meta pre-aprobado cuando no logró contactar al deudor por teléfono.

**Lo que queda fuera de esta fase** (per ROADMAP §Out of scope):
- Audit log inmutable con hash chain (logs operacionales OK por ahora).
- Test suite adversarial completa.
- Memoria L3/L4 cargada al inicio de cada caso (handoff se recibe, pero no hidrata estado todavía).

</domain>

<decisions>
## Implementation Decisions

### Storage de comprobantes
- **D-01:** Storage = **Railway volume** persistente, montado en el service `landa-agent-service`. No S3, no Postgres BYTEA. Razón: cliente único, costo mínimo, no hay requisito multi-region todavía. Path interno `/data/comprobantes/{case_id}/{timestamp}-{wamid}.{ext}`. Volume size inicial 1 GB, monitor + alerta al 70 %.
- **D-02:** Retención: **90 días**, luego limpieza por cron del worker (ARQ scheduled job). Razón: cubre disputas razonables sin acumular indefinidamente; ajustable cuando legal/compliance defina política DPG.
- **D-03:** Path internamente referenciado pero **nunca expuesto** al cliente ni al LLM. El forward a cartera usa la API de Meta `media/upload` → `media_id` → `messages` con el id, NO un link público.

### Validación + parseo de respuesta de cartera
- **D-04:** Bot manda a cartera el comprobante + **botones interactivos** (Meta Cloud API → número personal de cartera, que SÍ recibe interactive replies aunque no use Business API). Tres opciones:
  - **`aprobar`** → "Pago confirmado"
  - **`rechazar`** → "Pago no válido"
  - **`pedir_info`** → "Pedir más información" (texto libre del cartera que el bot reenvía al cliente)
- **D-05:** El parsing del response de cartera es **tap-de-botón puro** (`interactive.button_reply.id`). NO texto libre, NO keyword matching, NO LLM judge. Si por algún error llega como texto, el bot le re-manda los botones ("No entendí, tocá una opción").
- **D-06:** Allowlist de números cartera vía env var `CARTERA_PHONE_ALLOWLIST` (lista E.164 separada por coma). Cualquier inbound desde un número no listado se descarta silenciosamente (log only, no respuesta para no leakear que existe el bot interno).

### Forward a cartera + multi-comprobantes
- **D-07:** **Mismo `case_id` para los N adjuntos del cliente.** Cliente envía 3 fotos → bot abre/usa el mismo case_id, hace 3 forwards a cartera (un mensaje por archivo) con la misma caption identificadora.
- **D-08:** Caption del forward:
  ```
  📎 Comprobante [{idx}/{total}] — Caso #{case_id}
  Cliente: {cliente_nombre} (Doc: {doc})
  Póliza: POL-{numero_poliza}
  Recibido: {timestamp_co}
  ```
  Solo en el **último** archivo del batch se agregan los botones (Aprobar/Rechazar/Pedir info), para que cartera vea todo el material antes de decidir.
- **D-09:** Si el cliente manda otro comprobante DESPUÉS de que cartera ya decidió → se abre **nuevo case_id** automáticamente, no se reabre el cerrado.

### Timeout y horarios de cartera
- **D-10:** Horario laboral DPG cartera = **Lunes a Viernes**, **8:00–12:00 + 14:00–16:00 hora Colombia (UTC-5)**. Almuerzo 12-14 cuenta como fuera de horario.
- **D-11:** **Reminder a cartera**: 20 minutos sin respuesta dentro de horario → nudge interno ("⏰ Sigue pendiente caso #X del cliente Y"). **Un solo reminder** por caso.
- **D-12:** **Auto-escalate a Chatwoot**: 90 minutos sin respuesta dentro de horario → bot avisa al cliente "La revisión está tardando, te conecto con un agente" y crea/asigna conversación en Chatwoot (mismo flujo que escape-hatch).
- **D-13:** **Comprobante fuera de horario**: bot acuse al cliente "Recibimos tu comprobante. Cartera revisa en horario laboral (L-V 8-12 + 14-16). Te confirmamos cuando esté validado." El timer arranca al inicio del próximo bloque laboral (no acumula tiempo durante off-hours ni almuerzo).
- **D-14:** Cron/scheduler de timers: ARQ scheduled jobs (`reminder_cartera`, `escalate_stale_case`) consultan Postgres cada minuto. NO usar `asyncio.sleep` ni in-memory state.

### Canal bidireccional Chatwoot → cliente
- **D-15:** **Opción B**: cuando agente humano responde en Chatwoot, Chatwoot dispara un webhook outbound a `POST /webhooks/chatwoot` en este servicio. El handler valida el origen (Chatwoot signature/secret), extrae conversation_id + texto + adjuntos, mapea a `wa_phone`, y envía por Meta Cloud API. Mantiene control centralizado de las credenciales WhatsApp en este service; Chatwoot no necesita configurar el WhatsApp native inbox.
- **D-16:** Mapping `chatwoot.conversation_id ↔ wa_phone` ya existe (cache Redis `chatwoot:conv:{phone_hash}`). El webhook chatwoot busca **al revés** vía Chatwoot API si hace falta (`GET /conversations/{id}/messages` → `sender.phone_number`), pero el camino feliz usa el Redis index inverso `chatwoot:phone_by_conv:{conv_id}` que vamos a poblar al crear cada conversación.
- **D-17:** Idempotencia: cada mensaje de Chatwoot trae `id` único; dedup en Redis con TTL 24h (mismo patrón que `wa:msg:{id}`).
- **D-18:** Adjuntos enviados por agente humano desde Chatwoot → re-subir a Meta CDN como media_id → enviar. Tipos soportados al inicio: imagen + PDF (mismo allowlist que comprobantes).

### Template Meta "no contestamos llamada"
- **D-19:** Template = **`voice_no_answer_followup`**, categoría **UTILITY**, idioma **`es`**.
- **D-20:** Cuerpo:
  ```
  Hola {{1}} 👋, soy el asistente de DPG Seguros.
  Intentamos llamarte sobre tu póliza POL-{{2}} pero no logramos contactarte.
  Si querés, podemos ayudarte por aquí. ¿Te gustaría que te ayude?
  ```
  Variables: `{{1}}` = nombre del cliente, `{{2}}` = numero_poliza.
- **D-21:** Botones quick-reply: **`Sí, ayúdenme`** y **`Más tarde`**. Tap "Sí" → entra al flujo Q&A existente como si el cliente hubiera escrito; tap "Más tarde" → bot responde "OK, escribinos cuando puedas" y termina la conversación (sin escalar).
- **D-22:** Submisión del template a Meta para approval = **prerequisito out-of-band** (Maxi/operador lo crea en Meta Business Suite). El plan documenta el nombre y variables; cuando esté aprobado, se setea env var `META_TEMPLATE_NO_ANSWER_NAME=voice_no_answer_followup` para que el código lo use.
- **D-23:** Endpoint `POST /case/handoff/no_answer` autenticado con `LAMBDA_PROYECT_INTERNAL_TOKEN` (env var compartido entre los dos repos). Payload mínimo: `{phone, cliente_nombre, numero_poliza, case_id}`. El handler crea el case en Postgres, envía el template, queda escuchando webhook con la respuesta del cliente.

### Attachments — tipos, tamaños, validación
- **D-24:** Aceptamos **imagen (jpeg/png/webp) + PDF**. Sin formatos exóticos en v1.
- **D-25:** Tamaño máximo **5 MB** (más bajo que el limite Meta de 16/100 MB). Razones: ahorra storage, suficiente para una foto/scanned PDF razonable, rechazo claro al cliente si lo excede.
- **D-26:** Validación al recibir = **magic-byte check** del primer chunk (libmagic o equivalente) para verificar que `.pdf` realmente sea PDF y la imagen sea imagen. Sin antivirus en v1 (los archivos no se ejecutan, solo se reenvían). Si magic-byte no matchea declared mime-type → rechazar con mensaje al cliente "El archivo no parece válido, intentá con otro formato".
- **D-27:** Comprobantes **nunca pasan por LLM con visión** (re-confirmado de PROJECT.md). El LLM solo ve metadata `{recibido: bool, tipo, size_kb, case_id}`.

### Output firewall — "pago confirmado"
- **D-28:** El texto literal "pago confirmado" (y variantes) solo puede aparecer en el path **post-tap "aprobar" del cartera**. Output firewall (módulo nuevo o extensión del existente) detecta el patrón en cualquier outbound y, si no viene marcado con flag `payment_approved=True` en el AIMessage, **lo bloquea y escala**. Pattern allowlist:
  - `pago confirmado`
  - `pago aprobado`
  - `tu pago fue (registrado|aceptado|recibido)` cuando va seguido de número de póliza
  Implementación = regex case-insensitive + check del flag de procedencia.

### Claude's Discretion
- Estructura interna del nuevo grafo `features/payment/graph.py` (qué subnodes, cómo se anidan con el grafo Q&A existente, si comparten checkpointer o no): research + planner deciden.
- Schema exacto de `case_id` (UUID v4 vs ULID), tabla Postgres y migraciones: research + planner deciden.
- Implementación concreta del scheduler de timers (ARQ vs cron job vs sidekiq-style): research + planner deciden — restricción es D-14 (NO asyncio.sleep, NO in-memory).
- Cómo se firma/valida el webhook de Chatwoot (HMAC, shared secret, API key): research busca lo que Chatwoot soporta nativamente y planner elige.
- Estructura de la tabla de cases / attachments en Postgres: planner.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project foundations
- `.planning/PROJECT.md` — alcance del producto, contratos no negociables (cartera = WhatsApp normal humano, comprobantes nunca por LLM visión, output firewall determinístico, allowlist números cartera, LangGraph interrupt())
- `.planning/ROADMAP.md` §Phase 4 — deliverables + success criteria que esta fase debe alcanzar
- `CLAUDE.md` — convenciones del repo (vertical slice, no ABCs prematuros, Pydantic v2, async por default, READ-ONLY SoftSeguros)

### Integraciones existentes que se reutilizan
- `app/integrations/meta_cloud.py` — `MetaCloudClient.send_text / send_buttons / send_list`. F4 agrega `send_media`, `upload_media`, `send_template`.
- `app/integrations/chatwoot.py` — `ChatwootClient.post_message / get_or_create_conversation / mark_resolved`. F4 agrega trigger/handler del webhook outbound y mapping inverso conv_id → phone.
- `app/webhooks/meta.py` — patrón HMAC + dedup + allowlist + firewall. F4 agrega rama para mensajes desde cartera (allowlist nueva) y para adjuntos.
- `app/features/qa/graph.py` — patrón de StateGraph + conditional entry point. F4 extiende con nodos de pago o crea un sub-graph.
- `app/features/qa/nodes.py` §`route_from_*` — patrón de routing que termina turno (END) por default — F4 lo respeta.
- `app/security/judge.py` — flag `affirms_payment_without_cartera_approval` ya existe en el rubric; F4 activa su uso real.
- `app/worker.py` — `WorkerSettings.functions`. F4 agrega `reminder_cartera`, `escalate_stale_case`, `cleanup_attachments_90d`.

### Decisiones de fases previas que vinculan
- `.planning/phases/02-integraci-n-softseguros-whatsapp-cloud-api/02-CONTEXT.md` — D-13 endpoints SoftSeguros (no se tocan en F4, pero `get_poliza` puede usarse para enriquecer el caption del forward).
- `.planning/phases/03-bot-q-a-inbound-chatwoot-mirror/03-CONTEXT.md` — D-04 grafo Q&A, D-15 orden de procesamiento del webhook, D-16 HMAC. F4 mantiene el orden y agrega su propio webhook Chatwoot.
- `.planning/phases/03-bot-q-a-inbound-chatwoot-mirror/03-06-SMOKE.md` §Live Smoke Findings — bugs operacionales conocidos (agent-worker no auto-deploya con git push; usar `railway up --service agent-worker --ci --detach`). F4 hereda este gotcha.

### Integración con lambda-proyect (voice agent)
- `.planning/PROJECT.md` §"Integración con lambda-proyect" — contrato del handoff (case_id UUID v4, POST /case/handoff). F4 implementa el endpoint del lado landa.
- `.planning/PROJECT.md` §"Out of scope" item "Cambios estructurales en lambda-proyect" — solo definimos contratos REST, lambda implementa su lado.

### Meta WhatsApp Cloud API docs (referencias externas)
- `https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media` — upload + retrieve media (necesario para forward y recepción de adjuntos).
- `https://developers.facebook.com/docs/whatsapp/cloud-api/messages/template-messages` — formato de template message + UTILITY category rules.
- `https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates` — submisión + estados de approval del template.
- `https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-buttons-messages` — interactive button (ya usado en F3, F4 lo aplica al canal con cartera).

### Chatwoot docs (referencias externas)
- `https://developers.chatwoot.com/api-reference/conversations/messages` — formato de mensaje incoming/outgoing.
- `https://www.chatwoot.com/hc/user-guide/articles/2125-how-to-setup-webhooks` — outbound webhook configuration (lo que F4 va a usar para recibir respuestas del agente humano).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `app.integrations.meta_cloud.MetaCloudClient` — extender con `upload_media(file_path)`, `send_media(to, media_id, type, caption)`, `send_template(to, template_name, lang, params)`.
- `app.integrations.chatwoot.ChatwootClient` — extender con helper para mapear `conv_id → phone` (índice inverso Redis `chatwoot:phone_by_conv:{id}` poblado al crear conversación).
- `app.security.judge.JudgeRubric.affirms_payment_without_cartera_approval` — flag ya definido, F4 lo activa.
- `app.features.qa.state.QAState` — extender (NO crear state nuevo) con campos del flujo pago: `case_id`, `attachments`, `payment_status`, `cartera_message_id`, etc. Mantiene checkpointer compartido.
- `app.webhooks.meta.py` — patrón completo (HMAC, dedup, allowlist, firewall) que se reaplica al webhook de cartera (es el mismo endpoint /webhooks/meta, solo cambia la lógica de routing por número origen).
- `app.worker.py` — WorkerSettings list pattern para agregar timer jobs.

### Established Patterns
- **Vertical slice**: nueva carpeta `app/features/payment/` (graph.py, nodes.py, state additions o subdir). No services/, no controllers/.
- **InjectedState para tools**: si hay tools nuevas en F4 (raras, payment es más imperativo), siguen el patrón `Annotated[..., InjectedState(...)]`.
- **Conditional routing terminando turno**: cada nodo no terminal emite mensaje y END (lección F3). Continuar con esto.
- **Lock per phone** (de chatwoot.py) → reutilizable si necesitamos serializar operaciones del caso por phone.
- **Mirror a Chatwoot** via ARQ jobs (`mirror_inbound`/`mirror_outbound`) ya está y debe seguir funcionando para mensajes de pago también — cliente ve el forward "como si nada" en Chatwoot.

### Integration Points
- Webhook Meta `/webhooks/meta` — agrega rama por número origen: si está en `CARTERA_PHONE_ALLOWLIST` → routear al handler de cartera, no al grafo Q&A.
- Webhook nuevo `/webhooks/chatwoot` (Opción B del D-15) — auth + parse + envío via meta_cloud.
- Endpoint nuevo `/case/handoff/no_answer` — auth via shared token con lambda, dispara template.
- Postgres: nueva tabla `cases` (case_id, poliza_id, cliente_doc, phone, status, created_at, updated_at, cartera_message_wamid). Y `attachments` (case_id, path, mime, sha256, recibido_at).

</code_context>

<specifics>
## Specific Ideas

- **Botones de cartera con emoji**: visible y rápido para alguien en su teléfono — `✅ Aprobar`, `❌ Rechazar`, `❓ Más info`.
- **Caption del forward** debe incluir el caso # y la póliza para que cartera pueda buscar en su sistema sin abrir 3 apps.
- **Confirmación al cliente fuera de horario**: tono cálido, no robotic — "Recibimos tu comprobante. Cartera revisa en horario laboral (L-V 8-12 + 14-16). Te confirmamos cuando esté validado 👍"
- **Template Meta**: el copy debe tener tono empático y dejar la salida fácil ("¿Te gustaría que te ayude?") para no presionar.

</specifics>

<deferred>
## Deferred Ideas

- **Audit log inmutable con hash chain** — Phase 5 (ROADMAP).
- **Test suite adversarial completa** — Phase 5 (ROADMAP).
- **Memoria L3/L4 cargada al recibir handoff** — Phase 6 (ROADMAP).
- **OCR/validación automática del comprobante** — PROJECT.md §Out of scope explícito; humano sigue decidiendo siempre.
- **Dashboard LANDA propio para revisión de comprobantes** — descartado en PROJECT.md.
- **Soporte multi-tenant operativo** — Phase futura (DPG es single-tenant en v1, arquitectura ya está pensada).
- **Antivirus dedicado para attachments** — diferido a Phase 5 (security hardening). v1 solo magic-byte check.
- **WhatsApp native inbox en Chatwoot** (Opción A del bidi) — descartada en favor de B; documentada por si en el futuro queremos simplificar.

</deferred>

---

*Phase: 04-flujo-de-validaci-n-de-pago-chatwoot-escalaci-n-bidirecciona*
*Context gathered: 2026-06-29*
