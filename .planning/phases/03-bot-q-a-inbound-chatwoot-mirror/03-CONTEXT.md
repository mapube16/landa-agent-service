# Phase 3: Bot Q&A inbound + Chatwoot mirror - Context

**Gathered:** 2026-06-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Bot Q&A real con LangGraph + judge + Chatwoot mirror. Cliente identifica vía **número de documento** (override del PROJECT.md/CLAUDE.md original — clientes no recuerdan número de póliza). Bot lista pólizas asociadas si hay múltiples, cliente elige, bot consulta SoftSeguros vía tools Pydantic, conversation LLM redacta respuesta, judge valida cada salida, Chatwoot mirrora desde el mensaje #1.

**Concretamente, después de Phase 3 funciona:**

1. **Identificación por documento + lock de póliza**: cliente da documento → bot lista pólizas → cliente elige → `poliza_id` queda locked en state hasta `closed`. Si 1 documento → 1 póliza, skip elección.
2. **Q&A sobre saldo / estado / coberturas** con tool calls scoped a la póliza locked. Tools devuelven JSON sanitizado (allowlist de campos).
3. **Judge sobre cada outbound**: rubric Pydantic 8-flag. Rechazo → 1 retry con guidance → si segundo rechazo, escala.
4. **Chatwoot mirror desde msg #1**: cada inbound + outbound aparece en la conversation Chatwoot del cliente con metadata correcta.
5. **Escape hatch híbrido**: cliente puede pedir humano explícita (regex) o implícitamente (LLM detecta frustración, llama tool `escalate_to_human`).
6. **KB stub realista + auditor pipeline completo end-to-end**: `knowledge/dpg_cartera.md` con 1 página de contenido placeholder coherente. `security/kb_auditor.py` corre 5 capas (hash + patterns + diff + LLM judge + risk scoring) en CI on PR + pre-deploy + startup (fail-closed si risk>50). 5-10 fixtures adversariales.
7. **Templates fijos para todos los mensajes de error/escalación** — cero LLM en error path. Español colombiano informal con "tú".

**OUT of scope F3** (heredado del ROADMAP): flujo de pago (F4), escalación bidireccional humano→cliente desde Chatwoot (F4), audit log inmutable con hash chain (F5), voice handoff (F6), rate limiting + budget hard (F5), tests adversariales catalogados extensos (F5).

</domain>

<decisions>
## Implementation Decisions

### Identificación + state machine

- **D-01:** **Solo documento, siempre preguntar.** No auto-id por wa_phone, no cross-check silente. Bot saluda y pide documento en todos los casos. **Esto es un override consciente del PROJECT.md línea 85 y CLAUDE.md (que decían "por número de póliza, no cédula"). Razón: clientes no recuerdan número de póliza.** Doc post-Phase 3: actualizar PROJECT.md + CLAUDE.md para reflejar el cambio.

- **D-02:** **1 documento → N pólizas: lista numerada, cliente elige.** Bot muestra: "Encontré 3 pólizas a tu nombre: 1️⃣ POL-12345 (AUTOMÓVILES, Vigente) 2️⃣ POL-67890 (VIDA, Vigente) 3️⃣ POL-11111 (HOGAR, Vencida). ¿Sobre cuál querés preguntar?". Parseo: regex `^\s*([1-9]\d*)\s*$` o `\b(POL-?\d+|\d{5,8})\b` primero, LLM fallback con allowlist solo de esas N. Estado intermedio `awaiting_policy_choice`. **Skip awaiting_policy_choice si N=1** (saluda con la única póliza, va directo a `answering_qa`).

- **D-03:** **Documento no encontrado: 1 reintento, luego escalar.** Counter `doc_retries` en state (max=1). Mensaje en 1er fail (template T-02), en 2do fail template T-03 + transition a `escalating`. Anti fuerza bruta + tolera typo.

