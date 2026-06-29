# Phase 3: Bot Q&A inbound + Chatwoot mirror - Research

**Researched:** 2026-06-29
**Domain:** LangGraph state machine + Chatwoot mirror + prompt security pipeline
**Confidence:** HIGH (all core patterns verified against installed packages and official sources)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Identificación + state machine**
- D-01: Solo documento, siempre preguntar. No auto-id por wa_phone, no cross-check silente. Bot saluda y pide documento en todos los casos. Override consciente del PROJECT.md / CLAUDE.md (que decían "por número de póliza"). Razón: clientes no recuerdan número de póliza.
- D-02: 1 documento → N pólizas: lista numerada, cliente elige. Skip awaiting_policy_choice si N=1.
- D-03: Documento no encontrado: 1 reintento, luego escalar. Counter `doc_retries` (max=1).
- D-04: 5 nodos del grafo: awaiting_identification → awaiting_policy_choice (cond, skip si N=1) → answering_qa → escalating (terminal) → closed (terminal). Conditional edges desde cualquier nodo hacia `escalating`.

**Judge + LLMs**
- D-05: Rubric Pydantic 8 flags + rationale (ver schema completo en CONTEXT.md).
- D-06: Rechazo: 1 retry con guidance inyectando `rationale`, luego escalar.
- D-07: Judge model: Gemini 2.5 Flash, temp=0, vía `get_llm("judge")`.
- D-08: Conversation model: Gemini 2.5 Pro, vía `get_llm("conversation")`. Sin hard budget F3.

**KB + auditor**
- D-09: KB stub ~300-500 tokens en `knowledge/dpg_cartera.md`. 4 secciones (Coberturas, FAQs, Procedimientos, Horarios). Swap real en F6.
- D-10: kb_auditor 5 capas completas en F3: hash check → static patterns → diff extraction → LLM judge → risk scoring (thresholds: >50 bloquea, 20-50 flag Sentry, <20 pasa).
- D-11: 3 wireups del auditor: CI on PR, pre-deploy gate, startup FAIL-CLOSED.
- D-12: 5-10 fixtures adversariales en `tests/fixtures/kb_adversarial/*.md` con frontmatter `risk`.

**Error UX + templates**
- D-13: Templates fijos en `app/features/qa/messages.py`. Cero LLM en error path.
- D-14: Español colombiano informal con "tú". Emojis OK en mensajes positivos (excepción a CLAUDE.md).
- D-15: Escape hatch híbrido: Layer 1 regex (determinístico), Layer 2 LLM tool `escalate_to_human(reason: str)`.
- D-16: 8 templates lockeados T-01..T-08 (ver CONTEXT.md para copy exacto).

### Claude's Discretion
- Parsing de elección numerada: regex first, LLM fallback con allowlist.
- Chatwoot mirror: async via ARQ queue.
- Chatwoot inbox: crear API Channel dedicado separado del inbox WhatsApp de F4.
- LangGraph thread_id: `wa_phone` E.164 normalized.
- TTL del lock: indefinido (LangGraph checkpoint); reset si regex/LLM detecta "nueva consulta".
- Prompt firewall catalog: OWASP LLM01 top 10, NFKC + strip codepoints invisibles, length cap 4000.
- Tool output allowlists por endpoint (ver CONTEXT.md §"Tool output sanitization allowlist").
- Tool argument derivation: TODAS via `InjectedState('poliza_id')`, NUNCA del LLM.
- `escalate_to_human` tool: retorna string vacío al LLM, muta state a `escalating`.

### Deferred Ideas (OUT OF SCOPE)
- Auto-id por wa_phone matching cliente_celular (F6/F7).
- Cross-check silente documento vs celular (F4/F5).
- Hard budget per-turn (F5).
- Catálogo extenso 30+ fixtures adversariales (F5).
- Vector RAG (F7+).
- Flujo de pago + escalación bidireccional (F4).
- Audit log inmutable + hash chain + S3 sink (F5).
- Voice handoff (F6).
- Re-identificación con TTL explícito (F7 si hay dolor en prod).
</user_constraints>

---

## Summary

Phase 3 construye el núcleo funcional del bot: LangGraph StateGraph de 5 nodos con identificación por documento, Q&A tools con `InjectedState` para poliza_id, LLM-as-judge sobre cada salida, Chatwoot mirror asíncrono via ARQ, y el pipeline de auditoría del KB. Todas las dependencias ya están instaladas — F3 NO agrega ningún paquete externo nuevo. La única incógnita real es la shape de respuesta de `/api/cliente/listar_cliente_por_documento/` que no fue probado en F2 y debe verificarse en Wave 0 con una probe real al sandbox de DPG antes de modelar el Pydantic schema.

El patrón central de defensa — `InjectedState('poliza_id')` en las tools — está verificado: el LLM ve `tool_call_schema` vacío para tools que solo toman args inyectados, y ve solo `question_type` para tools con argumentos mixtos. ToolNode inyecta el estado en runtime, sin que el LLM pueda suministrar o alterar `poliza_id`.

La integración Chatwoot usa la Application API con header `api_access_token`: `POST /api/v1/accounts/{account_id}/contacts` → `POST /api/v1/accounts/{account_id}/conversations` → `POST /api/v1/accounts/{account_id}/conversations/{id}/messages` con `message_type: incoming|outgoing`. El inbox debe ser tipo "API Channel" (creado manualmente en Chatwoot UI antes de F3 Wave 1).

**Primary recommendation:** Estructurar F3 en 4 waves: (W0) probe `/api/cliente/listar_cliente_por_documento/` + crear inbox API Channel en Chatwoot + modelos Pydantic; (W1) grafo LangGraph + tools + judge + prompt firewall; (W2) Chatwoot client + mirror ARQ + KB auditor; (W3) integración en webhook handler + smoke test end-to-end.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Identificación por documento | API/Backend (LangGraph node) | — | Estado del grafo, no UI |
| Lock de poliza_id | API/Backend (LangGraph state) | — | Defensa en código, no en prompt |
| Q&A tool calls a SoftSeguros | API/Backend (LangGraph tools) | DB/Cache (Redis TTL 60s) | Reads en tiempo real con cache |
| LLM-as-judge | API/Backend (security/judge.py) | — | Cross-cutting sobre cada outbound |
| Prompt firewall | API/Backend (security/prompt_firewall.py) | — | Pre-LLM, en webhook dispatch |
| Chatwoot mirror | Queue (ARQ job) | API/Backend (enqueue en webhook) | Async para no bloquear Meta 200 |
| KB content | API/Backend (system prompt) | — | <500 tokens, cabe en context window |
| KB auditor | API/Backend (startup gate + CI) | — | FAIL-CLOSED en lifespan |
| Webhook dispatch a graph | API/Backend (webhooks/meta.py) | — | asyncio.create_task, no bloquea |

---

## Standard Stack

