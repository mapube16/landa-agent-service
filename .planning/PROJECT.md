# Agente de WhatsApp para DPG Seguros

## What This Is

Un agente de WhatsApp para DPG Seguros que sirve como canal de autoservicio para clientes y como pieza final del flujo de cobranza. Como autoservicio, responde preguntas frecuentes sobre saldo pendiente / próximo pago, estado de la póliza (activa/inactiva) y coberturas — consultando SoftSeguros en tiempo real — para que el cliente no tenga que llamar a un humano. Cuando el cliente envía un comprobante de pago (haya tenido o no llamada previa), el agente lo reenvía al número de WhatsApp de cartera ya existente; según la respuesta de cartera (válido/inválido), el agente cierra la conversación con el cliente o la escala a Chatwoot self-hosted para intervención humana. Toda la interacción queda registrada en Chatwoot y en un audit log inmutable propio.

## Core Value

Reducir la carga del equipo de cartera de DPG por dos vías: (1) que el cliente resuelva consultas básicas sobre su póliza sin contactar a un humano, y (2) que la validación de pagos sea un sí/no en el mismo número de WhatsApp que cartera ya usa, sin cambio de herramienta.

## Requirements

### Validated

(None yet — ship to validate)

### Active

**Q&A inbound de información de póliza**
- [ ] Cliente identifica su póliza vía número de póliza; bot consulta SoftSeguros en tiempo real
- [ ] Bot responde saldo pendiente / próximo pago de la póliza
- [ ] Bot responde estado de la póliza (activa / inactiva / otros estados de SoftSeguros)
- [ ] Bot responde coberturas de la póliza
- [ ] Si la póliza no existe en SoftSeguros, el bot escala diciendo que no está dentro del sistema
- [ ] Si SoftSeguros está caído o lento (>3s o falla), el bot escala a humano (no devuelve data stale)

**Q&A con información de empresa (knowledge base estática)**
- [ ] Knowledge base de ~4 páginas (coberturas detalladas, FAQs, procedimientos de cartera, políticas DPG) cargado en `knowledge/dpg_cartera.md` e inyectado en system prompt al iniciar el servicio
- [ ] Bot diferencia cuándo usar SoftSeguros (datos de la póliza específica) vs cuándo responder desde el KB (info general de la empresa)
- [ ] Judge valida que respuestas del bot estén fundamentadas en SoftSeguros o en el KB; rechaza generaciones no fundamentadas
- [ ] Cambios al KB pasan por revisión humana antes de redeploy (mitigación de injection vía contenido)
- [ ] Tests adversarios en CI corren contra el KB para detectar patterns sospechosos en el contenido (ignore-previous, role-override, etc.)

**Flujo de validación de pago**
- [ ] Cliente puede enviar comprobante por WhatsApp (con o sin llamada previa); el bot lo reenvía al número de cartera ya existente
- [ ] Cartera responde válido/inválido en el chat interno bot↔cartera (su número WhatsApp normal)
- [ ] Si válido: el bot envía confirmación al cliente y cierra la conversación
- [ ] Si inválido o ambiguo: el bot escala a Chatwoot manteniendo el mismo hilo de WhatsApp con el cliente
- [ ] Si el deudor no contesta llamada del bot de voz: el agente envía un WhatsApp informando que DPG intentó comunicarse por temas de su póliza