- **D-04:** **5 nodos mínimos del grafo:**
  ```
  awaiting_identification → awaiting_policy_choice (cond, skip si N=1) → answering_qa → escalating (terminal) → closed (terminal)
  ```
  Conditional edges desde **cualquier** nodo → `escalating` si:
  - SoftSeguros caído (pybreaker open, raises `CircuitBreakerError`)
  - judge rechaza tras retry (judge_retries > 1)
  - prompt firewall bloquea entrada
  - doc_retries exhausted (>1)
  - escape hatch híbrido fired (regex match o `escalate_to_human` tool call)
  - cliente intenta cambiar póliza mid-conversación tras lock (template T-X o LLM redirect)

  `closed` marca la conversation Chatwoot como `resolved`. Extensible para F4 sin refactor (F4 agrega `awaiting_receipt`, `forwarded_to_cartera`, `awaiting_cartera_review`, `confirming`).

### Judge + LLMs

- **D-05:** **Rubric Pydantic completo, 8 flags + rationale desde F3:**
  ```python
  class JudgeRubric(BaseModel):
      is_in_scope: bool
      leaks_other_polizas: bool
      affirms_payment_without_cartera_approval: bool  # siempre False en F3, ya queda el schema
      factually_grounded: bool
      no_jailbreak_echo: bool
      no_pii_leak: bool
      no_external_links: bool
      sentiment_appropriate: bool
      rationale: str  # for debugging + LangSmith trace
  ```
  Un flag `False` (excepto `is_in_scope`/`factually_grounded`/etc. donde False es bad) → rejected. Convención: TODOS los flags deben ser True para approve. F4 reutiliza el schema, solo cambia que `affirms_payment_without_cartera_approval` puede legítimamente ser True post-cartera-approval.

- **D-06:** **Rechazo: 1 retry con guidance, luego escalar.** Counter `judge_retries` en state (max=1). Retry inyecta `rationale` del judge al system prompt: "Tu respuesta previa fue rechazada por: {rationale}. Reformulá sin {flag_violado}." Si segundo rechazo → transition a `escalating` con template T-07. Logged a LangSmith para iterar prompt.

- **D-07:** **Judge model: Gemini 2.5 Flash, temp=0** vía OpenRouter (default `LLM_MODEL_JUDGE`). Structured output via `with_structured_output(JudgeRubric)`. Coherente con CLAUDE.md.

- **D-08:** **Conversation model: Gemini 2.5 Pro** (default `LLM_MODEL_CONVERSATION`). **Sin hard budget per-turn en F3** — rate limiting + token cap en F5. F3 confia en bajo volumen (DPG demo) + LangSmith tracking para visibilidad de costos.

### KB + auditor

- **D-09:** **KB stub: mini-doc realista de 1 página** en `knowledge/dpg_cartera.md`. Secciones: `## Coberturas generales`, `## FAQs frecuentes`, `## Procedimientos de cartera`, `## Horarios de atención`. Contenido placeholder pero coherente con DPG (AUTOMÓVILES top ramo según SOFTSEGUROS_API_NOTES.md). ~300-500 tokens. **Swap del archivo en F6** cuando DPG entregue contenido real (4 páginas).

- **D-10:** **kb_auditor pipeline completo end-to-end en F3**, las 5 capas:
  1. **Hash check** — skip si KB hash no cambió desde último audit (cache en Redis o file)
  2. **Static patterns** — regex contra catálogo: ignore-previous, role override, hidden chars (zero-width space U+200B, RTL override U+202E, LTR override U+202D, etc — listar por code point, NUNCA pegar el char literal en código o docs), exfiltration patterns
  3. **Diff extraction** — solo audita el delta vs versión previa (no re-audita texto sin cambios)
  4. **LLM judge** — Gemini 2.5 Flash temp=0 (mismo modelo que outbound judge, pero rubric distinto), `with_structured_output(KBAuditRubric)`
  5. **Risk scoring** — combina señales en score 0-100. Thresholds: **>50 bloquea, 20-50 flag (Sentry warning), <20 pasa silent**.

