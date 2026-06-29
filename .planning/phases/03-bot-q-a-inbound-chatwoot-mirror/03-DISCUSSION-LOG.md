# Phase 3: Bot Q&A inbound + Chatwoot mirror - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-28
**Phase:** 03-bot-q-a-inbound-chatwoot-mirror
**Areas discussed:** Identificación + state machine, Judge rubric + política rechazo, KB stub + alcance kb_auditor, Error UX (copy exacto)

---

## Identificación + state machine

### Q1: ¿Cómo identifica el bot al cliente?

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-id por wa_phone, fallback a documento | Strip +57 del wa_phone, buscar cliente con cliente_celular == ese número. Si match único → saludar por nombre, mostrar pólizas. Si no match → pedir documento. Mejor UX, mejor seguridad. Costo: 1 GET extra a SoftSeguros. | |
| Solo documento (siempre preguntar) | Bot saluda y pide documento siempre, sin importar el teléfono. Más simple, más consistente, UX más frío. No aprovecha info wa_phone. Vector de impersonación si alguien obtiene un documento ajeno. | ✓ |
| Auto-id silente como cross-check, pero igual pide documento | Bot pide documento siempre, PERO en paralelo busca por wa_phone. Si documento dado no matchea el cliente del wa_phone → flag de impersonación, escala. Más código, doble query. | |

**User's choice:** Solo documento (siempre preguntar) → D-01
**Notes:** Override consciente del PROJECT.md línea 85 y CLAUDE.md (que decían "por número de póliza"). User feedback: "la identificación debería ser por número de documento, el cliente no suele saber su número de póliza". Cascadea a: nuevo método SoftSeguros (`get_clientes_by_documento`), estado intermedio en grafo si N>1 pólizas, doc update post-Phase 3 a PROJECT.md + CLAUDE.md.

---

### Q2: 1 documento → N pólizas, qué hace el bot?

| Option | Description | Selected |
|--------|-------------|----------|
| Lista numerada, cliente elige | Bot muestra lista enumerada, cliente responde con número o numero_poliza. Parseo regex primero, LLM fallback con allowlist. Lock en state. Estado nuevo: awaiting_policy_choice. | ✓ |
| Auto-pick más reciente activa, anuncia cuál | Bot ordena por fecha desc filtrando activo, toma primera, lockea, anuncia. Menos fricción pero "cambiar póliza" es vector de prompt injection. Si cliente pregunta por la vencida sin saber, mal answer. | |
| Lista pero sin lock, LLM ruta cada query | Bot lista N, cliente puede preguntar por cualquiera, LLM decide. Rompe invariante "conversation-locked póliza" del PROJECT.md. NO recomendado. | |

**User's choice:** Lista numerada, cliente elige → D-02
**Notes:** Skip awaiting_policy_choice si N=1 (saluda con la única póliza, va directo a answering_qa).

---

### Q3: Documento no encontrado, qué hace?

| Option | Description | Selected |
|--------|-------------|----------|
| 1 reintento, luego escalar | Bot pregunta confirmación, si segundo fail escala. Balance paciencia vs no bucle infinito. | ✓ |
| Escalar al primer fail, sin reintento | Más seguro contra brute force pero peor UX si typo. | |
| Reintentos infinitos hasta que matchee | NO recomendado: vector de fuerza bruta, UX horrible. | |

**User's choice:** 1 reintento, luego escalar → D-03
**Notes:** Counter `doc_retries` en state, max=1. Anti fuerza bruta + tolera typo.

---

### Q4: Set de nodos del grafo:

| Option | Description | Selected |
|--------|-------------|----------|
| 5 nodos mínimos | awaiting_identification → awaiting_policy_choice (cond) → answering_qa → escalating → closed. Plus conditional edges para errores. Extensible para F4 sin refactor. | ✓ |
| 3 nodos super-mínimos | awaiting_identification → answering_qa → escalating. Mezcla concerns, dificulta enforcear invariantes. | |
| 7+ nodos granulares | Cada error como nodo propio. Mejor observabilidad pero boilerplate alto. Sobreingeniería. | |

**User's choice:** 5 nodos mínimos → D-04