### Core (todo ya instalado en pyproject.toml)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| langgraph | 1.2.6 | StateGraph 5-nodos, ToolNode, InjectedState | Orquestador de agente (ROADMAP locked) |
| langchain | 1.3.11 | with_structured_output(JudgeRubric), tool binding | LangGraph dependency, también entrypoint para structured output |
| langchain-openai | 1.3.3 | ChatOpenAI via get_llm(role) factory | OpenRouter gateway (CLAUDE.md locked) |
| langgraph-checkpoint-postgres | 3.1.0 | AsyncPostgresSaver (ya wired en app.state.checkpointer) | Persistencia de state del grafo |
| pydantic | 2.13.4 | Schemas JudgeRubric, KBAuditRubric, tool I/O, ChatwootSettings | Validación estricta (CLAUDE.md rule) |
| httpx | 0.28.1 | ChatwootClient async HTTP | Mismo patrón que MetaCloudClient |
| redis | 8.0.1 | Cache SoftSeguros + ARQ queue + dedup | Ya wired |
| arq | 0.28.0 | Async Chatwoot mirror jobs con retry | Ya en WorkerSettings |
| pyyaml | 6.0.3 (transitive) | Parseo de frontmatter YAML en test fixtures adversariales | Transitivo via langchain, no requiere add |

### No se añade ningún paquete nuevo en F3

F3 usa exclusivamente dependencias ya en pyproject.toml. unicodedata, re, hashlib, tomllib son stdlib Python 3.12. pyyaml ya es dependencia transitiva.

**Verificación de versiones instaladas (confirmado en venv):**

```
langgraph:                  1.2.6
langchain:                  1.3.11
langchain-openai:           1.3.3
langsmith:                  0.9.3
langgraph-checkpoint-postgres: 3.1.0
arq:                        0.28.0
redis:                      8.0.1
pydantic:                   2.13.4
```

[VERIFIED: importlib.metadata en venv del proyecto]

---

## Package Legitimacy Audit

> F3 no instala paquetes externos nuevos. Todos los paquetes son los ya presentes en pyproject.toml, instalados y funcionando (66 tests pasando). No se ejecuta slopcheck porque no hay paquetes candidatos.

**Packages removed due to slopcheck [SLOP] verdict:** ninguno — no hay paquetes nuevos.
**Packages flagged as suspicious [SUS]:** ninguno.

---

## Architecture Patterns

### System Architecture Diagram

```
WhatsApp (Meta Cloud)
    |
    v
POST /webhooks/meta
    |── HMAC verify ──► 401 if invalid
    |── Pydantic parse
    |── dedup (Redis wa:msg:{id})
    |── allowlist check
    |
    v
_dispatch_message()
    |── prompt_firewall.sanitize(text) ──► if BLOCKED: send T-06 + return
    |── regex escape_hatch check ──► if HIT: create_task(graph.ainvoke({"escalate": True}))
    |
    |── asyncio.create_task(qa_graph.ainvoke(input, config={thread_id: phone}))
    |── arq.enqueue_job("mirror_inbound", phone, text)
    └── return 200 to Meta (immediately)

    LangGraph QAGraph (async, background task)
    ┌─────────────────────────────────────────────────────────────┐
    │  State: {messages, poliza_id, cliente_doc, doc_retries,     │
    │          judge_retries, node, polizas_list}                  │
    │                                                             │
    │  awaiting_identification                                    │
    │    └─► get_clientes_by_documento(doc) ──► ClienteRaw list   │
    │         if found + N=1: lock poliza, → answering_qa          │
    │         if found + N>1: → awaiting_policy_choice            │
    │         if not found + retries<1: retry (T-02)             │
    │         if not found + retries>=1: → escalating (T-03)     │
    │                                                             │
    │  awaiting_policy_choice                                     │
    │    └─► regex parse choice → lock poliza_id → answering_qa  │
    │                                                             │
    │  answering_qa                                               │
    │    └─► LLM invoke (get_llm("conversation")) + ToolNode      │
    │         tools: get_saldo, get_estado, get_coberturas        │
    │                (poliza_id from InjectedState, NEVER LLM)   │
    │         tool outputs sanitized via allowlist                │
    │    └─► judge(response) → JudgeRubric                       │
    │         if all True: send via meta.send_text()              │
    │         if rejected + judge_retries<1: retry with rationale │
    │         if rejected + judge_retries>=1: → escalating (T-07) │
    │                                                             │
    │  escalating (terminal) ──► send template ──► END            │
    │  closed   (terminal) ──► mark Chatwoot resolved ──► END     │
    └─────────────────────────────────────────────────────────────┘

    ARQ Worker (async, separate process)
    ┌─────────────────────────────────────────────────────────────┐
    │  mirror_inbound(ctx, phone, text, wamid)                    │
    │    └─► chatwoot.post_message(conv_id, text, type="incoming")│
    │                                                             │
    │  mirror_outbound(ctx, phone, text, wamid)                   │
    │    └─► chatwoot.post_message(conv_id, text, type="outgoing")│
    └─────────────────────────────────────────────────────────────┘

    SoftSeguros API (DPG tenant)
        /api/cliente/listar_cliente_por_documento/?documento=X [ASSUMED shape]
        /api/poliza/{id}/ [VERIFIED schema, 184 fields, allowlist before LLM]
        /api/estadopoliza/ [VERIFIED enum, 8 estados]

    Chatwoot self-hosted (chat.landatech.org)
        POST /api/v1/accounts/{id}/contacts
        POST /api/v1/accounts/{id}/conversations
        POST /api/v1/accounts/{id}/conversations/{id}/messages
```

### Recommended Project Structure (F3 additions)

```
app/
├── features/qa/
│   ├── graph.py          # build_qa_graph() → StateGraph
│   ├── nodes.py          # node functions: identify, choose_policy, answer, escalate
│   ├── tools.py          # @tool get_saldo, get_estado, get_coberturas (InjectedState)
│   ├── prompts.py        # system_prompt(poliza_id, kb_content, l4_flags) → str
│   ├── knowledge_base.py # load_kb() → str (from knowledge/dpg_cartera.md)
│   └── messages.py       # T-01..T-08 template constants + interpolate()
├── security/
│   ├── prompt_firewall.py # sanitize(text) → SanitizeResult (BLOCKED|CLEAN|FLAGGED)
│   ├── judge.py           # judge_response(messages, response) → JudgeRubric
│   └── kb_auditor.py      # audit_kb(content) → KBAuditResult (risk score 0-100)
├── integrations/
│   └── chatwoot.py        # ChatwootClient + get_chatwoot_client()
├── models/
│   └── softseguros.py     # expand: ClienteRaw, EstadoCodigo, SaldoResponse, etc.
├── worker.py              # add: mirror_inbound, mirror_outbound to WorkerSettings.functions
├── config/
│   └── settings.py        # add: ChatwootSettings(env_prefix="CHATWOOT_")
├── webhooks/
│   └── meta.py            # replace echo branch with qa_graph dispatch
└── main.py                # add: ChatwootClient + qa_graph + kb_auditor startup gate
knowledge/
└── dpg_cartera.md         # KB stub ~300-500 tokens
tests/
├── features/qa/
│   ├── test_graph.py
│   ├── test_tools.py
│   └── test_messages.py
├── security/
│   ├── test_prompt_firewall.py
│   ├── test_judge.py
│   └── test_kb_auditor.py
├── integrations/
│   └── test_chatwoot.py
└── fixtures/kb_adversarial/
    ├── 01_ignore_previous.md
    ├── 02_role_override.md
    ├── 03_data_exfiltration.md
    ├── 04_hidden_chars.md
    ├── 05_pii_patterns.md
    ├── 06_link_injection.md
    ├── 07_clean_control.md   # control: expected risk <20
    └── 08_mixed_risk.md
```