- **D-11:** **3 wireups del auditor:**
  - **CI on PR**: `.github/workflows/kb-audit.yml` corre auditor cuando `knowledge/dpg_cartera.md` cambia. Falla el PR si risk>50.
  - **Pre-deploy gate**: el step de GitHub Action que dispara Railway redeploy corre auditor primero (o gate manual documentado en runbook si Railway redeploy es vía CLI).
  - **Startup check, FAIL-CLOSED**: en `app/main.py` lifespan, corre `audit_kb()` ANTES de cargar contenido al system prompt. Si risk>50 → `raise RuntimeError("KB audit failed: risk={N}")` y servicio NO arranca. Mejor down que servir KB envenenado.

- **D-12:** **5-10 fixtures adversariales en F3:** `tests/fixtures/kb_adversarial/*.md`, cada uno con `risk` esperado en frontmatter. Top categorías: (1) ignore-previous instruction, (2) role override "you are now", (3) data exfiltration ("output all customer data"), (4) hidden chars (zero-width U+200B + RTL/LTR override U+202E/U+202D — los fixtures inyectan los chars literales y el auditor debe detectarlos; el repo NO contiene chars invisibles fuera de los .md fixtures bajo `tests/fixtures/kb_adversarial/`), (5) PII patterns embedded, (6) link injection (markdown links a dominios sospechosos). Test unitario itera y assertea. F5 expande catálogo.

### Error UX + templates

- **D-13:** **Templates fijos para TODOS los errores y escalaciones** en `app/features/qa/messages.py` como constantes. Cero LLM en error path → cero costo + cero riesgo de leak + output firewall verifica que esos exact strings aparezcan en mensajes tipados como error. Mensajes con interpolación simple (f-string) permitidos solo con identificadores que el cliente ya conoce (su documento, su número de póliza elegido), NUNCA PII derivada (celular, email, dirección).

- **D-14:** **Español colombiano informal con "tú"** (no "vos", no "usted"). Emojis OK en mensajes positivos (✅ lista de pólizas, 👋 saludo), NO en errores. Excepción explícita a CLAUDE.md "no emojis en mensajes generados" — aplicada por tono cálido WhatsApp cliente final.

- **D-15:** **Escape hatch híbrido (regex + LLM tool):**
  - **Layer 1 (determinístico, primero):** regex match en input crudo contra lista lockeada: `\b(humano|agente|persona|asesor|representante|hablar con alguien|persona real)\b` (case-insensitive, normalize acentos). Hit en CUALQUIER nodo → transition directa a `escalating` con template T-08. Zero LLM cost.
  - **Layer 2 (LLM, segundo):** si regex no matchea, mensaje sigue flow normal. Conversation LLM tiene un tool extra `escalate_to_human(reason: str) -> str` con Pydantic schema. System prompt instruye llamarlo cuando detecta frustración o pedidos indirectos ("no me estás entendiendo", "esto no me sirve", "esto es una pérdida de tiempo"). Cuando se invoca → transition a `escalating`.
  - **Ambas layers terminan en el mismo nodo `escalating`** con mismo template T-08.