---

## Judge rubric + política rechazo

### Q1: Flags del rubric Pydantic:

| Option | Description | Selected |
|--------|-------------|----------|
| 4 flags mínimos enfocados a F3 | is_in_scope, references_only_locked_poliza, factually_grounded, no_pii_leak. + rationale. F4 agrega payment flag. | |
| Rubric completo desde F3 (8 flags + extras) | is_in_scope, leaks_other_polizas, affirms_payment_without_cartera_approval (False en F3), factually_grounded, no_jailbreak_echo, no_pii_leak, no_external_links, sentiment_appropriate. Cobra costos extra pero evita refactor de schema en F4/F5. | ✓ |
| 1 flag binario + rationale | is_safe_to_send: bool + rationale. Menos código, más opaco para debugging. Riesgo de pasar outputs que rompen reglas granulares. | |

**User's choice:** Rubric completo desde F3 → D-05
**Notes:** Pagar costo de schema completo ahora evita refactor del rubric + tests + LangSmith eval datasets en F4/F5.

---

### Q2: Política cuando judge rechaza:

| Option | Description | Selected |
|--------|-------------|----------|
| 1 retry con guidance, luego escalar | Re-llamar al conversation LLM con rationale del judge inyectado. Si segundo rechazo → escalar. Balance, costo controlado, logueado a LangSmith. | ✓ |
| Escala directo al primer rechazo | Más seguro pero escala demasiado, carga más al equipo DPG. | |
| N retries hasta agotarse | Costo + latencia + riesgo de bucle si judge buggy. Sobreingeniería. | |

**User's choice:** 1 retry con guidance, luego escalar → D-06
**Notes:** Counter `judge_retries` en state, max=1.

---

### Q3: Modelo del judge:

| Option | Description | Selected |
|--------|-------------|----------|
| Gemini 2.5 Flash, temp=0 | Default CLAUDE.md. ~$0.075/1M in + $0.30/1M out, lat ~1s. Suficiente para detectar inyecciones obvias + scope violations. Structured output reliable. | ✓ |
| Gemini 2.5 Flash-Lite, temp=0 (más barato) | 50% más barato, 30% más rápido. Menor capacidad razonamiento sutil. F3 demo aceptable, F5 evalúa subir. | |
| Claude Haiku 4.5 via OpenRouter | 4x más caro que Flash. Solo si F5 muestra que Flash deja pasar jailbreaks reales. | |

**User's choice:** Gemini 2.5 Flash, temp=0 → D-07
**Notes:** Vía env var LLM_MODEL_JUDGE, cambiable sin redeploy.

---

### Q4: Conversation model + hard budget per-turn:

| Option | Description | Selected |
|--------|-------------|----------|
| Gemini 2.5 Pro + sin hard budget en F3 | Default CLAUDE.md. Pro porque conversaciones en español tono comercial, judge va a rechazar menos. Sin budget hard: F3 low traffic, tracking via LangSmith. Hard budget en F5. | ✓ |
| Gemini 2.5 Flash conversation también, más barato | 10x más barato pero respuestas mecánicas, más rechazos judge, más alucinaciones. | |
| Hard budget per-turn ya en F3 | Cap input=4000 + output=400. Suma código + monitoring. F3 no lo necesita. | |

**User's choice:** Gemini 2.5 Pro + sin hard budget en F3 → D-08
**Notes:** Rate limiting + budget hard en F5.

---

## KB stub + alcance kb_auditor

### Q1: Contenido del KB stub en F3:

| Option | Description | Selected |
|--------|-------------|----------|
| Mini-doc realista de 1 página | Secciones reales (Coberturas, FAQs, Procedimientos, Horarios) con contenido placeholder coherente DPG. Permite testear inyección, judge grounding, kb_auditor con contenido no trivial. Swap en F6. ~300-500 tokens. | ✓ |
| Placeholder mínimo (3 líneas) | "Contenido pendiente." Suficiente para no romper carga, pero no testea factual grounding ni patrones realistas para auditor. | |
| Archivo vacío, KB injection deshabilitado en F3 | Skipear carga del KB en F3. F6 introduce todo. Riesgo de regresión: en F6 hay que asegurar que NUNCA se cargue antes de pasar auditor. | |