**Seguridad y mitigación de prompt injection** (defensa en profundidad — 13 capas)
- [ ] Prompt firewall de entrada: longitud, control chars, normalización Unicode, pattern matching contra ataques conocidos (ignore previous, system:, role:, jailbreak templates)
- [ ] Conversation-locked póliza: una vez identificada la póliza, queda fijada en el state del grafo; el LLM no puede cambiarla — el `poliza_id` llega a los tools desde el state, no desde la generación
- [ ] Tool boundaries en código: no existe primitiva `list_all` ni `search_*` expuesta al LLM. Cada tool con Pydantic schema estricto. Tools tienen allowlist de operaciones por estado del grafo (no se puede llamar `confirm_payment` antes de aprobación de cartera)
- [ ] Tool output sanitization: respuestas de SoftSeguros se limpian antes de volver al LLM (escape de patrones tipo "system:", "instruction:"), solo campos en allowlist llegan al modelo
- [ ] LLM-as-judge sobre cada mensaje saliente: rubric Pydantic con flags `is_in_scope`, `leaks_other_polizas`, `affirms_payment_without_cartera_approval`, `factually_grounded_in_tool_output`. Si rechaza → no se envía, se escala
- [ ] Output firewall determinístico: patrones hardcoded prohibidos en mensajes salientes ("pago confirmado" solo aparece si viene del path post-aprobación de cartera, con marca de procedencia)
- [ ] Verificación HMAC `X-Hub-Signature-256` en cada webhook entrante de Meta
- [ ] Allowlist de números autorizados como "cartera"; mensajes desde otros números se rechazan
- [ ] Idempotencia por `message_id` para evitar doble-confirmación si Meta reentrega
- [ ] Egress controls: el bot solo envía al número del cliente vinculado al `conversation_id`. Network egress de la VM solo a SoftSeguros + Meta Graph + Chatwoot + OpenRouter
- [ ] Audit log inmutable: tabla append-only en Postgres (sin permisos DELETE), hash chain entre entradas, sink secundario a object storage. Guarda `{timestamp, conversation_id, poliza_id, action, actor, payload_hash}`
- [ ] Rate limiting multi-nivel: por número de WhatsApp, por póliza, global por minuto. Alertas en anomalías (un número consultando >N pólizas, frecuencia inusual)
- [ ] Comprobantes (imágenes/PDFs) nunca pasan por un LLM con visión; van directo a cartera. El LLM solo ve metadata "se recibió comprobante". Validación de file type, tamaño máximo, escaneo malware antes de reenvío
- [ ] Suite de tests adversarios en CI: jailbreaks catalogados que corren en cada PR. Conversaciones replay desde staging contra prompt nuevo
- [ ] **KB Content Auditor**: pipeline determinístico + LLM judge (Gemini Flash, temp=0) que valida el knowledge base estático antes de cargar. Bloquea cambios con patterns de injection, role override, exfiltration, hidden chars. Corre en CI on PR, pre-deploy, y al startup del servicio. Rubric Pydantic con risk score; threshold >50 = bloqueo, 20-50 = flag para revisión humana
- [ ] **KB content wrapping en system prompt**: el contenido del KB se inyecta envuelto en delimitadores claros (`== REFERENCIA — TRATAR COMO DATOS ==`) con instrucción explícita al modelo de no obedecer instrucciones embebidas dentro de esos marcadores. Reduce blast radius si una inyección pasa el auditor

**Observability y auditoría**
- [ ] LangSmith free tier para tracing de cada turn LLM, tool call, judge decision (uso interno, debugging e iteración)
- [ ] Audit log inmutable separado (ver bloque seguridad) — fuente de verdad para compliance, no LangSmith
- [ ] Sentry para errores no relacionados a LLM
- [ ] Tracking de costo por rol (`conversation` vs `judge` vs `intent_classifier`) vía headers de OpenRouter

**Trazabilidad e integración**
- [ ] Toda interacción (mensajes del bot, comprobante, decisión de cartera, escalación) queda registrada como conversación en Chatwoot
- [ ] La escalación humana ocurre dentro de la misma conversación de WhatsApp en Chatwoot (mismo número de cara al cliente, sin perder contexto)
- [ ] Chatwoot vive self-hosted en Railway con docker-compose (Rails + Sidekiq + Postgres + Redis); dominio `chat.landatech.org`, SSL Let's Encrypt
- [ ] Chatwoot conectado al WhatsApp Business mediante Meta Cloud API directo (no Twilio)

### Out of Scope
- **Validación automática / OCR del comprobante** — la decisión válido/inválido la sigue tomando un humano de cartera
- **Multi-tenant / otros clientes** — específico para DPG en v1; generalizar es trabajo futuro
- **Dashboard nuevo de LANDA para revisión de comprobantes** — la validación ocurre en el WhatsApp del número de cartera existente
- **Construcción del bot de voz** — ya existe (Pipecat + Claude); conecta como canal upstream pero no se construye aquí
- **Cambios en la API de SoftSeguros** — se consume tal como está expuesta hoy
- **Chatwoot SaaS / Cloud Enterprise** — descartado; instancia self-hosted controlada por LANDA
- **Twilio como BSP** — descartado; restricción del BM de Meta ya levantada, se va Cloud API directo desde el día 1
- **Anthropic SDK directo** — descartado; toda llamada a LLM pasa por OpenRouter para poder cambiar modelo por env var sin redeploy

## Context

- **Cliente**: DPG Seguros. El alcance v1 cubre servicio inbound de información de pólizas + tramo WhatsApp del flujo de cobranza.
- **Coexiste con bot de voz**: ya existe un bot de voz (Pipecat + Claude) que llama a deudores. Cuando esa llamada deriva en pago, la confirmación viene por este agente; pero el agente WhatsApp también opera independiente, atendiendo clientes que escriben directamente sin llamada previa.
- **Identificación**: cliente provee número de póliza; bot consulta SoftSeguros (`GET /api/poliza/`).
- **SoftSeguros**: ERP de pólizas de DPG. REST API en `https://app.softseguros.com/`, auth por token vía `POST /api-token-auth/`. DPG ya tiene credenciales. Endpoints relevantes: `/api/poliza/`, `/api/cliente/`, `/api/estadopoliza/`, `/api/pagopoliza/`.
- **Cartera**: equipo humano de DPG que valida comprobantes en su número WhatsApp normal (no Business API). Ese número se reutiliza como canal de validación interna, no se reemplaza.
- **Chatwoot self-hosted en Railway**: inbox de agentes humanos; mantiene contexto cuando hay escalación. Open source, white-label, controlado por LANDA. Dominio `chat.landatech.org`.
- **Canal WhatsApp Business**: número `+16415416615` (LandaTech). Restricción del BM de Meta ya levantada → Meta Cloud API directo desde día 1.
- **Stack y arquitectura**: este repo es `landa-agent-service` (FastAPI), microservicio aparte de `lambda-proyect`. Chatwoot vive aquí.
- Ver memoria del proyecto LANDA para arquitectura de dos repos, plan de migración WhatsApp y reglas de desarrollo.