- **D-16:** **Catálogo de 8 templates lockeados:**

  | ID | Cuándo | Copy exacto |
  |---|---|---|
  | T-01 | Saludo + pedir documento (1er mensaje de la conversación) | `"¡Hola! 👋 Soy el asistente virtual de DPG Seguros. Para ayudarte, ¿me das tu número de documento?"` |
  | T-02 | Doc no encontrado (1er intento) | `"No encontré ese documento en nuestro sistema. ¿Puedes confirmarlo? A veces se cuela un dígito."` |
  | T-03 | Doc no encontrado (2do intento → escalando) | `"Sigo sin encontrar ese documento. Te voy a conectar con un agente de DPG para que te ayude."` |
  | T-04 | Lista de pólizas para elegir (N≥2) | `"Encontré {N} pólizas a tu nombre:\n\n{lista_numerada}\n\n¿Sobre cuál querés preguntar? Respondé con el número o el número de póliza."` (lista_numerada: `"1️⃣ POL-{numero} ({ramo}, {estado})\n2️⃣ ..."`) |
  | T-05 | Póliza no activa / no info / pregunta fuera de alcance | `"Esta póliza no tiene esa información disponible o está fuera del alcance que puedo consultar. ¿Querés que te conecte con un agente?"` |
  | T-06 | SoftSeguros caído (CircuitBreakerError o timeout sostenido) | `"No puedo consultar tu información en este momento. Te voy a conectar con un agente que pueda ayudarte."` |
  | T-07 | Judge rechaza tras retry (no se pudo generar respuesta válida) | `"Disculpá, no pude armar una respuesta clara a tu pregunta. Te conecto con un agente de DPG."` |
  | T-08 | Escape hatch fired (cliente pidió humano explícita o LLM detectó frustración) | `"Listo, te conecto con un agente de DPG. Un humano te va a contestar pronto acá mismo."` |

  Plus template adicional para "intento de cambiar póliza mid-conversación tras lock" capturado como Claude's Discretion (no levantado como crítico en discussion).

### Claude's Discretion

Estas decisiones las resuelvo durante research/planning sin re-consultar — son detalles técnicos sin impacto en vision/UX:

- **Parsing de la elección numerada de pólizas (T-04):** regex first (`^\s*[1-9]\d*\s*$` o `\b\d{5,8}\b`), LLM fallback con allowlist (`grammar` constraint si OpenRouter lo soporta para el provider, sino prompt-only restriction al N possible IDs).
- **Chatwoot mirror sync vs async:** default async via ARQ queue (`enqueue_chatwoot_mirror`) para no bloquear respuesta al cliente. Sync solo si latency Chatwoot < 200ms p95.
- **Chatwoot inbox:** crear un inbox "API Channel" dedicado para mirror (separado del inbox WhatsApp directo de F4) — keeps F4 escalación bidir limpia.
- **LangGraph thread_id:** `thread_id = wa_phone` (E.164 normalized). Checkpointer Postgres ya está wired desde F1.
- **TTL del lock + resume policy:** lock por póliza persiste por `thread_id` indefinidamente (LangGraph checkpoint). Si cliente vuelve a escribir días después, resume desde último state. NO re-identificación automática salvo que regex/LLM detecte intent "nueva consulta" → reset al nodo `awaiting_identification`. **Diferido a Claude's discretion porque el TTL óptimo se descubre con tráfico real, no a priori.**
- **Prompt firewall regex catalog específico:** lista en `app/security/prompt_firewall.py` con patrones del top 10 OWASP LLM01 (prompt injection), normalización Unicode NFKC, control chars strip, length cap 4000 chars (cap WhatsApp es 4096).
- **Tool output sanitization allowlist por endpoint SoftSeguros:**
  - `get_saldo_proximo_pago(poliza_id)` → allowlist `[saldo_pendiente, proximo_pago_monto, proximo_pago_fecha, moneda]`
  - `get_estado_poliza(poliza_id)` → allowlist `[estado_poliza_nombre, fecha_inicio, fecha_fin, ramo_nombre, numero_poliza]`
  - `get_coberturas(poliza_id)` → allowlist `[coberturas[].nombre, coberturas[].monto_asegurado, coberturas[].deducible]`
  - Strings sanitized via regex strip de patterns tipo `(?i)system:|instruction:|<\|.*?\|>`
