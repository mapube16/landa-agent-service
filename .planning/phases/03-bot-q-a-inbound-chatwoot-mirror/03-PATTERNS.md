# Phase 3: Bot Q&A inbound + Chatwoot mirror - Pattern Map

**Mapped:** 2026-06-29
**Files analyzed:** 21 new/modified files
**Analogs found:** 19 / 21

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `app/features/qa/state.py` | feature / model | stateful (LangGraph) | `app/models/softseguros.py` (PolizaRaw TypedDict shape) | partial — use RESEARCH Pattern 1 |
| `app/features/qa/graph.py` | feature / orchestrator | event-driven (LangGraph) | `app/config/checkpointer.py` (LangGraph setup) | partial — use RESEARCH Pattern 1 |
| `app/features/qa/nodes.py` | feature / handler | request-response + tool-call | `app/webhooks/meta.py` (`_dispatch_message`) | role-match |
| `app/features/qa/tools.py` | feature / tools | request-response (CRUD) | `app/integrations/softseguros.py` (public read methods) | role-match |
| `app/features/qa/prompts.py` | feature / utility | transform | `app/features/handoff/echo.py` (pure function pattern) | partial |
| `app/features/qa/knowledge_base.py` | feature / utility | file-I/O | `app/features/handoff/echo.py` (pure function pattern) | partial |
| `app/features/qa/messages.py` | feature / utility | transform | `app/features/handoff/echo.py` (constants + pure functions) | exact |
| `app/security/prompt_firewall.py` | security / middleware | transform | `app/webhooks/meta.py` (`_verify_signature` pattern) | role-match |
| `app/security/judge.py` | security / service | request-response (LLM) | `app/integrations/openrouter.py` (`get_llm` + structured output) | exact |
| `app/security/kb_auditor.py` | security / service | file-I/O + LLM | `app/integrations/softseguros.py` (cache layer + `_cached_get`) | role-match |
| `app/integrations/chatwoot.py` | integration / client | request-response | `app/integrations/meta_cloud.py` (httpx singleton factory) | exact |
| `app/integrations/softseguros.py` (MODIFY) | integration / client | CRUD | self — adds `get_clientes_by_documento` method | self-modify |
| `app/models/softseguros.py` (MODIFY) | model | transform | self — adds `ClienteRaw`, `EstadoCodigo`, sanitized DTOs | self-modify |
| `app/config/settings.py` (MODIFY) | config | — | self — adds `ChatwootSettings` with `WhatsAppSettings` as pattern | exact analog |
| `app/webhooks/meta.py` (MODIFY) | webhook / handler | request-response | self — replaces echo branch, preserves HMAC/dedup/allowlist | self-modify |
| `app/main.py` (MODIFY) | app / lifespan | event-driven | self — adds new resources to lifespan blocks 4+5 | self-modify |
| `app/worker.py` (MODIFY) | worker / queue | event-driven | self — adds ARQ job functions | self-modify |
| `tests/features/qa/test_*.py` | test | — | `tests/test_webhooks_meta.py` (monkeypatch + AsyncMock pattern) | exact |
| `tests/security/test_*.py` | test | — | `tests/test_softseguros_readonly.py` (introspection guard pattern) | role-match |
| `tests/integrations/test_chatwoot.py` | test | — | `tests/test_integrations_softseguros.py` (stub_http + stub_redis fixtures) | exact |
| `.github/workflows/kb-audit.yml` | infra / CI | — | no analog (new CI file) | no analog |

---

## Pattern Assignments

### `app/features/qa/state.py` (feature / model, stateful)

**Analog:** `app/models/softseguros.py` (passthrough model pattern) + RESEARCH.md Pattern 1

The existing `app/models/softseguros.py` uses a simple `TypeAlias` passthrough. F3 needs a proper TypedDict. Pattern comes from RESEARCH.md Pattern 1 (verified imports).

**Imports pattern** (lines 1-7 from `app/models/softseguros.py`):
```python
from __future__ import annotations
from typing import Any
# F3 replaces with:
from typing import Annotated, Literal, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
```

**Core pattern** — TypedDict with `Annotated` reducer (RESEARCH.md Pattern 1, verified in langgraph 1.2.6):
```python
class QAState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]  # accumulates
    poliza_id: str | None          # locked once set — NEVER from LLM
    cliente_doc: str | None
    polizas_list: list[dict[str, Any]]
    doc_retries: int               # max 1 (D-03)
    judge_retries: int             # max 1 (D-06)
    node: Literal[
        "awaiting_identification",
        "awaiting_policy_choice",
        "answering_qa",
        "escalating",
        "closed",
    ]
```

**No test analog** — TypedDict schemas tested implicitly via graph compile.

---

### `app/features/qa/graph.py` (feature / orchestrator, event-driven)

**Analog:** `app/config/checkpointer.py` (LangGraph resource factory) + RESEARCH.md Pattern 1

**Imports pattern** (from `app/config/checkpointer.py` lines 37-43):
```python
from __future__ import annotations
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.config.settings import settings
# F3 adds:
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from app.features.qa.state import QAState
```