### Pattern 1: LangGraph StateGraph con 5 nodos

**What:** StateGraph compila el grafo de nodos con conditional edges. Estado compartido via TypedDict con reducers Annotated.
**When to use:** Cualquier flujo multi-step con bifurcaciones y persistencia de conversación.

```python
# Source: verified via import check in venv (langgraph 1.2.6)
from typing import TypedDict, Annotated, Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class QAState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]  # accumulates via reducer
    poliza_id: str | None          # locked once set — NEVER modified by LLM tools
    cliente_doc: str | None
    polizas_list: list[dict]       # list of polizas for the selection step
    doc_retries: int               # max 1 per D-03
    judge_retries: int             # max 1 per D-06
    node: Literal[
        "awaiting_identification",
        "awaiting_policy_choice",
        "answering_qa",
        "escalating",
        "closed",
    ]

def build_qa_graph() -> StateGraph:
    builder = StateGraph(QAState)
    builder.add_node("awaiting_identification", node_identify)
    builder.add_node("awaiting_policy_choice", node_choose_policy)
    builder.add_node("answering_qa", node_answer)
    builder.add_node("escalating", node_escalate)
    builder.add_node("closed", node_close)
    builder.set_entry_point("awaiting_identification")
    builder.add_conditional_edges(
        "awaiting_identification",
        route_from_identification,
        {
            "awaiting_policy_choice": "awaiting_policy_choice",
            "answering_qa": "answering_qa",
            "escalating": "escalating",
            # stays in same node on retry: return "awaiting_identification"
            "awaiting_identification": "awaiting_identification",
        },
    )
    builder.add_conditional_edges(
        "awaiting_policy_choice",
        route_from_policy_choice,
        {"answering_qa": "answering_qa", "awaiting_policy_choice": "awaiting_policy_choice"},
    )
    builder.add_conditional_edges(
        "answering_qa",
        route_from_answering,
        {"answering_qa": "answering_qa", "escalating": "escalating", "closed": "closed"},
    )
    builder.add_edge("escalating", END)
    builder.add_edge("closed", END)
    return builder

# Compile in lifespan:
# qa_graph = build_qa_graph().compile(checkpointer=app.state.checkpointer)
# app.state.qa_graph = qa_graph
```

[VERIFIED: StateGraph, END, add_messages, conditional_edges all import and compile in langgraph 1.2.6]

### Pattern 2: InjectedState para poliza_id en tools

**What:** `Annotated[str, InjectedState('poliza_id')]` hace que el campo no aparezca en el schema que ve el LLM. ToolNode inyecta el valor desde el state en runtime.
**When to use:** Siempre que un arg de tool deba venir del state del grafo, no del LLM.

```python
# Source: verified via tool_call_schema inspection in langgraph 1.2.6
from typing import Annotated
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState, ToolNode

@tool
def get_saldo(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> str:
    """Consulta saldo y próximo pago de la póliza activa. No requiere argumentos."""
    # poliza_id viene del state — LLM no puede pasarlo ni modificarlo
    ...

@tool
def get_estado(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> str:
    """Consulta estado de la póliza activa."""
    ...

@tool
def get_coberturas(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> str:
    """Consulta coberturas de la póliza activa."""
    ...

@tool
def escalate_to_human(reason: str) -> str:
    """Escalar a agente humano cuando el cliente lo pide o cuando no puedes responder."""
    # reason es el ÚNICO arg visible al LLM; muta state en node_answer
    return ""  # retorna vacío al LLM — sin info sensible

# ToolNode inyecta los InjectedState args automáticamente
tool_node = ToolNode([get_saldo, get_estado, get_coberturas, escalate_to_human])
llm_with_tools = get_llm("conversation").bind_tools(
    [get_saldo, get_estado, get_coberturas, escalate_to_human]
)
# Resultado: LLM ve tool_call_schema sin poliza_id (campo vacío para get_saldo/get_estado/get_coberturas)
# LLM SOLO puede llamar escalate_to_human con un argument: reason: str
```

[VERIFIED: tool_call_schema de tools con InjectedState devuelve solo los campos NO inyectados — confirmado en venv]

### Pattern 3: with_structured_output para JudgeRubric

**What:** `llm.with_structured_output(PydanticModel)` retorna un RunnableSequence que parsea la salida del LLM al modelo Pydantic. Usa tool calling nativo cuando el provider lo soporta (Gemini Flash via OpenRouter si soporta).
**When to use:** Judge node, KB auditor LLM layer.

```python
# Source: verified in langchain-openai 1.3.3 + langchain 1.3.11
from pydantic import BaseModel
from app.config.llm import get_llm

class JudgeRubric(BaseModel):
    is_in_scope: bool
    leaks_other_polizas: bool
    affirms_payment_without_cartera_approval: bool  # siempre False en F3
    factually_grounded: bool
    no_jailbreak_echo: bool
    no_pii_leak: bool
    no_external_links: bool
    sentiment_appropriate: bool
    rationale: str  # NEVER log raw — hash or truncate before structlog

judge_llm = get_llm("judge").with_structured_output(JudgeRubric)
# Retorna JudgeRubric | None (None si el modelo no pudo parsear)
# Manejar None como rechazo automático

# Aprobación: TODOS los flags deben ser True para pasar
def is_approved(rubric: JudgeRubric) -> bool:
    return all([
        rubric.is_in_scope,
        not rubric.leaks_other_polizas,
        not rubric.affirms_payment_without_cartera_approval,
        rubric.factually_grounded,
        rubric.no_jailbreak_echo,
        not rubric.no_pii_leak,  # True significa "sin pii leak" = bueno
        not rubric.no_external_links,  # True = sin links externos = bueno
        rubric.sentiment_appropriate,
    ])
# Nota: convención de naming — todos los flags True = aprobado.
# Flags is_in_scope=True / factually_grounded=True = bueno
# Flags leaks_other_polizas=True / affirms_payment=True / no_pii_leak=False = malo
```

[VERIFIED: with_structured_output disponible en ChatOpenAI 1.3.3; retorna RunnableSequence]

**Pitfall de naming:** los flags negativos (`no_pii_leak`, `no_external_links`) como True significan "sin pii" = bueno. Considera renombrar a `pii_leak_detected: bool` (True=malo) para evitar doble negación. Decisión de planner.

### Pattern 4: Webhook dispatch asíncrono (no bloquea Meta 200)