- **Tool argument derivation:** TODAS las tools reciben `poliza_id` desde state (LangGraph `RunnableConfig` o injected via `ToolNode` factory), NUNCA del LLM generation. El LLM nombra la tool, los args vienen del state. Defensa en profundidad capa 2.
- **`escalate_to_human(reason: str)` tool implementation:** retorna string vacío al LLM (no info sensible), transition a `escalating` via state mutation. Reason logged a LangSmith + structured log para análisis post-hoc.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level (locked)
- `.planning/PROJECT.md` — Q&A scope (saldo/estado/coberturas), 13 capas de seguridad, defensa en profundidad principio, **NOTAR: línea 85 dice "número de póliza" pero D-01 de F3 lo override a "número de documento"**
- `.planning/ROADMAP.md` §"Phase 3" — deliverables originales + success criteria
- `CLAUDE.md` — vertical slice, regla READ-ONLY SoftSeguros, no agregar Twilio, no SDK directo, `get_llm(role)` factory, **NOTAR: regla de identificación por póliza override por D-01 a documento**

### Phase 2 artifacts (carry-forward)
- `.planning/phases/02-integraci-n-softseguros-whatsapp-cloud-api/02-CONTEXT.md` — D-01/D-08/D-11/D-13/D-14/D-15/D-16 aplican (env vars, v21.0, tenacity+pybreaker+cache 60s, 4 endpoints, idempotency, HMAC compare_digest)
- `.planning/phases/02-integraci-n-softseguros-whatsapp-cloud-api/SOFTSEGUROS_API_NOTES.md` — **CRÍTICO para F3:** schema completo de `/api/poliza/{id}/` (184 campos), `/api/cliente/{id}/` (122 campos), enum de 8 estados, embedded fields `cliente_numero_documento` + `cliente_celular`, `cliente_nombres` + `cliente_apellidos`. **Endpoint `/api/cliente/listar_cliente_por_documento/` mencionado en CLAUDE.md pero NO probado en F2 — Phase 3 researcher debe confirmar shape de respuesta.**
- `.planning/phases/02-integraci-n-softseguros-whatsapp-cloud-api/02-04-SUMMARY.md` — round-trip Meta funcional, webhook handler con HMAC + dedup + allowlist ya wireado. F3 reemplaza el `_handle_inbound_text` stub por router a LangGraph.
- `app/integrations/softseguros.py` — base READ-ONLY client. F3 **agrega `get_clientes_by_documento(documento: str) -> list[ClienteRaw]`** (READ-ONLY, no rompe CI guard `tests/test_softseguros_readonly.py`).
- `app/webhooks/meta.py` — handler actual con echo. F3 reemplaza echo branch por `await qa_graph.ainvoke({...}, config={"configurable": {"thread_id": phone}})`.
- `app/features/handoff/echo.py` — folder transitional. F3 puede borrarlo cuando `features/qa/` lo reemplace en webhook routing.
- `app/config/llm.py` — factory `get_llm(role)` ya wireado. F3 usa `get_llm("conversation")` y `get_llm("judge")` con structured output.

### Phase 1 artifacts (infra)
- `.planning/phases/01-setup-infra/01-04-SUMMARY.md` — FastAPI lifespan pattern, AsyncPostgresSaver wired
- `.planning/phases/01-setup-infra/CONTEXT.md` §"PII redaction" — structlog scrubber + sentry scrub ya wireados, F3 NO re-implementa
- `app/config/checkpointer.py` — LangGraph PostgresSaver instance que F3 inyecta al graph compile

### External docs
- LangGraph state machine + conditional edges: https://langchain-ai.github.io/langgraph/concepts/low_level/
- LangGraph tools binding + ToolNode: https://langchain-ai.github.io/langgraph/how-tos/tool-calling/
- LangChain structured output (Pydantic): https://python.langchain.com/docs/how_to/structured_output/
- Chatwoot API v1 — conversations + messages: https://www.chatwoot.com/developers/api/ (research si Chatwoot ya está running en chat.landatech.org desde F1 — confirmar credenciales)