**Core pattern** — `build_qa_graph()` factory function (same factory-function shape as `build_checkpointer_cm()` in `app/config/checkpointer.py` line 46):
```python
def build_qa_graph() -> StateGraph:
    builder = StateGraph(QAState)
    # add_node / add_conditional_edges / set_entry_point / add_edge(X, END)
    return builder
# Compile in lifespan, NOT here:
# app.state.qa_graph = build_qa_graph().compile(checkpointer=app.state.checkpointer)
```

**Key invariant from `app/config/checkpointer.py` lines 17-22** (never `async with` in lifespan):
```python
# The lifespan owns entering/exiting exactly once — explicit __aenter__/__aexit__
# The checkpointer compile is analogous: compile() is not an async ctx manager,
# it returns a compiled graph directly. Safe to call synchronously in lifespan.
app.state.qa_graph = build_qa_graph().compile(checkpointer=app.state.checkpointer)
```

---

### `app/features/qa/nodes.py` (feature / handler, request-response + tool-call)

**Analog:** `app/webhooks/meta.py` (`_dispatch_message` function — lines 145-233)

**Imports pattern** (from `app/webhooks/meta.py` lines 39-54):
```python
from __future__ import annotations
from typing import Any
import structlog
from app.config.settings import settings
# F3 adds:
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import ToolNode
from app.features.qa.state import QAState
from app.features.qa.messages import T_01, T_02, T_03, T_06, T_07, T_08
from app.security.judge import judge_response
```

**Core pattern** — async node function with `# noqa: BLE001` for broad exceptions (from `app/webhooks/meta.py` lines 184-200):
```python
async def node_identify(state: QAState) -> dict[str, Any]:
    """awaiting_identification node."""
    # ... call softseguros.get_clientes_by_documento
    # ... return state mutations
    pass

# Error handling with type(exc).__name__ (from meta.py lines 184-190):
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "qa.node.error",
            node="awaiting_identification",
            error_type=type(exc).__name__,
        )
```

**Log pattern** — structlog `result=` field (from `app/webhooks/meta.py` lines 192-200):
```python
log.info(
    "qa.node.identify.found",
    phone_hash=_hash_phone(phone),
    poliza_count=len(polizas),
    result="polizas_listed",
)
```

---

### `app/features/qa/tools.py` (feature / tools, CRUD)

**Analog:** `app/integrations/softseguros.py` public read methods (lines 236-261)

**Imports pattern** (no direct analog — from RESEARCH.md Pattern 2):
```python
from __future__ import annotations
from typing import Annotated
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from app.integrations.softseguros import get_softseguros_client
from app.security.prompt_firewall import sanitize_tool_output
```

**Core pattern** — `@tool` with `InjectedState` (RESEARCH.md Pattern 2, verified in langgraph 1.2.6):
```python
@tool
async def get_saldo(
    poliza_id: Annotated[str, InjectedState("poliza_id")],
) -> str:
    """Consulta saldo y próximo pago de la póliza activa."""
    client = get_softseguros_client()
    raw = await client.get_poliza(poliza_id)
    # Allowlist: only [saldo_pendiente, proximo_pago_monto, proximo_pago_fecha, moneda]
    return sanitize_tool_output(raw, allowlist=["saldo_pendiente", ...])
```

**Analog method shape** (from `app/integrations/softseguros.py` lines 236-261):
```python
# Copy this exact shape for each tool's internal call:
async def get_poliza(self, poliza_id: str) -> PolizaRaw:
    """GET ``/api/poliza/{poliza_id}/``."""
    return await self._cached_get(poliza_id, "poliza", f"/api/poliza/{poliza_id}/")
```

---

### `app/features/qa/prompts.py` (feature / utility, transform)

**Analog:** `app/features/handoff/echo.py` (pure functions, no I/O — lines 1-47)

**Imports pattern** (from `app/features/handoff/echo.py` lines 1-15):
```python
from __future__ import annotations
from app.config.settings import settings
```

**Core pattern** — pure function returning str (from `app/features/handoff/echo.py` lines 37-39):
```python
def format_echo(text: str) -> str:
    """Return ``'echo: <text>'`` — ..."""
    return f"echo: {text}"

# F3 analog:
def system_prompt(kb_content: str, poliza_id: str | None = None, l4_flags: dict | None = None) -> str:
    """Build system prompt injecting KB between == REFERENCIA == delimiters."""
    ...
```

**Module docstring** — explain "why" briefly, no planning context (from `echo.py` line 1):
```
"""Pure functions over settings + input. No I/O."""
```

---

### `app/features/qa/knowledge_base.py` (feature / utility, file-I/O)

**Analog:** `app/features/handoff/echo.py` (pure functions) + `app/healthcheck.py` startup probe pattern

**Core pattern** — load once, cache result (from `app/healthcheck.py` `_probe` pattern lines 40-56):
```python
# lru_cache on the load function (not on the module) — same singleton approach as get_llm:
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=1)
def load_kb() -> str:
    """Load dpg_cartera.md and wrap in == REFERENCIA == delimiters."""
    content = (Path(__file__).parent.parent.parent / "knowledge" / "dpg_cartera.md").read_text()
    return f"== REFERENCIA ==\n{content}\n== FIN REFERENCIA =="
```

---

### `app/features/qa/messages.py` (feature / utility, transform)

**Analog:** `app/features/handoff/echo.py` (module-level constants + pure functions — lines 17-47)