**What:** El graph.ainvoke() puede tardar 3-10s (LLM calls). Devolver 200 a Meta inmediatamente con `asyncio.create_task`. Chatwoot mirror via ARQ queue.

```python
# Source: FastAPI + asyncio pattern, app/webhooks/meta.py
import asyncio

async def _dispatch_message(*, msg: InboundMessage, meta: Any, redis: Any, app: Any) -> None:
    # ... HMAC, dedup, allowlist (unchanged from F2)

    # Prompt firewall ANTES de disparar el graph
    firewall_result = sanitize(msg.text.body)
    if firewall_result.blocked:
        await meta.send_text(to=msg.from_, body=T_06)
        asyncio.create_task(_mirror_blocked(msg, app.state.arq))
        return

    # Escape hatch regex (Layer 1 — D-15)
    if ESCAPE_REGEX.search(msg.text.body):
        asyncio.create_task(
            app.state.qa_graph.ainvoke(
                {"input": msg.text.body, "force_escalate": True},
                config={"configurable": {"thread_id": _normalize_e164(msg.from_)}},
            )
        )
        return

    # Normal flow: dispatch to graph (non-blocking)
    asyncio.create_task(
        app.state.qa_graph.ainvoke(
            {"input": msg.text.body},
            config={"configurable": {"thread_id": _normalize_e164(msg.from_)}},
        )
    )

    # Mirror inbound via ARQ (async, con retry)
    await app.state.arq.enqueue_job(
        "mirror_inbound",
        phone=msg.from_,
        text=msg.text.body,
        wamid=msg.id,
    )
    # return implícitamente — webhook handler devuelve 200 al caller
```

**Pitfall:** `asyncio.create_task` lanza la coroutine sin await. Si el FastAPI process muere, la task se pierde. Para v1 (DPG single-tenant, bajo volumen) esto es aceptable. Si hay reliability concerns, mover el graph dispatch también a ARQ — pero eso requiere serializar el estado de entrada, que arq hace vía JSON (Pydantic model_dump()).

### Pattern 5: Chatwoot Application API — mirror pattern

**What:** POST a `/api/v1/accounts/{account_id}/conversations/{conv_id}/messages` con `message_type: incoming` (cliente) o `outgoing` (bot).

**Endpoint flow para setup inicial (Wave 0 — operador hace esto en Chatwoot UI):**
1. Crear inbox "API Channel" en Chatwoot UI → obtener `inbox_id` e `inbox_identifier`
2. Crear contacto por número de teléfono: `POST /api/v1/accounts/{id}/contacts`
3. Crear conversación: `POST /api/v1/accounts/{id}/conversations` con `inbox_id`, `contact_id`, `source_id` (phone E.164)
4. Postear mensaje: `POST /api/v1/accounts/{id}/conversations/{conv_id}/messages`

```python
# Source: Chatwoot Application API docs (developers.chatwoot.com) + DeepWiki chatwoot analysis
# CITED: https://developers.chatwoot.com/api-reference/messages/create-new-message

class ChatwootClient:
    """READ pattern (same as MetaCloudClient): singleton via @lru_cache(maxsize=1)."""

    def __init__(self, http: httpx.AsyncClient, account_id: int) -> None:
        self._http = http
        self._account_id = account_id

    async def get_or_create_conversation(self, phone: str) -> int:
        """Return conversation_id for phone. Create if absent.

        Idempotent: check Redis for cached conv_id before calling API.
        Cache key: chatwoot:conv:{phone_hash} — TTL 7 days.
        """
        ...

    async def post_message(
        self,
        conversation_id: int,
        content: str,
        message_type: Literal["incoming", "outgoing"],
    ) -> None:
        """POST /api/v1/accounts/{id}/conversations/{conv_id}/messages"""
        await self._http.post(
            f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}/messages",
            json={"content": content, "message_type": message_type},
        )

    async def mark_resolved(self, conversation_id: int) -> None:
        """PATCH conversation status to resolved (closed node)."""
        await self._http.patch(
            f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}",
            json={"status": "resolved"},
        )


# Authentication header (Application API):
# Header name: "api_access_token" (NOT "Authorization: Bearer")
# Value: user API token from Chatwoot Profile → Access Token

@lru_cache(maxsize=1)
def get_chatwoot_client() -> ChatwootClient:
    http = httpx.AsyncClient(
        base_url=settings.chatwoot.url,
        headers={"api_access_token": settings.chatwoot.api_key.get_secret_value()},
        timeout=httpx.Timeout(10.0, connect=3.0),
    )
    return ChatwootClient(http=http, account_id=settings.chatwoot.account_id)
```

**message_type values (VERIFIED via Chatwoot source/docs):**
- `"incoming"` (code 0) — mensaje del cliente hacia el agente
- `"outgoing"` (code 1) — mensaje del agente/bot hacia el cliente
- `"activity"` (code 2) — sistema/evento (no usar para mirror)