### Security / threat model
- `.planning/PROJECT.md` §"Seguridad y mitigación de prompt injection" — capas 1, 2, 3, 4, 5, 6, 14 (KB auditor), 15 (KB wrapping) aplican a F3. Capas 7-13 son F4/F5.
- OWASP LLM Top 10 v1.1 — LLM01 (prompt injection), LLM02 (insecure output handling), LLM06 (sensitive info disclosure) son los críticos a mitigar en F3.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`app/integrations/softseguros.py`** — singleton client + tenacity + pybreaker + Redis cache 60s ya completos. F3 agrega 1 método nuevo: `get_clientes_by_documento(documento)`. NUNCA crear nuevo client.
- **`app/integrations/openrouter.py`** + **`app/config/llm.py`** — `get_llm(role)` factory ya factory'd, F3 lo consume con `role in {"conversation", "judge"}`. Models leídos de env vars.
- **`app/config/checkpointer.py`** — `AsyncPostgresSaver` ya en `app.state.checkpointer`. F3 lo pasa al `graph.compile(checkpointer=...)`.
- **`app/webhooks/meta.py`** — handler con HMAC + dedup + allowlist + echo. F3 reemplaza echo branch por router al graph (manteniendo allowlist de F2 hasta que F5 lo abra).
- **structlog + correlation_id + Sentry** — wireado en F1. F3 hereda traces correlados sin trabajo extra.
- **`app/models/softseguros.py`** — `PolizaRaw` model ya existe. F3 agrega `ClienteRaw`, `EstadoCodigo` enum (los 8 valores), `SaldoResponse`/`EstadoResponse`/`CoberturasResponse` (sanitized DTOs para tools).

### Established Patterns

- **Vertical slice**: `app/features/qa/{graph,nodes,tools,prompts,knowledge_base,messages}.py`, `app/security/{prompt_firewall,judge,kb_auditor}.py`, `app/integrations/chatwoot.py`. Cada feature es autónomo.
- **READ-ONLY invariant SoftSeguros**: F3 SOLO agrega métodos GET (`get_clientes_by_documento`). CI guard `tests/test_softseguros_readonly.py` falla el build si aparece verbo prohibido.
- **SecretStr** para credenciales: `CHATWOOT_API_KEY: SecretStr` en nuevo `ChatwootSettings(env_prefix="CHATWOOT_")` en `app/config/settings.py`. Mismo patrón que `WhatsAppSettings`/`SoftSegurosSettings`.
- **`@lru_cache(maxsize=1)` singleton factory** para `get_chatwoot_client()` (mismo patrón que `get_softseguros_client()`).
- **Pydantic v2 + structured output**: tools usan `BaseModel` subclass como input schema, judge usa `with_structured_output(JudgeRubric)`.
- **`# noqa: BLE001`** + `type(exc).__name__` en respuestas públicas — F3 lo mantiene en error path para no leakear stack traces vía templates.
- **Lifespan async resource init**: `app/main.py` añade Chatwoot client + LangGraph compiled graph como `app.state.qa_graph`.

### Integration Points

- **Lifespan (`app/main.py`)**: añade construcción de Chatwoot client (singleton, no async teardown) + compile del LangGraph (`qa_graph = build_qa_graph().compile(checkpointer=app.state.checkpointer)`) + kb_auditor startup gate (`await audit_kb(); raise if risk>50`).
- **Webhook router (`app/webhooks/meta.py`)**: branch en `_handle_inbound_text`: si poliza locked en thread → `await app.state.qa_graph.ainvoke({"input": text}, config={"configurable": {"thread_id": phone}})`. Mantener allowlist para F3 (mismo set que F2).
- **ARQ worker (`app/worker.py`)**: nueva task `enqueue_chatwoot_mirror(direction, phone, text, metadata)` para async mirror sin bloquear respuesta.
- **Pre-commit mypy `additional_dependencies`**: si F3 agrega libs (LangGraph types ya incluidos via paquete principal, no debería sumar). Verificar al final del plan.