**Imports pattern** (from `app/features/handoff/echo.py` lines 1-15):
```python
from __future__ import annotations
```

**Core pattern** — module-level string constants + optional interpolate() helper (from `echo.py` lines 37-44):
```python
def format_echo(text: str) -> str:
    return f"echo: {text}"

# F3 analog — constants only (D-13), no LLM:
T_01 = "¡Hola! 👋 Soy el asistente virtual de DPG Seguros. Para ayudarte, ¿me das tu número de documento?"
T_02 = "No encontré ese documento en nuestro sistema. ¿Puedes confirmarlo? A veces se cuela un dígito."
T_03 = "Sigo sin encontrar ese documento. Te voy a conectar con un agente de DPG para que te ayude."
T_04 = "Encontré {N} pólizas a tu nombre:\n\n{lista_numerada}\n\n¿Sobre cuál querés preguntar? Respondé con el número o el número de póliza."
T_05 = "Esta póliza no tiene esa información disponible o está fuera del alcance que puedo consultar. ¿Querés que te conecte con un agente?"
T_06 = "No puedo consultar tu información en este momento. Te voy a conectar con un agente que pueda ayudarte."
T_07 = "Disculpá, no pude armar una respuesta clara a tu pregunta. Te conecto con un agente de DPG."
T_08 = "Listo, te conecto con un agente de DPG. Un humano te va a contestar pronto acá mismo."

def interpolate_t04(n: int, lista_numerada: str) -> str:
    return T_04.format(N=n, lista_numerada=lista_numerada)
```

---

### `app/security/prompt_firewall.py` (security / middleware, transform)

**Analog:** `app/webhooks/meta.py` `_verify_signature` (deterministic security check — lines 57-65)

**Imports pattern** (from `app/webhooks/meta.py` lines 39-46):
```python
from __future__ import annotations
import hashlib
import hmac
# F3 replaces with:
import re
import unicodedata
from dataclasses import dataclass
import structlog
```

**Core pattern** — deterministic check, return structured result NOT raise (from `meta.py` lines 57-65):
```python
def _verify_signature(raw_body: bytes, header_value: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value)

# F3 analog — same pattern: function returns a result, caller decides action:
@dataclass
class SanitizeResult:
    blocked: bool
    reason: str = ""
    cleaned: str = ""

def sanitize(text: str) -> SanitizeResult:
    """Unicode NFKC → strip invisible codepoints → length cap → injection patterns."""
    ...
```

**Error pattern** — caller handles, never swallow in the function (from `meta.py` lines 107-112):
```python
if not header_sig or not _verify_signature(...):
    log.warning("webhook.hmac.invalid", header_present=bool(header_sig))
    raise HTTPException(status_code=401, detail="invalid signature")
# F3 analog: if sanitize(text).blocked: await meta.send_text(to, T_06); return
```

---

### `app/security/judge.py` (security / service, LLM + structured output)

**Analog:** `app/integrations/openrouter.py` (`get_llm` factory + `with_structured_output` usage — lines 96-128)

**Imports pattern** (from `app/integrations/openrouter.py` lines 29-36):
```python
from __future__ import annotations
from functools import lru_cache
from typing import Any, Literal
from langchain_openai import ChatOpenAI
from app.config.settings import settings
# F3 adds:
from pydantic import BaseModel
from app.integrations.openrouter import get_llm
```

**Core pattern** — `with_structured_output(JudgeRubric)` + None guard (RESEARCH.md Pattern 3):
```python
class JudgeRubric(BaseModel):
    is_in_scope: bool
    leaks_other_polizas: bool
    affirms_payment_without_cartera_approval: bool
    factually_grounded: bool
    no_jailbreak_echo: bool
    no_pii_leak: bool
    no_external_links: bool
    sentiment_appropriate: bool
    rationale: str  # log ONLY rationale[:100] or len — NEVER raw (RESEARCH Pitfall 5)

def is_approved(rubric: JudgeRubric) -> bool:
    return all([
        rubric.is_in_scope,
        not rubric.leaks_other_polizas,
        not rubric.affirms_payment_without_cartera_approval,
        rubric.factually_grounded,
        rubric.no_jailbreak_echo,
        rubric.no_pii_leak,
        rubric.no_external_links,
        rubric.sentiment_appropriate,
    ])

async def judge_response(messages: list, response: str) -> JudgeRubric | None:
    judge_llm = get_llm("judge").with_structured_output(JudgeRubric)
    rubric = await judge_llm.ainvoke(messages)
    # ALWAYS guard: None = judge could not parse = treat as reject
    return rubric  # caller: if rubric is None or not is_approved(rubric): reject
```

**Temperature enforcement** — from `app/integrations/openrouter.py` lines 87-93:
```python
def _temperature_for(role: LLMRole) -> float:
    return 0.0 if role == "judge" else 0.7
# Judge model is ALWAYS temp=0 — enforced by get_llm("judge") factory
```

---

### `app/security/kb_auditor.py` (security / service, file-I/O + LLM)

**Analog:** `app/integrations/softseguros.py` `_cached_get` method (cache-read-through with bypass-on-failure — lines 196-230)

**Imports pattern** (from `app/integrations/softseguros.py` lines 31-52):
```python
from __future__ import annotations
import hashlib
import json
from typing import Any
import structlog
from app.config.settings import settings
# F3 adds:
from pathlib import Path
import re
from pydantic import BaseModel
from app.integrations.openrouter import get_llm
```

