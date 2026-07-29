# Plan de operación — pendientes tras el lanzamiento (2026-07-29)

Consolidado de todo lo hablado en la sesión de lanzamiento, para revisar y
ejecutar de punta a punta sin que se pierda nada. Ordenado por prioridad
(gravedad × cuánto degrada la operación real hoy).

Estado base: `main` sincronizado con `origin` en `930c9d4`. Único cambio
local sin commitear: 3 métodos nuevos en `app/integrations/chatwoot.py`
(`list_conversations`, `list_messages`, `add_label`) — quedan como cimiento
para la auditoría (P3) y las etiquetas (P4).

---

## P0 — Bugs que degradan conversaciones reales AHORA

### P0.1 — Listas de pólizas de EMPRESA salen rotas ("1, 1, 1, 1...")
**Evidencia (conv #68, ELEONORA ... 10 pólizas):** las opciones de la lista
interactiva aparecen como `[opciones: 1, 1, 1, 1, 1, 1, 1, 1, CONTRAT...]`.
**Causa:** el fix de hoy "título de fila = riesgo asegurado" asume placa
(AUTOMÓVILES). En empresas el riesgo es un contrato/licitación y el campo que
uso cae a un valor vacío o a "1". El título queda inútil.
**Fix:** para pólizas sin riesgo tipo-placa, el título debe ser el
ramo + número (o el nombre del contrato), no un "1" crudo. Revisar qué campo
real trae SoftSeguros para contratos/licitaciones y construir un título
legible con fallback robusto. Probar contra un NIT con contratos
(ej. INVERSIONES LITRUPE / ELEONORA del log).

### P0.2 — Callejón sin salida cuando el cliente no reconoce su póliza
**Evidencia (conv #68):** cliente escribe "No veo la póliza que mencionan" y
el bot repite la MISMA lista en loop, sin ofrecer salida.
**Fix:** cuando el cliente exprese que no reconoce/encuentra su póliza (o tras
N repeticiones de la lista sin elección válida), ofrecer botón "Hablar humano"
en vez de re-listar. Reusar la escalación de Capa 1 que ya existe.

---

## P1 — Verificación en vivo de fixes ya desplegados (no requieren código)

Confirmar en WhatsApp que lo desplegado hoy funciona:
- **Firewall de solvencia**: pedir "saldo" en póliza sin deuda → NO debe decir
  "está al día"; debe escalar. (conv #71/#70 mostraron el bug ANTES del deploy).
- **Handoff sin re-preguntar documento**: depende del campo `documento` que
  VOICE debe mandar (ver P5). Sin eso, el saludo es contextual pero igual pide
  documento — comportamiento esperado hasta que voz lo mande.

---

## P2 — Notas internas retroactivas (escalaciones de hoy)

**Objetivo:** que las conversaciones que ya escalaron a humano queden con una
nota interna en Chatwoot para que el equipo tenga el contexto.
**Realidad encontrada:** el audit log NO guarda teléfono ni motivo (privacidad
por diseño: `conversation_id`/`metadata` = None en las 3 escalaciones). Así
que el mapeo audit→conversación no sirve. Hay que leer Chatwoot directo.
**Detección correcta (la ingenua matcheaba el botón "Hablar humano" del menú):**
una escalación REAL es el mensaje del bot "te conecto con un agente de DPG"
DESPUÉS de que el cliente escribió "agente"/"humano" — no la presencia del
botón. Confirmadas hasta ahora: **#71 y #70** (ambas terminan en
"Listo, te conecto con un agente..."). #73 NO es escalación (es promesa de
pago bien manejada).
**Fix:** script/one-shot que recorre conversaciones, detecta el patrón real de
escalación, y postea `post_private_note` con un resumen. Mostrar detección
antes de escribir (ya acordado).

---

## P3 — Auditoría diaria de la operación

**Pedido:** saber cómo funciona la operación cada día. Detectar problemas como
los que encontré a mano leyendo conversaciones.

**DECISIÓN PENDIENTE (bloquea el diseño): ¿KPIs nativos de Chatwoot o job custom?**
- Chatwoot TIENE reportes nativos (`/api/v2/.../reports/*`). El endpoint de
  conteo de conversaciones respondió OK (`open/unattended/unassigned/pending`).
  El `overview` dio 404 — hay que encontrar las rutas v2 correctas de esta
  versión (4.16.2).
- Lo NATIVO cubre: volumen, tiempos de respuesta/resolución, carga por agente,
  conversaciones por estado/etiqueta. Si esto basta, NO construimos job custom
  (ponytail: no reinventar lo que la plataforma da). Las etiquetas de P4
  alimentan estos reportes nativos.
- Lo que Chatwoot NATIVO **no** puede: detectar anomalías específicas del bot
  (intentó certificar un pago, lista rota "1,1,1", loop sin salida, cliente
  frustrado). Eso SÍ requiere leer el contenido de los mensajes → job custom.

**Propuesta híbrida (recomendada):**
1. **KPIs de volumen/tiempos** → usar reportes NATIVOS de Chatwoot + las
   etiquetas de P4. Cero código nuevo de métricas; el equipo los ve en la UI.
2. **Auditoría de calidad del bot** → job ARQ nocturno (infra de cron ya existe:
   `check_pending_cases`, `cleanup_attachments_90d`, etc.). Lee las
   conversaciones del día vía `list_conversations`/`list_messages` (métodos ya
   agregados, sin commitear), detecta las anomalías de contenido, y manda un
   resumen corto por WhatsApp a un número de operación + lo guarda. Funciones
   de análisis PURAS (testeables sin red), el job solo orquesta.

**Métricas del job custom (calidad, no volumen):**
- Tasa de respuesta a la plantilla (enviadas vs. respondidas)
- Escalaciones (cuántas, por qué motivo)
- Comprobantes recibidos
- 🔴 Anomalías: intento de certificar pago (LLM lo dijo aunque el firewall lo
  bloqueara), listas rotas, loops sin salida (cliente repite sin avanzar),
  señales de frustración

---

## P4 — Clasificación automática de conversaciones (etiquetas)

**Pedido:** que las conversaciones de plantilla-sin-respuesta no hagan ruido, y
que las que responden se separen. **Decisiones ya tomadas por el operador:**
- Plantilla enviada + cliente NO responde → **snooze + etiqueta `sin-respuesta`**
  (sale de la bandeja activa; Chatwoot la reabre sola si el cliente escribe).
- Etiquetas automáticas a asignar: `sin-respuesta` / `en-conversacion`,
  `escalado-humano`, `comprobante-recibido`, `promesa-pago` / `consulta`.

**Fix:** el bot aplica la etiqueta vía `add_label` (método ya agregado) en cada
transición de estado; el handoff snooze-a la conversación al enviar la
plantilla. Crear las etiquetas en Chatwoot primero (o dejar que la API las
cree). Esto además alimenta los KPIs nativos de P3.

---

## P5 — Coordinación con el repo de VOICE (lambda-proyect)

- **Campo `documento` en el handoff**: el endpoint `/case/handoff/no_answer` ya
  ACEPTA `documento` (opcional). Cuando voz lo mande, el bot resuelve la póliza
  y NO pide documento al cliente (arregla el re-preguntar de LITRUPE). Falta
  que el lado voz lo agregue al payload (ya tienen el documento en Mongo).

---

## P6 — Operaciones / infraestructura (acción humana)

- **Plantilla Meta `alerta_atencion_humana`**: crear/aprobar en Meta para que la
  alerta de escalación a cartera se entregue fuera de la ventana de 24h.
  Mientras no exista, opera el fallback de texto libre (solo llega con ventana
  abierta).
- **Recargar/subir límite OpenRouter**: key nueva `sk-or-v1-dd2...` con límite
  $10 ya activa. Vigilar consumo (~$0.06/día observado).
- **Rotar credenciales expuestas** en chats/terminal esta sesión: `WA_TOKEN`,
  `CHATWOOT_API_KEY`, `OPENROUTER` (la vieja `fb552...`, desactivarla),
  `SOFTSEGUROS_PASSWORD`, `LANGSMITH_API_KEY`, PrivateEmail (`teamtech`),
  tokens del puente F6, contraseñas temporales del equipo DPG.
- **Correo al equipo DPG** con contraseñas + guía de uso (borrador ya entregado).
- **SMTP**: Railway Hobby bloquea SMTP; correos de Chatwoot no salen. Subir a
  Pro o quedarse sin correos de invitación/notificación (workaround: tokens
  manuales por API, ya usado).

---

## Orden de ejecución propuesto

1. **P0.1 + P0.2** — bugs de listas de empresa (degradan conversaciones reales).
2. **P3 decisión** — confirmar nativo vs. custom vs. híbrido para KPIs/auditoría.
3. **P4** — etiquetas (rápido, alimenta P3 nativo, ordena la bandeja).
4. **P3 job custom** — auditoría de calidad del bot (si se elige híbrido).
5. **P2** — notas retroactivas (one-shot, cuando haya un momento).
6. **P5/P6** — coordinación voz + ops (dependen de terceros / acción humana).

Todo lo de código: TDD, ruff+black+mypy limpios, deploy web+worker vía
`railway up`, y `git push` (revisar que main siga en sync con la otra sesión
antes de pushear).