[CITED: https://deepwiki.com/chatwoot/chatwoot/4.3-application-api-agent-operations]
[CITED: https://developers.chatwoot.com/api-reference/messages/create-new-message]

### Pattern 6: Prompt Firewall — Unicode + pattern matching

**What:** Sanitizar entrada antes de llegar al LLM. Unicode NFKC primero, luego strip chars invisibles por codepoint explícito, luego regex patterns.

```python
# Source: verified via Python unicodedata stdlib + regex testing
import re
import unicodedata

# Step 1: NFKC normalization (normaliza variantes Unicode, confusables)
# NOTA: NFKC NO elimina chars zero-width ni RTL/LTR override — necesita step 2
normalized = unicodedata.normalize("NFKC", text)

# Step 2: Strip chars invisibles por codepoint (NUNCA pegar el char literal en código)
# Codepoints a strip (referenciados por valor, no char literal):
# U+200B zero-width space, U+200C ZWNJ, U+200D ZWJ, U+200E LRM, U+200F RLM
# U+202A LRE, U+202B RLE, U+202C PDF, U+202D LRO, U+202E RLO (RTL override)
# U+2060 WJ, U+2061 FA, U+2062 IT, U+2063 IS, U+2064 TS, U+FEFF BOM
INVISIBLE_CHARS_PATTERN = re.compile(
    "["
    "​‌‍‎‏"
    "‪‫‬‭‮"
    "⁠⁡⁢⁣⁤"
    "﻿"
    "]"
)
cleaned = INVISIBLE_CHARS_PATTERN.sub("", normalized)

# Step 3: Strip control chars (0x00-0x08, 0x0b-0x0c, 0x0e-0x1f, 0x7f)
# Preservar \t (0x09), \n (0x0a), \r (0x0d)
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
cleaned = CONTROL_CHARS.sub("", cleaned)

# Step 4: Length cap (WhatsApp max 4096 → cap 4000 para margen)
if len(cleaned) > 4000:
    return SanitizeResult(blocked=True, reason="length_exceeded")

# Step 5: Pattern matching (case-insensitive, post-normalization)
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|above|all)\s+(instructions?|context)", re.I),
    re.compile(r"you\s+are\s+now\s+(a\s+)?", re.I),
    re.compile(r"(system|instruction|assistant|user)\s*:", re.I),
    re.compile(r"<\|.{0,20}\|>", re.I),               # sentinel tokens
    re.compile(r"(forget|disregard)\s+(everything|all)", re.I),
    re.compile(r"new\s+(role|persona|task|instructions?)", re.I),
    re.compile(r"DAN\b", re.I),                        # DAN jailbreak
    re.compile(r"developer\s+mode", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"prompt\s+(injection|hack)", re.I),
]
for pattern in INJECTION_PATTERNS:
    if pattern.search(cleaned):
        return SanitizeResult(blocked=True, reason=f"pattern_match:{pattern.pattern[:20]}")
```

[VERIFIED: unicodedata stdlib, NFKC behaviour tested in venv — NFKC does NOT remove zero-width chars, explicit codepoint strip required]

### Pattern 7: KB Auditor — 5 capas

**What:** Pipeline determinístico + LLM judge para validar el contenido del KB antes de cargar.

```python
# Source: [ASSUMED] — design derived from CONTEXT.md D-10, PROJECT.md reqs
from pydantic import BaseModel

class KBAuditRubric(BaseModel):
    contains_injection_attempt: bool
    contains_role_override: bool
    contains_exfiltration_pattern: bool
    contains_hidden_chars: bool
    contains_pii_pattern: bool
    contains_suspicious_links: bool
    rationale: str
    risk_score: int  # 0-100 (computed from flags in layer 5, NOT by LLM)

async def audit_kb(kb_path: str, redis: Any) -> int:
    """Return risk score 0-100. Raises RuntimeError if score > 50 (FAIL-CLOSED)."""
    content = Path(kb_path).read_text()

    # Layer 1: Hash check — skip if unchanged
    current_hash = hashlib.sha256(content.encode()).hexdigest()
    cached_hash = await redis.get("kb:last_audit_hash")
    if cached_hash == current_hash.encode():
        cached_score = await redis.get("kb:last_audit_score")
        return int(cached_score or 0)

    # Layer 2: Static patterns (same INJECTION_PATTERNS as prompt firewall)
    static_flags = run_static_patterns(content)

    # Layer 3: Diff extraction (only audit delta vs previous version)
    prev_content = await redis.get("kb:last_content") or b""
    diff = extract_diff(content, prev_content.decode())

    # Layer 4: LLM judge over the full content (or diff if diff is small)
    kb_judge = get_llm("judge").with_structured_output(KBAuditRubric)
    rubric = await kb_judge.ainvoke(build_kb_audit_prompt(content))

    # Layer 5: Risk scoring (DETERMINISTIC — NOT from LLM score)
    score = compute_risk_score(static_flags, rubric)
    # score = sum(weight_i * flag_i) where weights are tuned constants

    # Cache result
    await redis.set("kb:last_audit_hash", current_hash)
    await redis.set("kb:last_audit_score", str(score))
    await redis.set("kb:last_content", content.encode())

    if score > 50:
        raise RuntimeError(f"KB audit failed: risk_score={score}")
    return score
```

### Anti-Patterns to Avoid

- **LLM genera poliza_id:** el LLM NUNCA debe suministrar `poliza_id` como arg de tool. Usar siempre `InjectedState('poliza_id')`. Si el LLM intenta cambiar de póliza mid-conversation, el state ya la tiene locked y la tool ignora el intent.
- **Blocking en webhook:** NO hacer `await qa_graph.ainvoke(...)` en el webhook handler. Usar `asyncio.create_task`. Meta espera 200 en <5s o reintenta.
- **Loggear `rationale` raw del judge:** `rationale` puede contener tokens del usuario (inyección indirecta si el usuario craftea su texto para leakear). Loggear solo `rationale[:100]` o hash de rationale en structlog.
- **`async with` para AsyncPostgresSaver:** ver `app/config/checkpointer.py` — el patrón explícito `__aenter__/__aexit__` ya está documentado. No usar `async with` en lifespan.
- **Pydantic models directos en arq:** arq serializa args como JSON. Pasar solo primitivos (str, int, dict) o `.model_dump()` explícito. No pasar objetos Pydantic directamente como kwargs de job.
- **NFKC como única sanitización:** NFKC NO elimina zero-width spaces ni RTL override. Siempre step 2 (strip por codepoint) después de NFKC.
- **message_type="bot_generated":** no existe. Para marcar bot messages en Chatwoot usar `message_type="outgoing"`. El `sender_type` en la respuesta indicará quién lo envió.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| State machine con persistencia | Custom dict en Redis | LangGraph StateGraph + AsyncPostgresSaver | Reduce, checkpointing, resume, thread isolation ya resueltos |
| Structured output parsing | JSON parse + regex | `llm.with_structured_output(PydanticModel)` | Retry automático en parse failure, type-safe, LangSmith tracing |
| Tool arg injection from state | Global variable / closure | `InjectedState('field')` de langgraph.prebuilt | Limpio, testeable, patrón oficial de LangGraph |
| HTTP client para Chatwoot | requests síncrono | httpx.AsyncClient (ya en deps) | Async-first, mismo patrón que MetaCloudClient |
| Queue para mirror async | asyncio.Queue en memoria | ARQ (ya en deps, ya en WorkerSettings) | Persistencia en Redis, retry automático, survives restart |
| Unicode attack detection | Inventar patterns ad hoc | unicodedata.normalize('NFKC') + strip por codepoint | Cobertura sistemática de attacks documentados |
| Regex injection detection | LLM detection only | Regex patterns + LLM (capas separadas) | Regex es determinístico y free; LLM es capa extra |
| frontmatter parsing en fixtures | Implementar parser | stdlib re + pyyaml.safe_load (ya transitive dep) | 3 líneas, ya disponible |

---

## Open Questions

1. **Shape exacta de `/api/cliente/listar_cliente_por_documento/`**
   - Qué sabemos: endpoint existe (mencionado en CLAUDE.md + web search snippet). Probablemente DRF pagination con `{count, results: [ClienteObj]}`. Probablemente query param `?numero_documento=X` o `?documento=X`.
   - Qué no sabemos: query param name exacto, si retorna full 122-field object o subset, si retorna también polizas embebidas del cliente.
   - Recomendación: **Wave 0 task obligatorio** — probe real con curl autenticado contra sandbox DPG antes de modelar `ClienteRaw` Pydantic. Alternativa si falla: `GET /api/poliza/?cliente_numero_documento=X` (DRF filter sobre poliza list).
   - Tag: [ASSUMED] — shape no confirmada via tools.

2. **Chatwoot self-hosted status en chat.landatech.org**
   - Qué sabemos: infraestructura desplegada en F1. `chat.landatech.org` no responde desde cliente local (puede estar detrás de Railway interno).
   - Railway health endpoint responde 200 — Postgres + Redis operativos. El servicio de landa-agent-service está up.
   - Recomendación: Wave 0 — operador verifica acceso a Chatwoot UI, obtiene `CHATWOOT_API_KEY`, `CHATWOOT_ACCOUNT_ID`, crea inbox "API Channel" y obtiene `CHATWOOT_INBOX_ID`.
   - Tag: [ASSUMED] Chatwoot está operativo en Railway (no verificable desde local).

3. **Gemini 2.5 Flash via OpenRouter + structured output**
   - Qué sabemos: `with_structured_output` disponible en ChatOpenAI 1.3.3. OpenRouter soporta tool calling para Gemini 2.5 Flash.
   - Qué no sabemos: si el endpoint de OpenRouter para Gemini 2.5 Flash soporta `response_format` JSON schema o solo tool-calling-based structured output.
   - Recomendación: Wave 0 — test con `get_llm("judge").with_structured_output(JudgeRubric).ainvoke("test")` contra OpenRouter real. Si falla JSON mode, langchain fallback a prompt-based parsing.
   - Tag: [ASSUMED] — structured output funciona con Gemini Flash via OpenRouter (no probado en este repo aún).

4. **arq + redis 8 en producción (Railway)**
   - Qué sabemos: `arq.connections` usa `redis.asyncio` (compatible). Local import OK.
   - Qué no sabemos: si Railway Redis 8 tiene alguna configuración que rompa el arq worker en runtime (AUTH, TLS, pool behavior).
   - Recomendación: Wave 3 — test en Railway con un job simple `mirror_noop` antes de full Chatwoot integration. pyproject.toml ya tiene el `override-dependencies` para arq.
   - Tag: [ASSUMED] — arq 0.28 funciona con redis 8.0.1 en producción (compatible en código, no probado en Railway).

5. **Converación nueva vs resumida en LangGraph**
   - Qué sabemos: `thread_id = wa_phone` E.164. `AsyncPostgresSaver` persiste indefinidamente. Si el cliente volvió meses después, el grafo resume desde el último state (posiblemente `answering_qa` o `closed`).
   - Qué no sabemos: si `closed` (con `END`) permite un nuevo `ainvoke` — en LangGraph, invocar un thread en estado `END` con nueva input crea un nuevo "turn" desde el último checkpoint, que puede ser confuso.
   - Recomendación: el node `closed` debe transicionar a `awaiting_identification` si detecta nueva input (no salir a END si el thread puede recibir nuevos mensajes). Esto es un detalle de diseño para el planner.
   - Tag: [ASSUMED] — comportamiento de resume en threads terminados no verificado en langgraph 1.2.6.

---

## Common Pitfalls

### Pitfall 1: asyncio.create_task pierde excepciones

**What goes wrong:** Si el graph `ainvoke` lanza una excepción dentro del task, Python no la propaga al caller — se "traga" silenciosamente.
**Why it happens:** `create_task` fire-and-forget sin attach de callback.
**How to avoid:** Adjuntar un done callback que logee excepciones:
```python
def _log_task_error(task: asyncio.Task) -> None:
    if task.exception():
        log.error("qa_graph.task.error", error_type=type(task.exception()).__name__)

t = asyncio.create_task(qa_graph.ainvoke(...))
t.add_done_callback(_log_task_error)
```
**Warning signs:** Judge o tools nunca aparecen en LangSmith pero el webhook devuelve 200.

### Pitfall 2: InjectedState requiere ToolNode — no funciona con invoke directo

**What goes wrong:** Si llamas `get_saldo.invoke({"poliza_id": "123"})` directamente en tests, `InjectedState` no se inyecta porque eso es manejado por ToolNode.
**Why it happens:** InjectedState es un annotation que ToolNode interpreta en runtime.
**How to avoid:** En tests, pasar `poliza_id` explícitamente en el dict de args cuando se invoca la tool directamente. O testear el tool via ToolNode con un state stub.
**Warning signs:** KeyError en tests de tools al intentar acceder a `poliza_id`.

### Pitfall 3: `with_structured_output` retorna None si el modelo no parsea

**What goes wrong:** `rubric = await judge_llm.ainvoke(messages)` retorna `None` si el LLM no genera JSON válido contra el schema.
**Why it happens:** Gemini puede rehusar generar en formato estructurado por algunas queries.
**How to avoid:** Siempre guard: `if rubric is None: return reject_response(reason="judge_parse_failed")`. Tratar None como rechazo.
**Warning signs:** AttributeError en `rubric.is_in_scope` cuando rubric es None.

### Pitfall 4: Chatwoot inbox WhatsApp vs API Channel

**What goes wrong:** Usar el inbox de WhatsApp (F4) para el mirror de F3. El WhatsApp inbox en Chatwoot espera recibir mensajes via webhook propio de Chatwoot — no acepta `POST /api/v1/accounts/{id}/conversations/{id}/messages` de la misma forma.
**Why it happens:** Chatwoot tiene dos tipos de inbox para WhatsApp: nativo (via Chatwoot webhook) y API Channel (via Application API).
**How to avoid:** Crear inbox "API Channel" dedicado para el mirror (per D en CONTEXT.md Claude's Discretion). El inbox WhatsApp nativo se conecta en F4.
**Warning signs:** 422 Unprocessable Entity al intentar postear mensajes al WhatsApp inbox via Application API.

### Pitfall 5: Token leak via rationale en LangSmith

**What goes wrong:** El campo `rationale: str` del JudgeRubric puede contener el texto original del usuario (para justificar el rechazo). Si se logea raw a LangSmith, los tokens del usuario quedan en los traces.
**Why it happens:** LangSmith traza todos los inputs/outputs automáticamente.
**How to avoid:** `rationale` SOLO para debugging offline. En structlog: `rationale_len=len(rubric.rationale)`, NUNCA `rationale=rubric.rationale`. LangSmith traces son internos — aceptable en dev pero agregar PII scrubbing en F5.

### Pitfall 6: arq job kwargs deben ser JSON-serializable

**What goes wrong:** `await arq.enqueue_job("mirror_inbound", payload=MirrorPayload(...))` falla porque arq no sabe serializar Pydantic models.
**Why it happens:** arq serializa kwargs via json.dumps por default.
**How to avoid:** Pasar solo primitivos: `await arq.enqueue_job("mirror_inbound", phone=phone, text=text, wamid=wamid)`. Reconstruir objetos dentro de la job function si necesario.

### Pitfall 7: `closed` node con `END` no permite reabrir conversación

**What goes wrong:** Si el grafo llega a `closed → END`, un nuevo `ainvoke` en el mismo thread_id no sabe qué hacer — el state persiste pero el "current node" es END.
**Why it happens:** LangGraph resume desde el último checkpoint.
**How to avoid:** En vez de transicionar a END desde `closed`, transicionar a `awaiting_identification` para permitir nuevas consultas. O: reset el checkpoint al detectar `node == "closed"` en un nuevo turn (borrar checkpoint y reiniciar).

### Pitfall 8: FastAPI ORJSONResponse deprecated en 0.138.1

**What goes wrong:** Warning "ORJSONResponse is deprecated" en tests (ya visible en 66 tests actuales).
**Why it happens:** FastAPI 0.138.1 serializa JSON natively.
**How to avoid:** F3 no toca `main.py` `default_response_class` — puede dejarse como está y limpiarse en F5. No es blocking para F3.

---

## Code Examples

### Graph invoke en webhook (replacment del echo branch)

```python
# Source: [ASSUMED] pattern — LangGraph ainvoke with checkpointer config
# En app/webhooks/meta.py, reemplaza el branch de echo (líneas 180-200 aprox)

if msg.type == "text" and msg.text is not None:
    # Prompt firewall primero
    sanitized = sanitize(msg.text.body)
    if sanitized.blocked:
        await meta.send_text(to=msg.from_, body=MESSAGES["T-06"])
        return

    # Escape hatch regex (Layer 1 — D-15)
    if ESCAPE_REGEX.search(msg.text.body):
        asyncio.create_task(
            request.app.state.qa_graph.ainvoke(
                {"input": msg.text.body, "force_escalate": True},
                config={"configurable": {"thread_id": _normalize_e164(msg.from_)}},
            )
        )
        return

    # Normal dispatch — fire-and-forget, NO bloquea 200
    task = asyncio.create_task(
        request.app.state.qa_graph.ainvoke(
            {"input": msg.text.body},
            config={"configurable": {"thread_id": _normalize_e164(msg.from_)}},
        )
    )
    task.add_done_callback(_log_task_error)

    # Mirror inbound via ARQ
    if request.app.state.arq:
        await request.app.state.arq.enqueue_job(
            "mirror_inbound",
            phone=msg.from_,
            text=msg.text.body,
            wamid=msg.id,
        )
    return
```

### lifespan additions (app/main.py)

```python
# Source: [ASSUMED] pattern — same as F2 lifespan additions
# 6. ARQ pool para enqueue jobs (Chatwoot mirror)
from arq import create_pool
from arq.connections import RedisSettings
arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis.url.get_secret_value()))
app.state.arq = arq_pool

# 7. Chatwoot client
from app.integrations.chatwoot import get_chatwoot_client
app.state.chatwoot = get_chatwoot_client()

# 8. KB audit — FAIL-CLOSED
from app.security.kb_auditor import audit_kb
kb_risk = await audit_kb("knowledge/dpg_cartera.md", redis=app.state.redis)
if kb_risk > 50:
    raise RuntimeError(f"KB audit failed: risk={kb_risk}. Service not started.")

# 9. LangGraph QA graph
from app.features.qa.graph import build_qa_graph
app.state.qa_graph = build_qa_graph().compile(checkpointer=app.state.checkpointer)

# Finally block — add:
# await app.state.arq.close()  (no __aexit__ needed, just close())
```

### settings.py addition (ChatwootSettings)

```python
# Source: [ASSUMED] — same pattern as WhatsAppSettings in app/config/settings.py
class ChatwootSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHATWOOT_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )
    url: str = "https://chat.landatech.org"
    api_key: SecretStr     # REQUIRED F3
    account_id: int        # REQUIRED F3
    inbox_id: int          # REQUIRED F3 — API Channel inbox id

# Add to Settings class:
# chatwoot: ChatwootSettings = Field(default_factory=ChatwootSettings)
```

### pre-commit mypy additional_dependencies update

```yaml
# F3 agrega nuevos módulos que mypy isolated env necesita
# Agregar en .pre-commit-config.yaml bajo mypy additional_dependencies:
- langgraph==1.2.6         # InjectedState, StateGraph, ToolNode
- langchain==1.3.11        # with_structured_output, tools
- langsmith==0.9.3         # auto-tracing, noqa needed for strict
# langchain-openai ya está. pyyaml no necesita mypy stubs.
# langgraph, langchain, langchain_openai todos tienen py.typed (confirmado).
```

[VERIFIED: py.typed presente en langgraph, langchain, langchain_openai en venv instalado]

### ARQ worker update (app/worker.py)

```python
# Source: [ASSUMED] — arq WorkerSettings pattern from F1
# Agregar funciones al WorkerSettings existente

async def mirror_inbound(ctx: dict, *, phone: str, text: str, wamid: str) -> None:
    """Mirror inbound WhatsApp message to Chatwoot."""
    chatwoot = get_chatwoot_client()
    conv_id = await chatwoot.get_or_create_conversation(phone)
    await chatwoot.post_message(conv_id, text, message_type="incoming")

async def mirror_outbound(ctx: dict, *, phone: str, text: str, wamid: str) -> None:
    """Mirror outbound bot message to Chatwoot."""
    chatwoot = get_chatwoot_client()
    conv_id = await chatwoot.get_or_create_conversation(phone)
    await chatwoot.post_message(conv_id, text, message_type="outgoing")

class WorkerSettings:
    functions = [mirror_inbound, mirror_outbound]  # replace [_noop]
    redis_settings = RedisSettings.from_dsn(settings.redis.url.get_secret_value())
```

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| LangGraph 0.x `interrupt_before` | LangGraph 1.x `interrupt()` in node (F4, NOT F3) | interrupt() not needed in F3 — solo `END` |
| Chatwoot v1 Postman API (community) | Chatwoot Application API `/api/v1/accounts/` con `api_access_token` header | Stable, self-hosted compatible |
| LangChain 0.x OutputParser | langchain 1.x `with_structured_output(PydanticModel)` | Type-safe, retry automático, limpio |
| Custom state en Redis | LangGraph StateGraph + AsyncPostgresSaver | Transaccional, resumible, multi-session |

**No hay cambios de API breaking en langgraph 1.2.6 vs 1.1.x para los patrones que usa F3** — InjectedState, StateGraph, add_messages, conditional_edges son todos stable surface. [ASSUMED — basado en training knowledge del changelog, no verificado contra release notes oficiales]

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `/api/cliente/listar_cliente_por_documento/?documento=X` retorna `{count, results: [ClienteObj]}` con query param `documento` | Standard Stack / Open Questions | Shape distinta → reescribir `get_clientes_by_documento`, Wave 0 bloqueada hasta probe real |
| A2 | Chatwoot self-hosted en chat.landatech.org está operativo en Railway (sin tráfico pero accesible) | Environment Availability | No accesible → operador debe hacer redeploy del stack Chatwoot antes de Wave 2 |
| A3 | `with_structured_output(JudgeRubric)` funciona con Gemini 2.5 Flash via OpenRouter (tool calling o JSON mode) | Pattern 3 | Falla → usar prompt-based parser con `OutputParser` de langchain; mayor latencia |
| A4 | arq 0.28 funciona con redis 8.0.1 en Railway en producción (no solo local) | Pattern 4 | Falla en Railway → investigar TLS/AUTH config en arq RedisSettings |
| A5 | Conversación en Chatwoot API Channel puede recibir mensajes via Application API sin configuración adicional (callback URL no requerido para mirror one-way) | Pattern 5 | Chatwoot rechaza posts sin callback URL → registrar callback URL en inbox config |
| A6 | LangGraph 1.2.6 no tiene breaking changes relevantes desde 1.1.x para los patrones de F3 | State of the Art | Breaking change → ajustar patterns en Wave 1 |
| A7 | Invocar un graph thread en estado `closed` (post-END) con nuevo ainvoke crea un nuevo turn o lanza error manejable | Open Questions #5 | Comportamiento inesperado → implementar reset explícito del checkpoint al detectar state `closed` |

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Railway service (landa-agent-service) | Smoke test | ✓ | health 200 | — |
| Postgres (Railway) | LangGraph checkpointer | ✓ | latency 37ms | — |
| Redis (Railway) | Cache + ARQ + dedup | ✓ | latency 5ms | — |
| OpenRouter | LLM calls (conversation + judge) | ✓ | latency ~963ms | — |
| SoftSeguros sandbox (`/test/poliza/228700`) | Q&A tools | ✓ | tested in F2 | — |
| Chatwoot (chat.landatech.org) | Mirror | [ASSUMED] | no probe exitoso desde local | Operador verifica en Railway console |
| `/api/cliente/listar_cliente_por_documento/` | Identificación por documento | UNKNOWN | no probado en F2 | Fallback: GET /api/poliza/?cliente_numero_documento=X |
| Meta WhatsApp (test number) | Smoke E2E | ✓ | +1 555-203-1790 verificado F2 | — |

**Missing dependencies con no fallback:**
- Chatwoot API key + account_id + inbox_id (operador debe obtener en Wave 0)
- `/api/cliente/listar_cliente_por_documento/` shape confirmada (Wave 0 probe)

**Missing dependencies con fallback:**
- Si listar_cliente_por_documento falla: usar `GET /api/poliza/?cliente_numero_documento=X` (DRF filter sobre poliza list, retorna polizas directamente sin round-trip de cliente)

---

## Security Domain

> `security_enforcement: true` en config.json. ASVS level 1 aplicable.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No (Meta HMAC ya en F2, no user auth) | — |
| V3 Session Management | Yes (LangGraph thread_id por phone) | AsyncPostgresSaver + E.164 normalization |
| V4 Access Control | Yes (tool boundaries, allowlist operaciones) | `InjectedState` + allowlist en code |
| V5 Input Validation | Yes | prompt_firewall.py + Pydantic v2 en todos I/O |
| V6 Cryptography | No (no nuevas operaciones criptográficas en F3) | — |

### Known Threat Patterns para este stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt injection via user input (LLM01) | Tampering | prompt_firewall.py (regex + unicode strip) + judge rubric `no_jailbreak_echo` |
| Indirect prompt injection via KB content (LLM01) | Tampering | kb_auditor.py pipeline + KB wrapping con `== REFERENCIA ==` |
| Poliza_id manipulation via tool arg forgery (LLM01 + Privilege Escalation) | Elevation of Privilege | `InjectedState('poliza_id')` — LLM no puede suministrar este arg |
| Output leaking PII from SoftSeguros raw response (LLM02 / LLM06) | Information Disclosure | Tool output sanitization — solo allowlist de campos antes de LLM |
| Judge output leaking rationale (LLM06) | Information Disclosure | `rationale` NUNCA loggeado raw — solo `rationale_len` |
| Chatwoot API key exposure (Credential Exposure) | Information Disclosure | `SecretStr` en ChatwootSettings — nunca en repr/logs |
| Mid-conversation poliza switch (Privilege Escalation) | Elevation of Privilege | `poliza_id` locked in state — LLM redirect bloqueado por code, no por prompt |
| Jailbreak via adversarial KB content | Tampering | kb_auditor 5 capas + FAIL-CLOSED startup |
| ARQ job poisoning (si alguien puede enqueue directamente) | Tampering | ARQ no expuesto externamente — solo vía webhook handler interno |

**Defensa en profundidad (from CLAUDE.md):** el LLM no es la única línea de defensa. Las restricciones críticas están en código:
1. `poliza_id` en state → InjectedState (código)
2. No list_all tools (código — tools solo aceptan la póliza locked)
3. Judge sobre CADA outbound (código — no se salta)
4. Output firewall en F4 (código — `pago confirmado` solo en path post-cartera)

---

## Sources

### Primary (HIGH confidence)
- `app/integrations/softseguros.py` — SoftSegurosClient pattern (lru_cache, _cached_get, tenacity outer + pybreaker inner)
- `app/config/checkpointer.py` — AsyncPostgresSaver lifespan pattern (explicit __aenter__/__aexit__)
- `app/integrations/openrouter.py` — get_llm(role) factory, ROLE_ALIASES
- `app/main.py` — lifespan resource acquisition order
- `app/config/settings.py` — ChatwootSettings pattern (SecretStr, env_prefix, BaseSettings)
- `.planning/phases/02-integraci-n-softseguros-whatsapp-cloud-api/SOFTSEGUROS_API_NOTES.md` — Schema real capturado: 184 campos poliza, 122 campos cliente, enum 8 estados
- Venv inspection via `importlib.metadata` — versiones confirmadas: langgraph 1.2.6, langchain 1.3.11, langchain-openai 1.3.3
- Import verification en venv — InjectedState, ToolNode, StateGraph, add_messages, END, with_structured_output todos importan y funcionan

### Secondary (MEDIUM confidence)
- Chatwoot Application API — `POST /api/v1/accounts/{id}/conversations/{id}/messages` con `api_access_token` header, `message_type: incoming|outgoing` [CITED: developers.chatwoot.com + deepwiki.com/chatwoot]
- InjectedState API docs — class signature, field injection pattern [CITED: reference.langchain.com/python/langgraph.prebuilt/tool_node/InjectedState]
- SoftSeguros `/api/cliente/listar_cliente_por_documento/` — endpoint mencionado en CLAUDE.md + web search snippet de academia.softseguros.com — retorna lista paginada de clientes [MEDIUM — no probado en F2]

### Tertiary (LOW confidence — marcado ASSUMED)
- Shape exacta de `/api/cliente/listar_cliente_por_documento/` query param name (`?documento=` vs `?numero_documento=`) — [ASSUMED]
- Comportamiento de LangGraph thread en estado END al recibir nuevo ainvoke — [ASSUMED]
- arq 0.28 + redis 8 en Railway producción sin issues — [ASSUMED]

---

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH — todos los paquetes instalados y verificados en venv
- LangGraph patterns: HIGH — InjectedState, StateGraph, add_messages verificados vía import + schema inspection
- Chatwoot API: MEDIUM — endpoints documentados en fuentes oficiales pero no testados contra instancia real
- SoftSeguros listar_cliente_por_documento: LOW — no probado en F2, shape inferida
- Security patterns (Unicode, regex injection): HIGH — probados en Python stdlib

**Research date:** 2026-06-29
**Valid until:** 2026-07-29 (paquetes estables; Chatwoot API stable; OpenRouter slugs cambian más rápido — re-verificar si pasa más de 30 días)