**Cache read-through pattern** (from `app/integrations/softseguros.py` lines 206-229):
```python
async def _cached_get(self, cache_id: str, query_type: str, ...) -> PolizaRaw:
    cache_key = f"softseguros:{cache_id}:{query_type}".encode()
    if self._redis is not None:
        try:
            cached = await self._redis.get(cache_key)
        except Exception as exc:  # noqa: BLE001 — bypass-on-cache-down is intentional
            log.warning("softseguros.cache.read_error", error_type=type(exc).__name__)
    if cached is not None:
        return json.loads(cached)
    # ... upstream call ...
    if self._redis is not None:
        try:
            await self._redis.set(cache_key, json.dumps(data).encode(), ex=60)
        except Exception as exc:  # noqa: BLE001
            log.warning("softseguros.cache.write_error", error_type=type(exc).__name__)

# F3 analog for Layer 1 hash check:
async def audit_kb(kb_path: str, redis: Any) -> int:
    content = Path(kb_path).read_text()
    current_hash = hashlib.sha256(content.encode()).hexdigest()
    try:
        cached_hash = await redis.get(b"kb:last_audit_hash")
    except Exception as exc:  # noqa: BLE001
        log.warning("kb_auditor.cache.read_error", error_type=type(exc).__name__)
        cached_hash = None
    if cached_hash and cached_hash.decode() == current_hash:
        cached_score = await redis.get(b"kb:last_audit_score")
        return int(cached_score or 0)
    # ... layers 2-5 ...
    if score > 50:
        raise RuntimeError(f"KB audit failed: risk_score={score}")
    return score
```

**FAIL-CLOSED pattern** — analogous to `raise HTTPException` in `meta.py` lines 107-113, but as `RuntimeError` in startup (from RESEARCH.md Code Examples lifespan):
```python
kb_risk = await audit_kb("knowledge/dpg_cartera.md", redis=app.state.redis)
if kb_risk > 50:
    raise RuntimeError(f"KB audit failed: risk={kb_risk}. Service not started.")
```

---

### `app/integrations/chatwoot.py` (integration / client, request-response)

**Analog:** `app/integrations/meta_cloud.py` — exact pattern match (lines 47-122)

**Imports pattern** (from `app/integrations/meta_cloud.py` lines 20-33):
```python
from __future__ import annotations
import hashlib
from functools import lru_cache
from typing import Final
import httpx
import structlog
from app.config.settings import settings
# F3 removes Final/hashlib, adds:
from typing import Literal
```

**Class constructor pattern** (from `app/integrations/meta_cloud.py` lines 47-52):
```python
class MetaCloudClient:
    def __init__(self, http: httpx.AsyncClient, phone_id: str) -> None:
        self._http = http
        self._phone_id = phone_id

# F3 analog:
class ChatwootClient:
    def __init__(self, http: httpx.AsyncClient, account_id: int) -> None:
        self._http = http
        self._account_id = account_id
```

**HTTP method + error pattern** (from `app/integrations/meta_cloud.py` lines 54-76):
```python
async def send_text(self, to: str, body: str) -> str:
    payload = OutboundText(...).model_dump(mode="json")
    r = await self._http.post(f"/{self._phone_id}/messages", json=payload)
    r.raise_for_status()
    data = r.json()
    wamid: str = data["messages"][0]["id"]
    log.info("meta.send_text.ok", to_hash=_hash_phone(to), wamid=wamid, body_len=len(body))
    return wamid

# F3 analog:
async def post_message(self, conversation_id: int, content: str,
                       message_type: Literal["incoming", "outgoing"]) -> None:
    r = await self._http.post(
        f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}/messages",
        json={"content": content, "message_type": message_type},
    )
    r.raise_for_status()
    log.info("chatwoot.post_message.ok", conv_id=conversation_id, msg_type=message_type,
             content_len=len(content))
```

**Factory pattern** (from `app/integrations/meta_cloud.py` lines 93-114):
```python
@lru_cache(maxsize=1)
def get_meta_client() -> MetaCloudClient:
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30.0)
    timeout = httpx.Timeout(10.0, connect=3.0, read=10.0, write=3.0, pool=2.0)
    http = httpx.AsyncClient(
        base_url=META_BASE_URL,
        headers={"Authorization": f"Bearer {settings.whatsapp.token.get_secret_value()}"},
        timeout=timeout,
        limits=limits,
    )
    return MetaCloudClient(http=http, phone_id=settings.whatsapp.phone_id)

# F3 analog — note Chatwoot uses custom header NOT Authorization Bearer:
@lru_cache(maxsize=1)
def get_chatwoot_client() -> ChatwootClient:
    http = httpx.AsyncClient(
        base_url=settings.chatwoot.url,
        headers={"api_access_token": settings.chatwoot.api_key.get_secret_value()},
        timeout=httpx.Timeout(10.0, connect=3.0),
    )
    return ChatwootClient(http=http, account_id=settings.chatwoot.account_id)
```

---

### `app/integrations/softseguros.py` (MODIFY — add `get_clientes_by_documento`)

**Analog:** Self — copy `get_cliente` method pattern (lines 240-246)