</code_context>

<specifics>
## Specific Ideas

- **El override de identificación es el momento de decisión más importante de F3.** PROJECT.md y CLAUDE.md decían número de póliza; el usuario corrigió a número de documento basado en UX real (clientes no recuerdan póliza). Esto cascadea a: nuevo método SoftSeguros, estado intermedio `awaiting_policy_choice`, riesgo aumentado de impersonación (mitigado por el principio "el bot solo lee datos, no muta"), templates rediseñados. F6 (post-deploy real con DPG) puede revisitar si necesitamos un cross-check con celular o algún 2FA.
- **El judge se diseñó "completo" desde F3** aunque F3 no use todos los flags activamente. Razón: cambiar el schema del rubric en F4/F5 implica refactor de todos los lugares que lo consumen + tests + LangSmith eval datasets. Pagar el costo ahora.
- **El KB auditor se prioriza "completo end-to-end" en F3 con stub realista** porque battle-testar el pipeline contra contenido controlado (stub + adversarial fixtures) es más seguro que cargar el contenido real de DPG por primera vez contra un pipeline incompleto. F6 swap del contenido real es 1-line change si el pipeline es maduro.
- **Templates fijos en español colombiano informal con "tú"** — el equipo cartera de DPG usa ese registro, el cliente lo va a sentir natural. Emojis con moderación en mensajes positivos refuerzan el tono "asistente útil", se evitan en errores para no banalizar.

</specifics>

<deferred>
## Deferred Ideas

- **Auto-id por wa_phone matching cliente_celular** — descartado para F3 (D-01) pero capturado para revisitar en F6/F7 si el dato del celular en SoftSeguros está confiablemente actualizado. Mejoraría UX (cliente identifica sin escribir nada) y seguridad (impersonación requiere doc + estar en ese teléfono).
- **Cross-check silente documento vs celular** — F4/F5 capa de seguridad opcional: si el documento dado por el cliente no matchea el cliente registrado para el wa_phone, log warning + flag para revisión humana sin bloquear (asume buena fe pero deja auditoría).
- **Hard budget per-turn (input/output token cap)** — F5 con rate limiting + alertas. F3 confía en volumen bajo + LangSmith tracking.
- **Catálogo extenso 30+ fixtures adversariales** — F5 (suite de tests adversarios completa). F3 cubre 5-10 top categorías.
- **Vector RAG con embeddings + pgvector** — diferido por ROADMAP a F7+ (cuando KB crezca >20 pgs). F3 usa inyección directa al system prompt (cabe sobrado en context window de Gemini 2.5 Pro: ~2M tokens).
- **Flujo de pago + escalación bidireccional** — F4.
- **Audit log inmutable + hash chain + S3 sink** — F5.
- **Voice handoff (POST /case/handoff)** — F6.
- **Cliente vuelve después de 30+ días — política de re-identificación** — Claude's Discretion en F3 (resume desde checkpoint LangGraph indefinidamente). Si surge dolor en prod, F7 agrega TTL explícito.
- **Doc update post-Phase 3:** un commit `docs(03):` aparte cuando F3 cierre, cubriendo:
  - `PROJECT.md` línea 85 (`Identificación: cliente provee número de póliza` → `número de documento`)
  - `CLAUDE.md` regla de identificación (póliza → documento)
  - Entry en `## Key Decisions` de PROJECT.md
  - **Excepción a "No emojis en código ni mensajes generados" en CLAUDE.md** — agregar nota: "Excepción explícita en `app/features/qa/messages.py` templates T-01 (👋) y T-04 (1️⃣ 2️⃣ ...) por tono cálido WhatsApp cliente final — lockeado en Phase 3 CONTEXT D-14."

</deferred>

---

*Phase: 03-bot-q-a-inbound-chatwoot-mirror*
*Context gathered: 2026-06-28*