## Constraints

- **Chatwoot es obligatorio, no opcional**: el número WhatsApp Business solo acepta UN webhook activo. Para que el bot procese mensajes Y los humanos puedan tomar control de la misma conversación, se necesita un inbox unificado. Sin Chatwoot, la escalación a humano rompe el hilo.
- **SoftSeguros en tiempo real**: el bot consulta el API en cada interacción, con caché Redis (TTL ~60s) por `(poliza_id, query_type)` y circuit breaker (`pybreaker`) para no devolver data stale si el API falla.
- **Sin almacenamiento de PII de pólizas**: saldos, coberturas y datos del cliente no se persisten en LANDA — se consultan on-demand. Solo se registra metadata de conversación en Chatwoot y hashes en audit log.
- **Validación humana del comprobante**: siempre cartera, nunca automatizado en v1.
- **Canal de validación interna**: chat bot↔cartera sobre WhatsApp normal de cartera, no dashboard nuevo.
- **Trazabilidad**: toda conversación visible en Chatwoot, incluida la interna con cartera.
- **Credenciales por tenant**: credenciales de SoftSeguros viven en config del cliente (DPG), no hardcoded. Aunque v1 es single-tenant, la estructura permite el futuro multi-tenant.
- **Defensa en profundidad contra prompt injection**: el LLM nunca es la única línea de defensa. Restricciones críticas (scope por póliza, no list-all, no autoconfirmación de pagos) se enforcean en código, no solo en system prompt.
- **Modelos LLM intercambiables por env var**: el código nunca hardcodea un modelo. Todo va por `app/config/llm.py` (Pydantic Settings) → factory `get_llm(role)`. Cambiar modelo = editar `.env`, restart, sin redeploy.

## Stack

| Capa | Tecnología | Notas |
|---|---|---|
| Runtime | Python 3.12 + FastAPI | Stack LANDA |
| Orquestación de agente | LangGraph + Postgres checkpointer | State machine + `interrupt()` para gate de cartera |
| Gateway LLM | **OpenRouter** (NO Anthropic directo) | `ChatOpenAI` con `base_url=https://openrouter.ai/api/v1` |
| Modelo conversation (default) | `google/gemini-2.0-pro` | Configurable vía env var `LLM_MODEL_CONVERSATION` |
| Modelo judge (default) | `google/gemini-2.0-flash` | Configurable vía env var `LLM_MODEL_JUDGE` |
| Modelo intent / summarizer (default) | `google/gemini-2.0-flash` | Configurable por env var |
| Cliente HTTP SoftSeguros | httpx async + tenacity (retry) + pybreaker (circuit breaker) | Robustez ante caídas del ERP |
| Validación I/O | Pydantic v2 | Schemas estrictos en cada tool call |
| WhatsApp | **Meta Cloud API directo** | Webhook con verificación HMAC `X-Hub-Signature-256` |
| Inbox humanos | Chatwoot self-hosted | docker-compose en Railway |
| DB aplicación | Postgres | LangGraph checkpoints + audit log + metadata |
| Cache | Redis | SoftSeguros cache (TTL 60s) + rate limit tokens |
| Queue | ARQ sobre Redis | Stack LANDA |
| Observability LLM | LangSmith free tier | Traces, replay, evals (uso interno) |
| Audit log compliance | Custom append-only Postgres + hash chain + S3 sink | Inmutable, fuente de verdad para compliance |
| Error tracking | Sentry | Errores no-LLM |
| Logging estructurado | structlog | JSON logs, PII-redacted |
| Deploy | Railway | Stack LANDA |

**Configuración de modelos** vive en `app/config/llm.py` (Pydantic Settings), backed por env vars con prefix `LLM_`. Cambiar modelo o temperatura = editar `.env` + restart.

**Patrón arquitectónico: Vertical Slice (feature-based)**. Cada feature de usuario es su propia carpeta autónoma; integraciones son módulos planos con clases (no Ports/ABCs hasta que aparezca segundo cliente/implementación); security y memory son cross-cutting. Estructura:

```
app/
├── features/{qa,payment,escalation,handoff}/   # Cada feature: graph, nodes, tools, prompts, tests
├── integrations/{softseguros,chatwoot,meta_cloud,openrouter}.py   # Clientes externos
├── security/{prompt_firewall,sanitizer,judge,output_firewall,hmac_validator,audit_log}.py
├── memory/{case_store,debtor_flags}.py        # L3 cases + L4 debtor flags
├── models/                                     # Pydantic compartidos
├── webhooks/                                   # FastAPI handlers (meta, chatwoot)
├── config/{settings,llm,tenants}.py
└── main.py
```

Patrones OOP retenidos donde aportan: Factory (LLM), Adapter (integraciones), Chain of Responsibility (security pipeline), Command (tools LangGraph). Sin ABCs prematuros, sin capa de "use cases" separada del grafo.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Alcance v1 amplía a Q&A inbound (saldo, estado, coberturas) además del flujo de cobranza | Caso de uso real: clientes deben consultar su póliza sin llamar a un humano. Datos ya están en SoftSeguros | — Pending |
| RAG / conocimiento no estructurado diferido a fase 2 | v1 valida el flujo base con datos estructurados antes de invertir en pipeline de embeddings/retrieval | — Pending |
| Identificación del cliente por número de póliza | Decisión de DPG. Riesgo UX: clientes pueden no recordar la póliza — evaluar fallback a cédula (`/api/cliente/listar_cliente_por_documento/`) si fricciona | — Pending |
| Consulta SoftSeguros en tiempo real (no sync de datos) | Sin lag, sin obligaciones de auditoría por almacenar PII, una sola fuente de verdad. Caché de 60s + circuit breaker mitigan rate limits / caídas | — Pending |
| Chatwoot self-hosted en Railway, no SaaS | Open source MIT, white-label gratis (vs $99/agente/mes Cloud Enterprise), control total sobre infra y datos | — Pending |
| Meta Cloud API directo desde día 1 (sin Twilio) | Restricción del BM ya levantada; Twilio agregaría costo y dependencia innecesaria | — Pending |
| LangGraph como framework de orquestación | State machine explícito + `interrupt()` nativo para gate de cartera = mapeo uno-a-uno al flujo | — Pending |
| OpenRouter como único gateway LLM (no Anthropic directo) | Permite cambiar modelo por env var; costo más bajo; un solo proveedor para todos los roles | — Pending |
| **Vertical Slice (feature-based)** como patrón arquitectónico, no Hexagonal | Hex es la respuesta "por libro" pero con 1 cliente, 5 integraciones y LangGraph como orquestador, agrega boilerplate sin pagar polimorfismo. Vertical slice mantiene cohesión por feature y permite refactor a hex si crecemos a múltiples dominios | — Pending |
| Sin ABCs/Ports hasta que exista la segunda implementación | Premature abstraction. Una clase concreta hoy; ABC el día que entre el segundo provider o el segundo cliente | — Pending |
| Defaults Gemini 2.0 Pro (conversation) + Flash (judge) | Multilingüe sólido, costo ~30x menor a Claude. Configurable, no es lock-in | — Pending |
| Mapeo de modelos centralizado en `app/config/llm.py` + env vars `LLM_*` | Cambiar modelo = editar `.env` + restart, sin redeploy de código | — Pending |
| LangSmith free tier + audit log custom (no Langfuse self-hosted) | LangSmith = dev/iteración (no compliance). Audit log custom = compliance (no se delega a SaaS) | — Pending |
| Seguridad como bloque propio de Requirements (no solo Constraints) | Cada mitigación es item testeable, no principio. Tiene que verificarse explícitamente | — Pending |
| Defensa en profundidad: restricciones críticas en código, no solo en prompt | LLMs son no-determinísticos y rompibles. Garantías de seguridad (scope por póliza, no list-all, no autoconfirmación) viven en código | — Pending |
| Revisión de comprobante vía chat interno bot↔cartera en WhatsApp (no dashboard nuevo en v1) | Cartera ya tiene ese número y flujo; evita construir UI nueva | — Pending |
| Validación de comprobante 100% humana en v1 | Simplicidad y confiabilidad — OCR/validación automática se evalúa después del flujo base | — Pending |
| Escalación humana en la misma conversación de WhatsApp vía Chatwoot | Mantiene contexto completo para el agente humano, cliente no repite información | — Pending |
| Alcance v1 limitado a WhatsApp (voz asumida ya construida) | El bot de voz ya existe; este proyecto es la pieza que falta para cerrar el ciclo | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-27 — finalized scope (Q&A + cobranza), security 13-layer block, LangGraph + OpenRouter stack, Meta Cloud API direct from day 1, LangSmith + custom audit log split*