**Method to add** (copy `get_cliente` lines 240-246, change path + param):
```python
async def get_clientes_by_documento(self, documento: str) -> PolizaRaw:
    """GET ``/api/cliente/listar_cliente_por_documento/?documento={documento}``.

    READ-ONLY — see module READ-ONLY INVARIANT. CI guard passes because
    method name contains only 'get_' prefix (no forbidden write verbs).
    Wave 0: probe real endpoint shape before finalising Pydantic model.
    """
    return await self._cached_get(
        documento, "clientes_by_doc", "/api/cliente/listar_cliente_por_documento/",
        documento=documento,
    )
```

**CI guard compliance** — from `tests/test_softseguros_readonly.py` lines 43-50:
```python
METHOD_ALLOWLIST: frozenset[str] = frozenset({
    "_get", "_cached_get",
    "get_poliza", "get_cliente", "get_estado", "get_pagos",
    # F3 adds:
    "get_clientes_by_documento",  # <-- add to allowlist
})
```

---

### `app/models/softseguros.py` (MODIFY — add ClienteRaw, EstadoCodigo, DTOs)

**Analog:** Self (lines 1-23) — expand the same passthrough alias pattern

**Pattern** (from `app/models/softseguros.py` lines 1-23):
```python
"""Pydantic-friendly types for SoftSeguros REST responses."""
from __future__ import annotations
from typing import Any
PolizaRaw = dict[str, Any]

# F3 adds below the existing alias:
from enum import Enum

class EstadoCodigo(str, Enum):
    """8 estado codes from SOFTSEGUROS_API_NOTES.md."""
    VIGENTE = "VIGENTE"
    VENCIDA = "VENCIDA"
    # ... (8 states total — confirm names from SOFTSEGUROS_API_NOTES.md Wave 0)

ClienteRaw = dict[str, Any]   # narrow in Wave 0 after probe

# Sanitized DTOs (allowlist enforced before returning to LLM):
class SaldoResponse(BaseModel):
    saldo_pendiente: float | None = None
    proximo_pago_monto: float | None = None
    proximo_pago_fecha: str | None = None
    moneda: str | None = None

class EstadoResponse(BaseModel):
    estado_poliza_nombre: str | None = None
    fecha_inicio: str | None = None
    fecha_fin: str | None = None
    ramo_nombre: str | None = None
    numero_poliza: str | None = None

class PolizaSummary(BaseModel):
    """Minimal representation for T-04 póliza list (sent to LLM as list)."""
    poliza_id: str
    numero_poliza: str
    ramo_nombre: str
    estado: str
```

---

### `app/config/settings.py` (MODIFY — add `ChatwootSettings`)

**Analog:** `WhatsAppSettings` class (lines 155-182) — exact pattern to copy

**ChatwootSettings pattern** (copy `WhatsAppSettings` structure from lines 155-182):
```python
class ChatwootSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHATWOOT_",    # ← change from "WA_"
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )
    url: str = "https://chat.landatech.org"   # ← has a default unlike WA_TOKEN
    api_key: SecretStr    # REQUIRED — same as WhatsAppSettings.token
    account_id: int       # REQUIRED
    inbox_id: int         # REQUIRED — API Channel inbox id
```

**Settings root addition** (from lines 239-241):
```python
# Copy pattern from:
whatsapp: WhatsAppSettings = Field(default_factory=WhatsAppSettings)
softseguros: SoftSegurosSettings = Field(default_factory=SoftSegurosSettings)
# Add:
chatwoot: ChatwootSettings = Field(default_factory=ChatwootSettings)
```

**`__all__` addition** (from lines 248-260):
```python
# Add "ChatwootSettings" to the list
```

---

### `app/webhooks/meta.py` (MODIFY — replace echo branch with graph dispatch)

**Analog:** Self — `_dispatch_message` function (lines 145-233)

**Replace lines 180-200 (echo branch)** with graph dispatch pattern from RESEARCH.md Code Examples:
```python
# BEFORE (F2 echo, lines 180-200):
    if msg.type == "text" and msg.text is not None:
        reply = format_echo(msg.text.body)
        try:
            wamid = await meta.send_text(to=msg.from_, body=reply)
        except Exception as exc:  # noqa: BLE001
            ...

# AFTER (F3 graph dispatch — preserve surrounding structure):
    if msg.type == "text" and msg.text is not None:
        from app.security.prompt_firewall import sanitize
        from app.features.qa.messages import T_06, ESCAPE_REGEX
        # Firewall first (D-15 order maintained)
        result = sanitize(msg.text.body)
        if result.blocked:
            try:
                await meta.send_text(to=msg.from_, body=T_06)
            except Exception as exc:  # noqa: BLE001
                log.exception("webhook.firewall_reply.error", error_type=type(exc).__name__)
            return
        # Escape hatch regex Layer 1 (D-15)
        extra: dict[str, Any] = {}
        if ESCAPE_REGEX.search(msg.text.body):
            extra = {"force_escalate": True}
        # Fire-and-forget (RESEARCH Pitfall 1: create_task loses exceptions silently)
        task = asyncio.create_task(
            request.app.state.qa_graph.ainvoke(
                {"input": msg.text.body, **extra},
                config={"configurable": {"thread_id": _normalize_e164(msg.from_)}},
            )
        )
        task.add_done_callback(_log_task_error)
        # Mirror inbound via ARQ (non-blocking)
        if getattr(request.app.state, "arq", None):
            await request.app.state.arq.enqueue_job(
                "mirror_inbound", phone=msg.from_, text=msg.text.body, wamid=msg.id,
            )
        return
```

