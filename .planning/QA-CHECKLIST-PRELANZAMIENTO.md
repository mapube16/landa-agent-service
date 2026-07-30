# Checklist QA pre-lanzamiento — ARIA WhatsApp (DPG)

Levantado del código real por dos exploraciones (flujo Q&A + flujo pago/escalación),
2026-07-29. ~90 casos. Marcados los que puede probar el operador desde WhatsApp
vs. los que se validan por logs/DB.

Convención: 🔴 = crítico (probar sí o sí antes de prod) · 🟡 = importante · ⚪ = borde.

---

## BLOQUE 1 — Identificación (probar desde WhatsApp)

- 🔴 Cédula válida de persona → lista/menú de pólizas
- 🔴 NIT **sin** dígito de verificación (ej. `900144220`) → el bot lo encuentra igual (calcula el DV). El cliente NO debe tener que escribir el `-7`.
- 🟡 NIT con dígito (`900144220-7`) y con puntos (`900.144.220-7`) → encuentra
- 🔴 Documento inexistente → mensaje "no encontré…" **con botón "Hablar humano"** (no debe quedar en loop)
- 🟡 Documento basura (letras, emojis) → no crashea, mensaje not-found
- 🔴 Empresa (NIT) con varios contratos → lista **sin "1,1,1"** (títulos legibles: ramo/número)

## BLOQUE 2 — Selección de póliza (WhatsApp)

- 🔴 Cliente con 1 póliza → entra directo al menú (Saldo/Estado/Información)
- 🔴 Cliente con varias → lista; título = placa/riesgo si existe, si no ramo+número
- 🟡 ≥10 pólizas → "Ver más pólizas" pagina bien
- 🔴 "No veo mi póliza" → escala a humano (NO re-lista infinito)
- 🟡 Elegir por número, por placa tecleada, por texto natural
- 🟡 2 respuestas basura seguidas → a la 2ª escala a humano

## BLOQUE 3 — Preguntas (WhatsApp)

- 🔴 Saldo / Estado / Información / Coberturas → responde con datos reales de la póliza
- 🔴 **Idioma colombiano** (tú/tienes/quieres) — NUNCA argentino (vos/tenés/querés). *(voseo residual corregido hoy)*
- 🟡 Pregunta fuera de scope → rechaza y ofrece humano, no inventa
- 🟡 "Información" muestra el **riesgo asegurado destacado** (placa, etc.)

## BLOQUE 4 — 🔴 REGLA DE PAGOS (máxima prioridad, WhatsApp)

Probar cada frase — el bot NUNCA debe certificar solvencia:
- 🔴 "Ya pagué" → agradece + "cartera revisa", NO "está al día"
- 🔴 Saldo en póliza sin deuda → NO dice "no tienes saldo / al día", deriva a cartera
- 🔴 "¿Estoy al día?" → no confirma, escala
- 🔴 Verificar que el firewall bloquea si el LLM igual lo intenta (por logs: `output_firewall.payment_blocked`)
- ⚪ Saldo pendiente CONCRETO (monto+fecha) SÍ se puede informar

## BLOQUE 5 — Escalación / humano (WhatsApp + Chatwoot)

- 🔴 "Quiero hablar con un humano" desde cualquier etapa → escala, y llega **alerta a cartera** + **nota interna en Chatwoot**
- 🔴 Tras escalar, el bot queda **callado** si el cliente sigue escribiendo (verlo en Chatwoot)
- 🔴 Agente responde en Chatwoot → llega al cliente por WhatsApp
- 🟡 Agente marca "Resolver" → el bot vuelve a atender a ese cliente
- 🟡 Escalación sin atender 30 min → bot se re-activa solo (auto-recuperación)

## BLOQUE 6 — Comprobante de pago (WhatsApp + Chatwoot + cartera 314)

- 🔴 Foto JPG en horario hábil → llega al **314 con botones** Aprobar/Rechazar + imagen visible en Chatwoot
- 🔴 Tocar **Aprobar** (desde 314) → confirmación al cliente
- 🔴 Tocar **Rechazar** → escala a Chatwoot
- 🔴 Foto **fuera de horario** → ack "cartera revisa en horario laboral" + se reenvía a cartera a las 8:20am (cron)
- 🟡 PDF válido → igual que foto
- 🟡 Archivo prohibido (audio/video) → "solo imágenes o PDF"
- ⚪ Archivo >5MB → "supera 5 MB"
- ⚪ 2 comprobantes al mismo caso → ambos llegan, botones solo en el último

## BLOQUE 7 — Plantilla no-contestó (disparar POST o vía voz)

- 🔴 Recibir plantilla + tocar **"Sí, ayúdenme"** → arranca Q&A (con contexto de la póliza si voz mandó `documento`)
- 🔴 Tocar **"Más tarde"** → cierre cortés fijo
- 🟡 Handoff con `documento` → NO pide documento al cliente (entra directo a la póliza)
- 🟡 Retransmisión mismo case_id → idempotente (no doble plantilla)

## BLOQUE 8 — Robustez (logs/DB, no visible al cliente)

- 🟡 2 mensajes rápidos ("saldo" luego "estado") → ambos responden en orden (lock)
- 🟡 Mensaje duplicado (Meta reenvía) → no se procesa doble
- 🟡 Número de cartera escribe → no entra al Q&A de cliente
- ⚪ HMAC inválido → 401
- 🟡 Si el grafo crashea → cliente recibe "tuvimos un inconveniente, te conectamos con una persona" *(fallback agregado hoy)*

---

## Riesgos conocidos a vigilar (del análisis, no bugs abiertos)

1. **C13** — si Meta falla al enviar la confirmación tras Aprobar, el caso queda `approved` en DB pero el cliente no fue notificado. Monitorear en prod (no hay retry).
2. **B11** — multi-cartera NO soportado: solo el primer número de la allowlist recibe. Operación debe saberlo.
3. **A4** — WEBP valida solo prefijo `RIFF` (colisión teórica). Bajo riesgo.
4. **F6/G8** — comprobantes NO pasan por el lock de concurrencia ni por el mute; entran siempre al intake.

## Cómo validar los que no se ven en WhatsApp
- Alertas/mute/firewall/lock → `railway logs -s landa-agent-service` (eventos: `output_firewall.payment_blocked`, `muted_human_takeover`, `escalation_alert.sent`, `conv_lock.*`)
- Comprobante/forward/timing → `railway logs -s agent-worker` (`payment.forward.ok`, `scheduler.*`)
- Métricas del día → `GET /metrics/daily` o el dashboard en Chatwoot