**User's choice:** Mini-doc realista de 1 página → D-09

---

### Q2: Alcance del kb_auditor pipeline en F3:

| Option | Description | Selected |
|--------|-------------|----------|
| Pipeline completo end-to-end | Las 5 capas en F3 (hash + patterns + diff + LLM judge + risk score). Battle-tested con 5-10 adversarial fixtures. F6 carga real contra pipeline maduro. ~3-4h extra de trabajo. | ✓ |
| Solo hash + static patterns en F3, LLM judge en F5 | Capas 1+2 ahora (determinísticas, baratas), capas 3-5 en F5. Pragmático pero deja gaps: jailbreaks nuevos solo los caza el LLM judge. | |
| Stub vacío del auditor en F3, full en F5 | audit_kb() siempre pasa. F3 demuestra wireup sin lógica. Riesgo: wireup probado contra auditor que nunca rechaza es falso positivo de seguridad. | |

**User's choice:** Pipeline completo end-to-end → D-10
**Notes:** Battle-test ahora con stub controlado es más seguro que cargar el contenido real de DPG por primera vez contra un pipeline incompleto.

---

### Q3: Wireups del auditor:

| Option | Description | Selected |
|--------|-------------|----------|
| 3 wireups, startup como FAIL-CLOSED | CI on PR + pre-deploy + startup. Si risk>50 en startup → servicio NO arranca. Mejor down que servir KB envenenado. | ✓ |
| Solo CI on PR + startup en F3, pre-deploy en F5 | (a) y (c). Pre-deploy es hook Railway extra no trivial. Lo diferimos. | |
| Solo CI on PR en F3, startup + pre-deploy en F5 | Mínimo viable. Gap: KB editado en prod manualmente + restart carga sin auditar. | |

**User's choice:** 3 wireups, startup FAIL-CLOSED → D-11

---

### Q4: Cobertura adversarial en F3:

| Option | Description | Selected |
|--------|-------------|----------|
| 5-10 fixtures cubriendo top categorías | ignore-previous, role override, exfiltration, hidden chars, PII patterns, link injection. Cada fixture con risk esperado, test itera. F5 expande. | ✓ |
| Catálogo extenso 30+ fixtures desde F3 | Cobertura exhaustiva (DAN, AIM, multi-turn, base64, multilingual). ~8h extra. F5 ya tiene "suite adversaria" como deliverable. Riesgo de overlap. | |
| Smoke test mínimo: 1 obvio + 1 benigno | Verificar que rechaza "IGNORE EVERYTHING" y aprueba stub real. F5 carga catálogo grande. Trade-off: categorías no testeadas son oscuras hasta F5. | |

**User's choice:** 5-10 fixtures cubriendo top categorías → D-12

---

## Error UX (copy exacto)

### Q1: Templates fijos vs LLM-generated:

| Option | Description | Selected |
|--------|-------------|----------|
| Templates fijos para TODOS los errores | Constantes en messages.py. Output firewall verifica strings exactos. Cero LLM en error path, cero leak, copy consistente. Tono robotico aceptado a cambio de seguridad/predictibilidad. | ✓ |
| Templates con interpolación simple | f-strings con identificadores que el cliente ya conoce. Personalización mínima. Cuidado con cuáles vars interpolan (NO PII derivada). | |
| LLM-generated con system prompt + judge | Conversation LLM genera errores. Más natural pero suma costo LLM + leak risk + complejidad testing. | |

**User's choice:** Templates fijos para TODOS los errores → D-13

---

### Q2: Idioma + tono:

| Option | Description | Selected |
|--------|-------------|----------|
| Español colombiano informal con 'tú' | "¿Puedes confirmarlo?" Calidez sin formalidad excesiva. Coherente con WhatsApp consumer. Emojis con moderación en positivos, no en errores. | ✓ |
| Español formal con 'usted' | "¿Podría usted confirmarlo?" Corporativo, sector tradicional asegurador. Distante para clientes jóvenes. | |
| Español neutro internacional (LATAM) | Sin colombianismos. Multi-tenant futuro friendly. Pierde calidez local. | |