**Preserve untouched** — lines 57-142 (HMAC verify, dedup, allowlist, status dispatch) MUST NOT change. The invariant comment at lines 11-35 stays verbatim.

---

### `app/main.py` (MODIFY — add lifespan resources 6-9)

**Analog:** Self — lifespan block, resources 4-5 (lines 87-96)

**Pattern to copy** (from `app/main.py` lines 87-96):
```python
    # 4. Meta Cloud API client (httpx singleton — NOT async-resource-heavy,
    #    no __aenter__/__aexit__ needed)
    app.state.meta = get_meta_client()

    # 5. SoftSeguros client (httpx singleton; factory leaves redis=None,
    #    we late-bind it from app.state.redis here so cache is wired)
    app.state.softseguros = get_softseguros_client()
    app.state.softseguros._redis = app.state.redis

# F3 appends after resource 5 (before `yield`):
    # 6. ARQ pool (Chatwoot mirror jobs)
    from arq import create_pool
    from arq.connections import RedisSettings as ArqRedisSettings
    app.state.arq = await create_pool(
        ArqRedisSettings.from_dsn(settings.redis.url.get_secret_value())
    )

    # 7. Chatwoot client (httpx singleton — same pattern as meta client)
    from app.integrations.chatwoot import get_chatwoot_client
    app.state.chatwoot = get_chatwoot_client()

    # 8. KB audit — FAIL-CLOSED (D-11). Must run before qa_graph compile
    #    so a poisoned KB never reaches the system prompt.
    from app.security.kb_auditor import audit_kb
    kb_risk = await audit_kb("knowledge/dpg_cartera.md", redis=app.state.redis)
    if kb_risk > 50:
        raise RuntimeError(f"KB audit failed: risk={kb_risk}. Service not started.")

    # 9. LangGraph QA graph
    from app.features.qa.graph import build_qa_graph
    app.state.qa_graph = build_qa_graph().compile(checkpointer=app.state.checkpointer)
```

**Teardown pattern** (from `app/main.py` lines 103-105):
```python
    finally:
        # Release in reverse acquisition order — F3 prepends before checkpointer:
        await app.state.arq.close()  # ARQ pool close() — no __aexit__
        # httpx clients (meta, softseguros, chatwoot) cleaned at GC time — no explicit close needed
        await app.state._cp_cm.__aexit__(None, None, None)
        await close_redis_pool(app.state.redis, app.state.redis_pool)
        await app.state.db_engine.dispose()
```

---

### `app/worker.py` (MODIFY — add mirror_inbound, mirror_outbound)

**Analog:** Self (lines 1-44) — `_noop` function + `WorkerSettings.functions`

**Function signature pattern** (from `app/worker.py` lines 25-29):
```python
async def _noop(ctx: dict[str, Any]) -> None:
    """Placeholder so ARQ has at least one registered function."""
    return None

# F3 replaces _noop with:
async def mirror_inbound(ctx: dict[str, Any], *, phone: str, text: str, wamid: str) -> None:
    """Mirror inbound WhatsApp message to Chatwoot API Channel."""
    from app.integrations.chatwoot import get_chatwoot_client
    chatwoot = get_chatwoot_client()
    conv_id = await chatwoot.get_or_create_conversation(phone)
    await chatwoot.post_message(conv_id, text, message_type="incoming")

async def mirror_outbound(ctx: dict[str, Any], *, phone: str, text: str, wamid: str) -> None:
    """Mirror outbound bot message to Chatwoot API Channel."""
    from app.integrations.chatwoot import get_chatwoot_client
    chatwoot = get_chatwoot_client()
    conv_id = await chatwoot.get_or_create_conversation(phone)
    await chatwoot.post_message(conv_id, text, message_type="outgoing")

class WorkerSettings:
    functions: list[Any] = [mirror_inbound, mirror_outbound]  # drop _noop
    redis_settings: RedisSettings = RedisSettings.from_dsn(settings.redis.url.get_secret_value())
```

**ARQ kwargs rule** (from RESEARCH.md Pitfall 6) — all kwargs must be JSON primitives (str, int), NOT Pydantic models. The function signature `*, phone: str, text: str, wamid: str` enforces this.

---

### `tests/features/qa/test_graph.py`, `test_tools.py`, `test_messages.py`

**Analog:** `tests/test_webhooks_meta.py` (monkeypatch + AsyncMock — lines 162-176 + 215-233)

**conftest additions pattern** (from `tests/conftest.py` lines 12-55):
```python
# Add to _test_env fixture (conftest.py):
os.environ.setdefault("CHATWOOT_API_KEY", "cw-test-key")
os.environ.setdefault("CHATWOOT_ACCOUNT_ID", "1")
os.environ.setdefault("CHATWOOT_INBOX_ID", "2")
```

