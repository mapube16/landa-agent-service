# Fase 04 — Resultados del smoke en vivo (2026-07-03 noche, Railway production)

Smoke ejecutado con numeros de prueba Meta (modo test): cliente +573123528153,
cartera +573173717828, bot +1 555 203 1790 (WA_PHONE_ID 1210226812169851).

## Criterios (04-08 Task 3)

| # | Criterio | Resultado |
|---|----------|-----------|
| 1 | Happy path: imagen → caso → forward cartera → Aprobar → confirmacion al cliente | PARCIAL en vivo: intake+caso+ack+suspension+resume+status approved OK; la confirmacion al cliente fallo por el gap de dispatch (arreglado en `0db43b6`, re-test en vivo pendiente). Forward a cartera se hizo manual (fuera de horario, ver bug conocido 1) |
| 2 | Rechazo → escalacion + Chatwoot | PENDIENTE en vivo (cubierto por test E2E) |
| 3 | Numero spoofeado se descarta en silencio | OK (allowlist verificada en vivo: solo cartera rutea; cubierto ademas por test E2E) |
| 4 | Agente Chatwoot → cliente por WhatsApp | OK EN VIVO (`chatwoot.webhook.relayed`, mensaje recibido en el telefono del cliente) |
| 5 | Handoff no-answer (lambda POST → template) | PENDIENTE en vivo (cubierto por test E2E; requiere template Meta APPROVED) |
| 6 | Output firewall bloquea confirmacion alucinada | OK (tests E2E + unit; determinismo no requiere vivo) |

## Bugs REALES encontrados y ARREGLADOS durante el smoke

1. `dd3285e` — el webhook Meta nunca encolaba `process_attachment` (imagen solo
   recibia echo F3) y `InboundMessage` no parseaba el media object. Gap del
   ejecutor de 04-04 pese a estar especificado en el plan (L246-251).
2. `2351efc` — `_dispatch_message` pasaba `app.state.db_session_factory`
   (inexistente; el lifespan lo expone como `session_factory`) → TypeError 500
   en TODO mensaje de cartera.
3. `2351efc` — el proceso ARQ worker nunca inicializaba
   `app.state.session_factory` (el lifespan FastAPI no corre ahi) →
   `process_attachment` no podia abrir sesiones DB. Fix: `on_startup` del worker.
4. env — `agent-worker` tenia `WA_PHONE_ID`/`WA_BUSINESS_ACCOUNT_ID`/`WA_TOKEN`
   viejos (403 Forbidden al enviar). Sincronizados con el servicio web.
5. `e802947` — el verificador HMAC de `/webhooks/chatwoot` firmaba solo el body;
   Chatwoot firma `HMAC-SHA256(channel.secret, "{X-Chatwoot-Timestamp}.{body}")`
   (hex, prefijo `sha256=`). Ademas la clave correcta es el campo `secret` del
   canal API (NO el `hmac_token`). Env `CHATWOOT_WEBHOOK_SECRET` actualizado en
   web y worker. Sin este fix el canal bidireccional era 401 permanente.
6. `0db43b6` — `resume_payment_interrupt` nunca despachaba el AIMessage
   `send_to_client` que emiten `node_confirming`/`node_payment_escalate` → el
   cliente jamas recibia la confirmacion/escalacion tras el tap de cartera.

## Bugs LATENTES conocidos (para gap-closure, NO arreglados)

1. **Forward diferido nunca dispara**: el path fuera-de-horario de
   `node_forward_to_cartera` deja `case.status='awaiting_receipt'` (solo setea
   `work_hours_due_at`), pero el cron `check_pending_cases` consulta
   `status='awaiting_cartera'` → el comprobante diferido jamas se reenvia a
   cartera al abrir la ventana laboral. Ademas el cron envia solo botones
   (reminder), nunca la media.
2. **Comprobantes no se espejan a Chatwoot**: el flujo de pago no encola
   `mirror_inbound`; el inbox humano no ve las imagenes del caso.
3. **`send_media` a cartera sin manejo de error** (`nodes.py:333`): si Meta
   rechaza el envio (p.ej. fuera de ventana 24h), el nodo revienta y el caso no
   llega a `awaiting_cartera` (el job ARQ reintenta, pero sin fallback a
   escalacion).

## Pendientes de infra/ops

- Volumen Railway NO montado en `landa-agent-service`/`agent-worker` →
  comprobantes en disco efimero (se pierden en redeploy). Montar en
  `/data/comprobantes` para produccion.
- `APP_ENV=dev` en produccion (health reporta `env: dev`).
- Rotar `CHATWOOT_API_KEY` (filtrada en terminal en sesion previa).
- Template Meta `voice_no_answer_followup`: estado APPROVED sin verificar.
- Numero WhatsApp en modo test de Meta (max 5 recipients): pasar a live para
  clientes reales.