**User's choice:** Español colombiano informal con 'tú' → D-14
**Notes:** Excepción explícita a CLAUDE.md "no emojis en mensajes generados" por tono cálido WhatsApp cliente final.

---

### Q3: Escape hatch (cliente pide humano):

| Option | Description | Selected |
|--------|-------------|----------|
| Detección por keyword + escalación inmediata | Regex match en lista lockeada (humano, agente, persona, asesor, etc). Determinístico, predecible, zero LLM. | (parte) |
| Detección por LLM (sentiment + intent classification) | Cada inbound pasa por intent classifier. Captura formulaciones indirectas ("no me estás entendiendo"). Suma costo + complejidad. | (parte) |
| Sin escape hatch explícito en F3 | Cliente solo llega a humano vía error. Muy hostil. | |

**User's choice:** Híbrido 1+2 → D-15
**Notes:** User free-text: "me gustaría que fuera un 1 - 2". Diseño híbrido capturado:
- Layer 1: regex match en input crudo, lista lockeada. Hit → escalate directo. Zero LLM cost. Cubre 80% casos.
- Layer 2: si no matchea, conversation LLM tiene tool `escalate_to_human(reason)`. System prompt instruye llamarlo en frustración / pedidos indirectos.
- Ambas terminan en mismo nodo `escalating` con template T-08.

---

### Q4: Catálogo de templates:

| Option | Description | Selected |
|--------|-------------|----------|
| Los 8 cubren todo F3 | T-01 saludo, T-02 doc no found 1er, T-03 doc no found escalando, T-04 lista pólizas, T-05 sin info / fuera scope, T-06 SoftSeguros caído, T-07 judge rechaza tras retry, T-08 escape hatch. Cierra 6 success criteria + escape hatch + greeting. | ✓ |
| Agregar más templates | Prompt firewall block, respuesta exitosa formato, confirmación escalación llegó, cierre, bienvenida con poliza locked. | |
| Reducir a mínimo viable | 3 templates: pedir doc, escalación genérica, lista pólizas. Pierde granularidad de error. | |

**User's choice:** Los 8 cubren todo F3 → D-16
**Notes:** Copies exactos lockeados en CONTEXT.md tabla.

---

## Claude's Discretion

Áreas donde Claude resuelve durante research/planning sin re-consultar:

- Parsing de elección numerada de pólizas (regex first, LLM fallback con allowlist)
- Chatwoot mirror sync vs async (default async ARQ queue para no bloquear)
- Chatwoot inbox: crear "API Channel" dedicado para mirror (separado del WhatsApp inbox de F4)
- LangGraph thread_id = wa_phone (E.164 normalized)
- TTL del lock + resume policy (indefinido en F3, revisitar con tráfico real)
- Prompt firewall regex catalog específico (OWASP LLM01 top + normalización Unicode + length cap)
- Tool output sanitization allowlist por endpoint SoftSeguros (specific fields documentados en CONTEXT.md)
- Tool argument derivation desde state (NUNCA del LLM)
- escalate_to_human(reason) tool implementation (retorna string vacío al LLM, transition via state mutation, reason logged a LangSmith)
- Template adicional para "intento cambiar póliza mid-conversación tras lock"

## Deferred Ideas

- Auto-id por wa_phone matching cliente_celular — revisitar F6/F7 si cliente_celular está confiablemente actualizado en SoftSeguros
- Cross-check silente documento vs celular como capa de seguridad opcional — F4/F5
- Hard budget per-turn (input/output token cap) — F5
- Catálogo extenso 30+ fixtures adversariales — F5 (suite completa)
- Vector RAG con embeddings + pgvector — F7+ (KB >20 pgs)
- Flujo de pago + escalación bidireccional — F4
- Audit log inmutable + hash chain + S3 sink — F5
- Voice handoff (POST /case/handoff) — F6
- Política de re-identificación tras 30+ días de inactividad — F7 si surge dolor en prod
- **Doc update post-Phase 3:** PROJECT.md línea 85 + CLAUDE.md regla identificación + entry en Key Decisions. Commit `docs(03):` aparte cuando F3 cierre.