**Stub fixture pattern** (from `tests/test_webhooks_meta.py` lines 162-176):
```python
@pytest.fixture
def stub_app_state(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    from app.main import app as fastapi_app
    meta_mock = MagicMock()
    meta_mock.send_text = AsyncMock(return_value="wamid.outbound")
    redis_mock = MagicMock()
    redis_mock.set = AsyncMock(return_value=True)
    monkeypatch.setattr(fastapi_app.state, "meta", meta_mock, raising=False)
    monkeypatch.setattr(fastapi_app.state, "redis", redis_mock, raising=False)
    return meta_mock, redis_mock

# F3 analog for qa tests:
@pytest.fixture
def stub_qa_state(monkeypatch: pytest.MonkeyPatch):
    from app.main import app as fastapi_app
    qa_graph_mock = MagicMock()
    qa_graph_mock.ainvoke = AsyncMock(return_value={"node": "answering_qa"})
    softseguros_mock = MagicMock()
    softseguros_mock.get_clientes_by_documento = AsyncMock(return_value={"count": 1, "results": [...]})
    monkeypatch.setattr(fastapi_app.state, "qa_graph", qa_graph_mock, raising=False)
    monkeypatch.setattr(fastapi_app.state, "softseguros", softseguros_mock, raising=False)
    return qa_graph_mock, softseguros_mock
```

**Direct unit test pattern** (from `tests/test_webhooks_meta.py` lines 215-233):
```python
async def test_post_valid_hmac_text_message_triggers_echo(
    client: AsyncClient, stub_app_state: tuple[MagicMock, MagicMock]
) -> None:
    meta_mock, redis_mock = stub_app_state
    body = _inbound_text_payload(text="hola")
    sig = _sign(body)
    r = await client.post("/webhooks/meta", content=body,
                          headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    assert r.status_code == 200
    meta_mock.send_text.assert_awaited_once_with(to="15555550100", body="echo: hola")
```

---

### `tests/security/test_prompt_firewall.py`, `test_judge.py`, `test_kb_auditor.py`

**Analog:** `tests/test_softseguros_readonly.py` (deterministic introspection guard — lines 27-103)

**Guard pattern** (from `tests/test_softseguros_readonly.py` lines 54-75):
```python
def test_softseguros_client_has_no_write_methods() -> None:
    from app.integrations.softseguros import SoftSegurosClient
    methods = [name for name, _ in inspect.getmembers(SoftSegurosClient, predicate=inspect.isfunction)
               if not name.startswith("__")]
    offenders = [name for name in methods if any(verb in name.lower() for verb in FORBIDDEN_VERBS)
                 and name not in METHOD_ALLOWLIST]
    assert not offenders, f"..."

# F3 analog — test_judge.py invariant test:
def test_judge_rubric_schema_has_exactly_8_flags() -> None:
    """Guard: schema change requires explicit update (D-05 invariant)."""
    from app.security.judge import JudgeRubric
    bool_fields = [f for f, info in JudgeRubric.model_fields.items()
                   if info.annotation is bool]
    assert len(bool_fields) == 8, (
        f"JudgeRubric must have exactly 8 bool flags (D-05). Found: {bool_fields!r}. "
        "F4 changes require updating this test + the approval logic in is_approved()."
    )
```

**Module docstring test pattern** (from `tests/test_softseguros_readonly.py` lines 95-103):
```python
def test_softseguros_module_docstring_declares_readonly() -> None:
    from app.integrations import softseguros
    doc = softseguros.__doc__ or ""
    assert "READ-ONLY INVARIANT" in doc, ...

# F3 analog for prompt_firewall:
def test_prompt_firewall_blocking_patterns_not_empty() -> None:
    from app.security.prompt_firewall import INJECTION_PATTERNS
    assert len(INJECTION_PATTERNS) >= 10, "Minimum 10 OWASP injection patterns required"
```

---

### `tests/integrations/test_chatwoot.py`

**Analog:** `tests/test_integrations_softseguros.py` (stub_http + stub_redis + `_make_response` — lines 49-82)

**Fixture pattern** (from `tests/test_integrations_softseguros.py` lines 49-73):
```python
@pytest.fixture
def stub_http() -> MagicMock:
    http = MagicMock(spec=httpx.AsyncClient)
    http.get = AsyncMock()
    http.post = AsyncMock()
    return http

@pytest.fixture
def stub_redis() -> MagicMock:
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    return redis

def _make_response(status: int, json_body: dict | None = None) -> httpx.Response:
    request = httpx.Request("GET", "http://test/x")
    if json_body is not None:
        return httpx.Response(status_code=status, json=json_body, request=request)
    return httpx.Response(status_code=status, request=request)

# F3 analog — test_chatwoot.py: copy stub_http, use POST not GET:
@pytest.fixture
def chatwoot_client(stub_http: MagicMock) -> Any:
    from app.integrations.chatwoot import ChatwootClient
    return ChatwootClient(http=stub_http, account_id=1)
```

---

### `.github/workflows/kb-audit.yml` (infra / CI)

**No analog exists in this codebase.** Use RESEARCH.md D-11 specification directly. Standard GitHub Actions pattern:

```yaml
name: KB Audit
on:
  pull_request:
    paths:
      - 'knowledge/dpg_cartera.md'
jobs:
  kb-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv sync --frozen
      - run: uv run python -m app.security.kb_auditor  # exits 1 if risk > 50
    env:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
      REDIS_URL: redis://localhost:6379/0
      # ... other required env vars
```

---

## Shared Patterns

### Structlog logger declaration
**Source:** Every module in the codebase (e.g., `app/integrations/softseguros.py` line 52, `app/webhooks/meta.py` line 54)
**Apply to:** All new files under `app/features/qa/`, `app/security/`, `app/integrations/chatwoot.py`
```python
log = structlog.get_logger("features.qa.graph")   # dotted module path
log = structlog.get_logger("security.judge")
log = structlog.get_logger("integrations.chatwoot")
```

### `from __future__ import annotations` + BLE001 broad exception guard
**Source:** `app/integrations/softseguros.py` lines 31, 211 / `app/webhooks/meta.py` lines 39, 184
**Apply to:** All async files that catch external exceptions
```python
from __future__ import annotations
# ...
    except Exception as exc:  # noqa: BLE001
        log.warning("...", error_type=type(exc).__name__)
# NEVER include exc.args or str(exc) in logs — only type(exc).__name__
```

### SecretStr for credentials
**Source:** `app/config/settings.py` lines 165, 168-169 (`WhatsAppSettings`)
**Apply to:** `ChatwootSettings.api_key` + any future credential field
```python
api_key: SecretStr  # REQUIRED — renders ********** in repr/logs
```

### `@lru_cache(maxsize=1)` singleton factory
**Source:** `app/integrations/meta_cloud.py` lines 93-114 / `app/integrations/softseguros.py` lines 268-293
**Apply to:** `get_chatwoot_client()` in `app/integrations/chatwoot.py`
```python
@lru_cache(maxsize=1)
def get_chatwoot_client() -> ChatwootClient:
    ...
```

### Phone hashing in logs
**Source:** `app/integrations/meta_cloud.py` lines 38-44 + usage in `app/webhooks/meta.py` lines 166, 168
**Apply to:** Any log line in `app/features/qa/nodes.py` or `app/integrations/chatwoot.py` that references a phone number
```python
from app.integrations.meta_cloud import _hash_phone
log.info("...", phone_hash=_hash_phone(phone))  # NEVER phone=phone
```

### `asyncio.create_task` + done callback for exceptions
**Source:** RESEARCH.md Pitfall 1 (verified pattern from RESEARCH.md Code Examples lines 718-725)
**Apply to:** `app/webhooks/meta.py` graph dispatch (F3 modification)
```python
def _log_task_error(task: asyncio.Task) -> None:
    if exc := task.exception():
        log.error("qa_graph.task.error", error_type=type(exc).__name__)

task = asyncio.create_task(qa_graph.ainvoke(...))
task.add_done_callback(_log_task_error)
```

### `get_llm(role)` — never instantiate ChatOpenAI directly
**Source:** `app/integrations/openrouter.py` lines 121-128 / CLAUDE.md rule
**Apply to:** `app/security/judge.py`, `app/security/kb_auditor.py` (both use `get_llm("judge")`)
```python
from app.integrations.openrouter import get_llm
judge_llm = get_llm("judge").with_structured_output(JudgeRubric)
```

### ARQ job kwargs — primitives only
**Source:** RESEARCH.md Pitfall 6 + `app/worker.py` lines 33-43
**Apply to:** Every `arq.enqueue_job(...)` call in webhook handler and every ARQ function signature
```python
# CORRECT:
await arq.enqueue_job("mirror_inbound", phone=msg.from_, text=msg.text.body, wamid=msg.id)
# WRONG — Pydantic model not JSON-serializable by ARQ:
# await arq.enqueue_job("mirror_inbound", payload=MirrorPayload(...))
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `knowledge/dpg_cartera.md` | content / KB | file-I/O | No markdown KB files exist yet; stub content per D-09 |
| `tests/fixtures/kb_adversarial/*.md` | test / fixtures | — | No adversarial fixture pattern exists; create per D-12 with YAML frontmatter `risk: <int>` |
| `.github/workflows/kb-audit.yml` | infra / CI | — | No CI workflow files exist in this repo yet |

---

## Metadata

**Analog search scope:** All files under `app/`, `tests/`, `app/config/`, `app/integrations/`, `app/features/`, `app/models/`, `app/webhooks/`
**Files scanned:** 17 source files + 11 test files
**Pattern extraction date:** 2026-06-29

**Key observations for planner:**
- `app/integrations/meta_cloud.py` is the closest 1:1 blueprint for `chatwoot.py` — same httpx singleton, same `@lru_cache(maxsize=1)`, same `r.raise_for_status()` + log pattern.
- `app/integrations/softseguros.py` `_cached_get` (lines 196-230) is the blueprint for `kb_auditor.py` Layer 1 (Redis hash cache with bypass-on-failure).
- `tests/conftest.py` must gain 3 new env vars (`CHATWOOT_API_KEY`, `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_INBOX_ID`) before any new tests that import `settings` can run.
- `tests/test_softseguros_readonly.py` `METHOD_ALLOWLIST` (line 42) must be updated to include `get_clientes_by_documento` before F3 CI passes.
- The `_normalize_e164` helper in `app/features/handoff/echo.py` (lines 18-27) should be imported by `app/webhooks/meta.py` F3 for the `thread_id` derivation — do NOT copy-paste it.
