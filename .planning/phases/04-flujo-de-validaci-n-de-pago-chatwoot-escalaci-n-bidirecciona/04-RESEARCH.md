# Phase 4: Flujo de validación de pago + Chatwoot escalación bidireccional — Research

**Researched:** 2026-06-29
**Domain:** WhatsApp payment flow, LangGraph interrupt, Meta media API, Chatwoot outbound webhook, ARQ scheduling
**Confidence:** HIGH (codebase directly inspected; all patterns confirmed against existing code)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Storage de comprobantes**
- D-01: Railway volume, path `/data/comprobantes/{case_id}/{timestamp}-{wamid}.{ext}`. Alerts at 70% capacity.
- D-02: 90-day retention, ARQ cron cleanup job.
- D-03: Path never exposed to client or LLM. Forward uses Meta `media_id`, not public URL.

**Validación + parseo de respuesta de cartera**
- D-04: Bot manda botones interactivos a cartera: `✅ Aprobar`, `❌ Rechazar`, `❓ Más info`. Buttons sent only on LAST attachment in the batch.
- D-05: Parsing = tap-de-botón puro (`interactive.button_reply.id`). NO texto libre, NO LLM. If text arrives, re-send buttons.
- D-06: `CARTERA_PHONE_ALLOWLIST` env var (E.164 CSV). Non-listed inbound discarded silently (log only, no reply).

**Forward a cartera + multi-comprobantes**
- D-07: Same `case_id` for all N attachments from one client batch.
- D-08: Caption format: `📎 Comprobante [{idx}/{total}] — Caso #{case_id} / Cliente: {nombre} (Doc: {doc}) / Póliza: POL-{numero} / Recibido: {timestamp_co}`. Buttons only on last file.
- D-09: New comprobante after cartera already decided → new `case_id`.

**Timeout y horarios de cartera**
- D-10: Horario laboral = L-V 8:00-12:00 + 14:00-16:00 America/Bogota (UTC-5).
- D-11: Reminder a cartera at 20 min without response (within business hours). One per case.
- D-12: Auto-escalate to Chatwoot at 90 min without response (within business hours).
- D-13: Out-of-hours: ack to client, timer starts at next business window open, not immediately.
- D-14: ARQ scheduled jobs (`reminder_cartera`, `escalate_stale_case`) polling Postgres every minute. NO asyncio.sleep, NO in-memory.

**Canal bidireccional Chatwoot → cliente**
- D-15: Opción B — Chatwoot outbound webhook → `POST /webhooks/chatwoot` → meta_cloud send. All WhatsApp credentials stay in this service.
- D-16: Redis index `chatwoot:phone_by_conv:{conv_id}` populated at conversation creation. Fallback: Chatwoot API lookup.
- D-17: Chatwoot message dedup in Redis TTL 24h (same pattern as `wa:msg:{id}`).
- D-18: Human agent attachments from Chatwoot → re-upload to Meta CDN as `media_id` → send. Types: image + PDF.

**Template Meta "no contestamos llamada"**
- D-19: Template name `voice_no_answer_followup`, category UTILITY, lang `es`.
- D-20: Body with `{{1}}` = nombre, `{{2}}` = numero_poliza.
- D-21: Quick-reply buttons: `Sí, ayúdenme` → Q&A flow; `Más tarde` → polite close.
- D-22: Template submission to Meta = out-of-band prereq. Set `META_TEMPLATE_NO_ANSWER_NAME` env var when approved.
- D-23: `POST /case/handoff/no_answer` authenticated with `LAMBDA_PROYECT_INTERNAL_TOKEN`. Payload: `{phone, cliente_nombre, numero_poliza, case_id}`.

**Attachments**
- D-24: jpeg/png/webp + PDF only.
- D-25: Max 5 MB.
- D-26: Magic-byte validation on first chunk. Mismatch → reject with client message.
- D-27: Comprobantes NEVER through LLM vision. LLM only sees `{recibido: bool, tipo, size_kb, case_id}`.

**Output firewall**
- D-28: "pago confirmado" and variants only on post-tap "aprobar" path. Output firewall blocks pattern unless `payment_approved=True` flag present in AIMessage. Patterns: `pago confirmado`, `pago aprobado`, `tu pago fue (registrado|aceptado|recibido)` + poliza number.

### Claude's Discretion
- Internal structure of `features/payment/graph.py` (subnodes, nesting with QA graph, shared checkpointer): planner decides based on this research.
- `case_id` schema (UUID v4 vs ULID), Postgres tables, migrations: planner decides.
- Concrete ARQ scheduler implementation: planner decides within D-14 constraint.
- Chatwoot webhook HMAC/auth method: research what Chatwoot supports, planner chooses.

### Deferred Ideas (OUT OF SCOPE)
- Audit log inmutable con hash chain → Phase 5.
- Test suite adversarial completa → Phase 5.
- Memoria L3/L4 cargada al recibir handoff → Phase 6.
- OCR/validación automática del comprobante → PROJECT.md out of scope.
- Dashboard LANDA para comprobantes → descartado.
- Soporte multi-tenant operativo → Phase futura.
- Antivirus dedicado para attachments → Phase 5.
- WhatsApp native inbox en Chatwoot (Opción A) → descartada.
</user_constraints>

---

## TL;DR

1. **All the hard plumbing already exists.** HMAC, dedup, Redis lock, `_post_message`, `_hash_phone`, ARQ enqueue, Chatwoot client, LangGraph checkpointer — F4 extends them, doesn't rebuild them.
2. **The cartera channel is the same Meta webhook**, just a new routing branch in `_dispatch_message` keyed by `msg.from_` against `CARTERA_PHONE_ALLOWLIST`.
3. **LangGraph `interrupt()` is the gate** between "forwarded to cartera" and "received decision". Resume via `graph.update_state()` + re-`ainvoke` with the same `thread_id`.
4. **Alembic is already wired** (`alembic/versions/0001_initial_schema.py`). F4 adds migration `0002` with `cases` + `attachments` tables using the existing `Base` + `metadata` in `app/config/db.py`.
5. **ARQ timer jobs must use Postgres, not in-memory** (D-14). Use `cron` kwarg in `WorkerSettings` for the polling loop; working-hours math lives in a pure function.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Receive comprobante from client | API (webhooks/meta.py) | — | Same webhook endpoint, new media branch |
| Download media from Meta CDN | API (integrations/meta_cloud.py) | — | Needs WA token, lives in the integration client |
| Magic-byte validation | API (features/payment/) | — | Synchronous before any storage write |
| Store to Railway volume | API (features/payment/) | — | Direct disk I/O in the worker process |
| Forward to cartera with buttons | API (integrations/meta_cloud.py) | — | Existing `send_buttons` + new `send_media` |
| Parse cartera button tap | API (webhooks/meta.py) | — | New routing branch in `_dispatch_message` |
| LangGraph interrupt/resume | API (features/payment/graph.py) | DB (checkpointer) | Graph state persisted in Postgres |
| Business-hours timer | Worker (app/worker.py ARQ cron) | DB (cases table) | D-14: no in-memory, polling Postgres |
| Chatwoot → client relay | API (webhooks/chatwoot.py) | Integration (meta_cloud) | New webhook route, delegates to meta client |
| Lambda handoff endpoint | API (features/handoff/ or webhooks/) | Integration (meta_cloud) | Auth + template send |
| Output firewall (payment_approved) | Security (security/output_firewall.py) | — | New module or extension, deterministic |

---

## Reusable Patterns from F2/F3

### 1. HMAC + dedup order (`app/webhooks/meta.py:244-284`)

The invariant is explicit in the module docstring:
```
HMAC → parse → dedup → allowlist → firewall → graph dispatch
```
F4's cartera branch slots in at the **allowlist** step. After dedup succeeds, `_dispatch_message` checks `msg.from_` against `CARTERA_PHONE_ALLOWLIST`. If it matches, it goes to a new `_handle_cartera_message` handler instead of `_handle_text_message`. The invariant order is preserved.

### 2. `_post_message` helper (`app/integrations/meta_cloud.py:97-113`)

All outbound message types share this internal method:
```python
async def _post_message(self, payload: dict[str, Any], log_event: str, **log_kwargs: Any) -> str:
    r = await self._http.post(f"/{self._phone_id}/messages", json=payload)
    r.raise_for_status()
    wamid: str = r.json()["messages"][0]["id"]
    log.info(log_event, wamid=wamid, **log_kwargs)
    return wamid
```
F4's `send_media` and `send_template` use this same method. No duplication.

### 3. Redis lock per phone (`app/integrations/chatwoot.py:106-136`)

`_acquire_lock` / `_release_lock` via `SET NX` with TTL. The same pattern should be used for any payment operation that must not run twice concurrently for the same phone (e.g., opening a new case while one is already open). Reuse the `_acquire_lock` / `_release_lock` helpers via import from `chatwoot.py` or extract to a shared util.

### 4. `_hash_phone` (`app/integrations/meta_cloud.py:50-56`)

Already imported in `chatwoot.py` with `from app.integrations.meta_cloud import _hash_phone`. F4 should do the same — never redeclare.

### 5. `WorkerSettings.functions` list (`app/worker.py:75`)

```python
functions: list[Any] = [mirror_inbound, mirror_outbound]
```
F4 appends `reminder_cartera`, `escalate_stale_case`, `cleanup_attachments_90d` to this list. Because `agent-worker` does NOT auto-deploy on git push, each plan that touches `worker.py` must note: **run `railway up --service agent-worker --ci --detach` to deploy worker changes**.

### 6. ARQ enqueue pattern (`app/webhooks/meta.py:395-401`)

```python
await app_state.arq.enqueue_job("mirror_inbound", phone=..., text=..., wamid=...)
```
F4 enqueues `process_attachment` the same way. ARQ serializes kwargs as JSON — use only primitives (str, int, float, bool). No Pydantic models in ARQ kwargs (ARQ Pitfall 6 — referenced in `worker.py` docstring).

---

## Patterns to Extend

### `MetaCloudClient` — new methods needed

**`upload_media(file_path: Path, mime_type: str) -> str`**

Uploads a local file to Meta CDN and returns the `media_id`. Uses multipart form upload:
```
POST /{phone_id}/media
Content-Type: multipart/form-data
  file: <binary>
  type: <mime_type>
  messaging_product: whatsapp
```
Returns `{"id": "<media_id>"}`. [ASSUMED] from Meta Cloud API docs structure — verify exact field name before coding.

**`download_media(media_id: str) -> bytes`**

Two-step: `GET /{media_id}` returns `{"url": "...", "mime_type": "...", "file_size": N, ...}`. Then `GET <url>` with the same bearer token downloads the binary. TTL on the download URL is short (~5 minutes). The `media_id` itself is valid for 30 days. [ASSUMED] — confirm from Meta docs.

**`send_media(to: str, media_id: str, media_type: str, caption: str | None) -> str`**

```python
# payload shape [ASSUMED]:
{
    "messaging_product": "whatsapp",
    "recipient_type": "individual",
    "to": to,
    "type": media_type,   # "image" | "document" | "video"
    media_type: {         # e.g. "image": {...}
        "id": media_id,
        "caption": caption,
    }
}
```
Delegates to `_post_message`.

**`send_template(to: str, template_name: str, lang: str, components: list[dict]) -> str`**

```python
# payload shape [ASSUMED]:
{
    "messaging_product": "whatsapp",
    "to": to,
    "type": "template",
    "template": {
        "name": template_name,
        "language": {"code": lang},
        "components": components,
    }
}
```
Delegates to `_post_message`.

### `ChatwootClient` — inverse index for bidi

When `get_or_create_conversation` creates or reuses a conversation, it must also populate `chatwoot:phone_by_conv:{conv_id}` in Redis alongside the existing `chatwoot:conv:{phone_hash}`:

```python
# after conv_id is known (line ~132 in chatwoot.py):
await self._cache_set(f"chatwoot:phone_by_conv:{conv_id}".encode(), phone_encoded, ttl=604800)
```

The Chatwoot outbound webhook handler uses this to look up the client's WhatsApp phone from a `conversation_id`. This requires `_cache_set` to accept `bytes` value (currently it encodes `int`). Slight signature generalization needed, or a separate `_cache_set_raw`.

New method: `get_phone_by_conv(conv_id: int) -> str | None` — Redis lookup with Chatwoot API fallback.

### `QAState` extensions

F4 extends `QAState` (not a new TypedDict) per `04-CONTEXT.md` code context note. New fields:

```python
# payment flow additions
case_id: str | None          # UUID v4, set when first comprobante received
attachment_count: int         # N files received in this batch
attachment_idx: int           # current forward index (1-based)
payment_status: Literal[
    "none",
    "awaiting_receipt",
    "forwarded",
    "awaiting_cartera",
    "approved",
    "rejected",
    "escalated",
] | None
cartera_message_wamid: str | None  # wamid of the last message sent to cartera
payment_approved: bool            # flag for output firewall
```

The `node` Literal must be extended with new node names.

### Webhook routing — cartera allowlist branch

In `_dispatch_message` (`app/webhooks/meta.py:413`), after the dedup check and before the existing allowlist:

```python
# New branch — check cartera allowlist FIRST, before client allowlist
normalized_from = _normalize_e164(msg.from_)
if normalized_from in _get_cartera_allowlist():
    await _handle_cartera_message(msg=msg, redis=redis, request=request)
    return

# Then existing client allowlist check
if not is_echo_allowed(msg.from_):
    ...
```

`_get_cartera_allowlist()` reads `settings.payment.cartera_phone_allowlist` (new `PaymentSettings` in `app/config/settings.py`). Cache the parsed list at module level (it's a frozenset, immutable after startup).

---

## LangGraph `interrupt()` — How It Works

**Version in use:** `langgraph==1.2.6` (confirmed in `pyproject.toml:12`).

[ASSUMED — based on LangGraph 1.x patterns, not verified against Context7 in this session due to tool constraints. Verify exact API before coding.]

### Interrupt flow

```python
from langgraph.types import interrupt

def node_awaiting_cartera(state: QAState) -> dict:
    # This node suspends execution here.
    # The value passed to interrupt() is stored in the checkpoint
    # and returned to the caller of graph.ainvoke() as an Interrupt exception.
    cartera_decision = interrupt({"waiting_for": "cartera_tap"})
    # Execution resumes HERE after graph.update_state() + re-ainvoke
    return {"payment_status": cartera_decision["decision"]}
```

When `node_awaiting_cartera` is reached, `ainvoke` raises `GraphInterrupt` (or returns a special result depending on LangGraph version) and the checkpoint is saved with `interrupted=True`.

### Resume from external trigger (cartera tap)

```python
# In the cartera webhook handler:
config = {"configurable": {"thread_id": thread_id}}

# 1. Inject the cartera's decision into the checkpoint
await app_state.qa_graph.aupdate_state(
    config,
    values={"payment_status": "approved"},  # or "rejected" / "info_requested"
    as_node="node_awaiting_cartera",
)

# 2. Resume — pass None as input; LangGraph resumes from interrupt point
await app_state.qa_graph.ainvoke(None, config=config)
```

`thread_id` correlation: F4 uses the client's E.164 phone as `thread_id` (same as Q&A graph). The case_id stored in state provides the Postgres row reference. The cartera webhook must extract the `case_id` from `interactive.button_reply.id` suffix or from the cartera message caption.

**Better approach for cartera tap ID:** encode `case_id` into the button `id` field:
```python
buttons = [
    ("aprobar|{case_id}", "✅ Aprobar"),
    ("rechazar|{case_id}", "❌ Rechazar"),
    ("info|{case_id}",    "❓ Más info"),
]
```
The cartera tap returns `interactive.button_reply.id = "aprobar|abc-123"`. The handler splits on `|` to get both the action and the `case_id`, then looks up `wa_phone` from Postgres `cases.phone`.

### Missing checkpoint recovery

If the graph crashes mid-interrupt (Railway restart, OOM), the checkpoint persists in Postgres. On the next cartera tap for the same `case_id`, the handler attempts `aupdate_state` + `ainvoke`. If the thread_id is found, resumption proceeds. If the checkpoint row is missing or corrupted, the handler falls through to Chatwoot escalation as a safe default.

---

## Meta Media + Template API

[ASSUMED from Meta Cloud API public docs structure — verify field names against official docs before coding.]

### Upload media

```
POST https://graph.facebook.com/v21.0/{phone_number_id}/media
Authorization: Bearer {token}
Content-Type: multipart/form-data

messaging_product=whatsapp
file=@/path/to/file;type=image/jpeg
type=image/jpeg
```

Response: `{"id": "<media_id>"}`.

**File size limits (Meta hard limits):**
- Image (jpeg/png/webp): 5 MB (D-25 matches this; Meta allows up to 5 MB for images)
- Document (PDF): up to 100 MB (D-25 caps at 5 MB for our purposes)

**MIME type must match file content** — Meta validates this. The magic-byte check (D-26) prevents spoofed MIME types from reaching Meta's upload endpoint.

### Download media (receive comprobante)

When Meta sends an inbound media webhook, `msg.image.id` or `msg.document.id` contains a `media_id`. Download flow:

```
GET https://graph.facebook.com/v21.0/{media_id}
Authorization: Bearer {token}
→ { "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=...", "mime_type": "image/jpeg", "sha256": "...", "file_size": N, "id": "..." }

GET <url>
Authorization: Bearer {token}
→ binary file content
```

The `url` from step 1 is short-lived (~5 minutes). The `media_id` itself is valid for **30 days** before Meta purges it from their CDN. This means cartera can retrieve the original via `media_id` for 30 days; after that only our Railway volume copy remains.

### Template message shape

```python
{
    "messaging_product": "whatsapp",
    "to": "+573001234567",
    "type": "template",
    "template": {
        "name": "voice_no_answer_followup",
        "language": {"code": "es"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": cliente_nombre},
                    {"type": "text", "text": numero_poliza},
                ]
            },
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": "0",
                "parameters": [{"type": "payload", "payload": "si_ayudenme"}]
            },
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": "1",
                "parameters": [{"type": "payload", "payload": "mas_tarde"}]
            }
        ]
    }
}
```

Template quick-reply taps arrive as `interactive.button_reply.id` with the `payload` value. The button routing in the inbound handler already handles this type via the existing `msg.interactive.selected_id()` path at `webhooks/meta.py:457-469`.

---

## Chatwoot Outbound Webhook

### Event payload shape

When an agent sends a message in Chatwoot, it POSTs to all configured webhook URLs:

```json
{
  "event": "message_created",
  "id": 12345,
  "content": "Hola, te ayudo con tu caso.",
  "message_type": "outgoing",
  "content_type": "text",
  "created_at": 1719619200,
  "conversation": {
    "id": 789,
    "status": "open"
  },
  "sender": {
    "id": 42,
    "name": "Agente Juan",
    "type": "user"
  },
  "attachments": []
}
```

**Filter criteria** for F4 handler:
- `event == "message_created"`
- `message_type == "outgoing"`
- `sender.type == "user"` (agent, not bot — bots have `sender.type == "agent_bot"`)

Bot mirror messages sent via `post_message("outgoing")` have `sender.type == "agent_bot"`. Without this filter, F4 would relay bot messages back through Meta creating an infinite loop.

### Chatwoot webhook authentication

Chatwoot self-hosted supports **HMAC signature** on outbound webhooks. The signature header is `X-Chatwoot-Signature` containing HMAC-SHA256 of the raw body using a shared secret configured in Chatwoot Settings → Integrations → Webhooks.

[ASSUMED] — Chatwoot docs confirm HMAC support for outbound webhooks. The shared secret is set by the operator in the Chatwoot admin panel. Store as `CHATWOOT_WEBHOOK_SECRET` env var (new `SecretStr` in `ChatwootSettings`).

Verification follows the same pattern as Meta HMAC in `webhooks/meta.py:77-85`:
```python
def _verify_chatwoot_signature(raw_body: bytes, header_value: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value)
```

**Chatwoot version note:** The self-hosted Chatwoot version is not pinned in this repo. Webhook payload shape is stable across Chatwoot v3.x. [ASSUMED]

---

## ARQ Scheduled Jobs

**Version in use:** `arq==0.28.0` (confirmed in `pyproject.toml:14`).

### Cron jobs in ARQ 0.28

ARQ supports cron jobs via `cron` objects in `WorkerSettings.cron_jobs`:

```python
from arq import cron

class WorkerSettings:
    functions = [mirror_inbound, mirror_outbound, reminder_cartera, escalate_stale_case, cleanup_attachments_90d]
    cron_jobs = [
        cron(check_pending_cases, minute={0, 1, 2, ..., 59}),  # every minute
        cron(cleanup_attachments_90d, hour=2, minute=0),        # daily at 2am
    ]
```

[ASSUMED] — ARQ 0.28 cron syntax. Verify `arq.cron` import and exact signature. The alternative is a single polling loop function that checks due cases and dispatches reminder/escalate logic internally.

**Recommended approach (simpler):** One `check_pending_cases` cron job every minute. It queries Postgres for cases where:
- `status = 'awaiting_cartera'`
- `work_hours_due_at <= NOW()`

For each due case: check if reminder already sent → send reminder or escalate based on elapsed business minutes.

### Idempotency for timer jobs

Each case row carries `reminder_sent_at` and `escalated_at` nullable timestamps. The cron job reads these before acting:
```python
if case.escalated_at is not None:
    continue  # already handled
if case.reminder_sent_at is None and business_minutes_elapsed >= 20:
    await send_reminder(case)
    await mark_reminder_sent(case.case_id)
elif business_minutes_elapsed >= 90:
    await escalate_to_chatwoot(case)
    await mark_escalated(case.case_id)
```

This is idempotent — if the cron fires twice in the same minute (Railway restart), the DB check prevents double-action.

### Delayed enqueue (defer_by)

ARQ supports `defer_by=timedelta(minutes=20)` for one-shot delayed jobs. However, given the business-hours complexity (off-hours don't count), a cron polling approach is simpler and correct. Defer-by would miscalculate if the 20-minute window spans off-hours. Use polling, not defer.

---

## Postgres Schema

### Migration approach

Alembic is **already configured** (`alembic==1.18.5` in `pyproject.toml:20`, `alembic/env.py`, `alembic/versions/0001_initial_schema.py`). F4 adds migration `0002_payment_tables.py`.

The `alembic/env.py:28` comment explicitly documents the pattern:
```python
# NOTE for future phases: import application models here so their tables
# register on ``app.config.db.Base.metadata`` before alembic captures it.
# Example (uncomment when models exist):
#     from app.memory import case_store  # noqa: F401
```

F4 follows this pattern: define SQLAlchemy models in `app/memory/case_store.py`, import them in `alembic/env.py`, run `alembic revision --autogenerate -m "payment_tables"`.

### `cases` table

```sql
CREATE TABLE cases (
    case_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone       TEXT NOT NULL,          -- E.164 client phone, not stored as PII warning
    poliza_id   TEXT,                   -- may be NULL if handoff arrives before Q&A
    cliente_doc TEXT,                   -- document provided by client
    status      TEXT NOT NULL DEFAULT 'awaiting_receipt',
    -- status enum values: awaiting_receipt | forwarded | awaiting_cartera
    --                     approved | rejected | escalated | closed
    attachment_count    INT NOT NULL DEFAULT 0,
    reminder_sent_at    TIMESTAMPTZ,
    escalated_at        TIMESTAMPTZ,
    work_hours_due_at   TIMESTAMPTZ,    -- next business-hours deadline (computed on insert/update)
    cartera_message_wamid TEXT,         -- wamid of last message sent to cartera
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_cases_phone ON cases (phone);
CREATE INDEX ix_cases_status ON cases (status) WHERE status NOT IN ('approved', 'rejected', 'closed');
CREATE INDEX ix_cases_work_hours_due_at ON cases (work_hours_due_at) WHERE status = 'awaiting_cartera';
```

The partial indexes on `status` keep the timer cron query fast without scanning terminal rows.

### `attachments` table

```sql
CREATE TABLE attachments (
    id          BIGSERIAL PRIMARY KEY,
    case_id     UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    path        TEXT NOT NULL,          -- /data/comprobantes/{case_id}/{ts}-{wamid}.{ext}
    mime_type   TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    size_bytes  INT NOT NULL,
    meta_media_id TEXT,                 -- original Meta media_id (valid 30 days)
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_attachments_case_id ON attachments (case_id);
```

**SQLAlchemy model location:** `app/memory/case_store.py` (matches CLAUDE.md `memory/` for L3 cases).

**`case_id` choice:** UUID v4 (`gen_random_uuid()` in Postgres, `uuid4()` in Python). Simpler than ULID, already used in the project (CLAUDE.md references UUID v4 throughout).

---

## Working Hours Algorithm

```python
from datetime import datetime, timedelta
import zoneinfo

TZ_CO = zoneinfo.ZoneInfo("America/Bogota")

# Business schedule: L-V, 8-12 + 14-16
WORKDAY_BLOCKS = [
    (8, 0, 12, 0),   # (start_hour, start_min, end_hour, end_min)
    (14, 0, 16, 0),
]

def is_business_time(dt_co: datetime) -> bool:
    """True if dt_co falls within a work block on a weekday."""
    if dt_co.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    for sh, sm, eh, em in WORKDAY_BLOCKS:
        block_start = dt_co.replace(hour=sh, minute=sm, second=0, microsecond=0)
        block_end   = dt_co.replace(hour=eh, minute=em, second=0, microsecond=0)
        if block_start <= dt_co < block_end:
            return True
    return False

def next_business_window_after(dt_utc: datetime) -> datetime:
    """Return the UTC datetime when the next business block opens.

    If dt_utc is already in a business block, return dt_utc unchanged.
    """
    dt_co = dt_utc.astimezone(TZ_CO)
    if is_business_time(dt_co):
        return dt_utc  # already in window

    # Advance to next block start
    candidate = dt_co
    for _ in range(14):  # max 2 weeks to avoid infinite loop
        # Try each block on candidate's day
        for sh, sm, _, _ in WORKDAY_BLOCKS:
            block_start = candidate.replace(hour=sh, minute=sm, second=0, microsecond=0)
            if block_start > candidate and candidate.weekday() < 5:
                return block_start.astimezone(datetime.timezone.utc)
        # No block found today, try next day at midnight then loop
        candidate = (candidate + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    raise RuntimeError("Could not find next business window in 14 days")
```

**Edge cases:**
- Message arrives Friday 15:59 → next window = Monday 08:00 (skips weekend)
- Message arrives during lunch (12:00-14:00) → next window = same day 14:00
- `work_hours_due_at` stored in DB for 20-min and 90-min deadlines = `next_business_window_after(now_utc) + timedelta(minutes=20)` and `+timedelta(minutes=90)`. This correctly handles the "timer only ticks during business hours" requirement because the deadline is always anchored to a business-hours start.

**Module location:** `app/features/payment/business_hours.py` (pure function, no deps, easily unit-tested).

---

## Output Firewall Extension

### Module decision

`app/security/output_firewall.py` — **new module** (doesn't exist yet, checked via `Glob`). The existing `security/` directory has `prompt_firewall.py`, `kb_auditor.py`, `judge.py`. Output firewall is a natural peer.

### Pattern logic

```python
import re

_PAYMENT_CONFIRMED_RE = re.compile(
    r"pago\s+(confirmado|aprobado)|"
    r"tu\s+pago\s+fue\s+(registrado|aceptado|recibido)",
    re.IGNORECASE,
)

def check_outbound(text: str, payment_approved: bool) -> tuple[bool, str | None]:
    """Return (allowed, reason).

    allowed=True  → message may be sent.
    allowed=False → block, escalate. reason contains the matched pattern.
    """
    match = _PAYMENT_CONFIRMED_RE.search(text)
    if match and not payment_approved:
        return False, f"payment_confirmation_without_approval: '{match.group()}'"
    return True, None
```

### Integration point

In `webhooks/meta.py:_run_and_dispatch`, after `_extract_outbound_message`, before `_send_outbound`:

```python
from app.security.output_firewall import check_outbound

if outbound_msg is not None:
    text = str(outbound_msg.content) if outbound_msg.content else ""
    payment_approved = outbound_msg.additional_kwargs.get("payment_approved", False)
    allowed, reason = check_outbound(text, payment_approved)
    if not allowed:
        log.error("output_firewall.payment_blocked", reason=reason, ...)
        # escalate instead of send
        return
    await _send_outbound(app_state, phone, outbound_msg, wamid)
```

The `payment_approved=True` flag must be set by the `node_confirming` payment node on the AIMessage:

```python
from langchain_core.messages import AIMessage

msg = AIMessage(
    content="Tu pago fue confirmado. ✅ ...",
    additional_kwargs={"payment_approved": True}
)
```

---

## Webhook Routing Extension

### Precedence in `_dispatch_message`

The cartera allowlist check must come **before** the client allowlist check. Current order in `webhooks/meta.py:413-504`:

```
dedup → allowlist (is_echo_allowed) → firewall → dispatch
```

Extended order for F4:
```
dedup
  → cartera allowlist? → _handle_cartera_message (returns)
  → client allowlist (is_echo_allowed)?
      → text? → _handle_text_message
      → interactive? → _handle_text_message (selected_id)
      → media? → _handle_comprobante (NEW — replaces send_media_ack for image/document)
      → other → log + skip
```

### Media branch changes

Currently `app/webhooks/meta.py:471-491` sends a generic `send_media_ack` for all media types. F4 replaces this for `image` and `document` types when payment flow is active:

```python
if msg.type in {"image", "document"}:
    # Check if payment flow is active for this thread
    thread_id = _normalize_e164(msg.from_)
    if await _is_payment_active(app_state, thread_id):
        await _handle_comprobante(msg=msg, ..., request=request)
    else:
        # Fall back to existing echo-ack (Q&A flow doesn't handle media)
        await meta.send_media_ack(to=msg.from_, media_type=msg.type)
    return
```

`_is_payment_active` peeks at the checkpointer for `payment_status in ("awaiting_receipt", "forwarded", "awaiting_cartera")`.

### Cartera message handler

```python
async def _handle_cartera_message(*, msg: InboundMessage, redis: Any, request: Request) -> None:
    """Route inbound from cartera: button tap → resume interrupt; text → re-send buttons."""
    if msg.type == "interactive" and msg.interactive is not None:
        selected = msg.interactive.selected_id()  # e.g. "aprobar|case-abc"
        if selected and "|" in selected:
            action, case_id = selected.split("|", 1)
            await _resume_payment_interrupt(action=action, case_id=case_id, request=request)
            return
    # Text or unrecognized → re-send buttons
    # Look up case_id from cartera's last message context (stored in cases table)
    ...
```

---

## Lambda Handoff Contract

### Endpoint: `POST /case/handoff/no_answer`

**Auth:** `Authorization: Bearer {LAMBDA_PROYECT_INTERNAL_TOKEN}` — validated against `settings.lambda_proyect.internal_token` (new `LambdaProyectSettings` or field in existing settings).

**Payload (Pydantic model):**
```python
class NoAnswerHandoff(BaseModel):
    phone: str              # E.164 client phone
    cliente_nombre: str     # First name for template {{1}}
    numero_poliza: str      # Policy number for template {{2}}
    case_id: str            # UUID v4, created by lambda-proyect
```

**Idempotency:** `case_id` is the idempotency key. On duplicate request with same `case_id`, return 200 without re-sending template (check `cases` table for existing row).

**Handler flow:**
1. Validate Bearer token
2. Upsert `cases` row (`case_id` as PK, `status='awaiting_receipt'`)
3. Send template via `meta.send_template(phone, settings.payment.template_no_answer_name, "es", components)`
4. Return `{"case_id": case_id, "sent": true}`

**Error response:** 401 on bad token, 422 on validation error, 200 always on duplicate (idempotent).

### New settings fields needed

```python
class PaymentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAYMENT_", ...)
    cartera_phone_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)
    template_no_answer_name: str = "voice_no_answer_followup"
    volume_path: Path = Path("/data/comprobantes")

class LambdaProyectSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LAMBDA_PROYECT_", ...)
    base_url: str = "http://localhost:8001"
    internal_token: SecretStr  # REQUIRED
```

Both added to `Settings` composite in `app/config/settings.py`.

---

## Failure Modes

| Failure | Impact | Mitigation |
|---------|--------|------------|
| Railway volume full | `upload_media` write fails, comprobante lost | Alert at 70% (D-01). Handler returns error to client: "No podemos recibir tu archivo en este momento." Log + Sentry. |
| Meta media 401 on download | Can't retrieve comprobante from CDN | Retry 3x with tenacity (same pattern as SoftSeguros). After 3 failures, escalate to Chatwoot: "Recibimos tu comprobante pero hubo un error técnico." |
| Chatwoot down at escalation | `escalate_to_chatwoot` fails | Don't fail silently. Log Sentry alert. Retry via ARQ job with exponential backoff. Client gets "Un agente te contactará pronto." |
| Double-tap from cartera (button tapped twice) | `_resume_payment_interrupt` called twice | Idempotency: check `cases.status` before updating. If already `approved/rejected`, log and return 200 no-op. |
| Client sends new comprobante after decision | D-09: new case_id | Detect by checking current case status. If `status in ('approved', 'rejected', 'escalated')`, create new case. |
| Crash mid-interrupt (Railway restart) | Graph stuck at `awaiting_cartera` | Checkpoint persists in Postgres. Next cartera tap triggers `aupdate_state + ainvoke` successfully. If checkpoint corrupted, timer job at 90min triggers Chatwoot escalation. |
| Cartera sends text instead of button tap | D-05: re-send buttons | `_handle_cartera_message` checks `msg.type`. If not `interactive`, re-send the 3 buttons with a note: "Tocá una opción para continuar 👆". |
| `work_hours_due_at` set to past (time-zone bug) | Timer fires immediately | `next_business_window_after` tested with DST transitions. Colombia doesn't observe DST (fixed UTC-5), so no DST edge case. |
| Lambda sends duplicate `no_answer` handoff | Double template send | Idempotency check on `case_id` in `cases` table before sending template. |

---

## Plan Slicing

Proposed 8 plans across 5 waves. Dependencies create a clear DAG.

### 04-01: Schema + Settings + Payment State skeletons (Wave 1)

**What:** Alembic migration `0002_payment_tables.py` with `cases` + `attachments`. SQLAlchemy models in `app/memory/case_store.py`. `PaymentSettings` + `LambdaProyectSettings` in settings. `QAState` extended with payment fields. `app/features/payment/__init__.py` already exists. New files: `app/features/payment/business_hours.py`, `app/security/output_firewall.py` (stub).

**Deps:** None (schema-only, no runtime changes).

**Deploy note:** `alembic upgrade head` must run before any F4 code; migrations are additive and non-destructive.

---

### 04-02: `MetaCloudClient` — `upload_media`, `download_media`, `send_media`, `send_template` (Wave 2)

**What:** 4 new methods on `MetaCloudClient` in `app/integrations/meta_cloud.py`. Unit tests with httpx mock. Magic-byte validation helper in `app/features/payment/attachment.py`.

**Deps:** 04-01 (for `PaymentSettings.volume_path`).

---

### 04-03: Chatwoot — inverse index + outbound webhook handler (Wave 2)

**What:** Populate `chatwoot:phone_by_conv:{conv_id}` in `get_or_create_conversation`. New `get_phone_by_conv` method. New FastAPI router `app/webhooks/chatwoot.py` with `POST /webhooks/chatwoot`: HMAC verify, filter `message_created + outgoing + sender.type=user`, dedup, send via `meta_cloud`. Add `CHATWOOT_WEBHOOK_SECRET` to `ChatwootSettings`.

**Deps:** 04-01 (settings). Parallel with 04-02.

**Deploy note:** After this plan, operator must configure the webhook URL in Chatwoot admin and set `CHATWOOT_WEBHOOK_SECRET`.

---

### 04-04: Payment graph + `interrupt()` + comprobante handling (Wave 3)

**What:** `app/features/payment/graph.py` — nodes: `node_receive_comprobante`, `node_forward_to_cartera`, `node_awaiting_cartera` (interrupt here), `node_confirming`, `node_payment_escalate`. Extend `_route_entry` in QA graph or create a conditional entry that routes to payment subgraph when `payment_status` is set. Webhook `_handle_comprobante` branch in `meta.py`. Forward to cartera via `send_media` + `send_buttons` (on last attachment). ARQ job `process_attachment` for async download + store + forward.

**Deps:** 04-01, 04-02.

---

### 04-05: Cartera webhook routing + tap parsing + interrupt resume (Wave 3)

**What:** `_handle_cartera_message` in `meta.py`. `CARTERA_PHONE_ALLOWLIST` routing branch (before client allowlist). `_resume_payment_interrupt` that calls `aupdate_state + ainvoke`. Output firewall integration in `_run_and_dispatch`. `node_confirming` sets `payment_approved=True` in AIMessage.

**Deps:** 04-02 (buttons need media_id pattern), 04-04 (interrupt point must exist).

---

### 04-06: ARQ schedulers + working hours (Wave 4)

**What:** `reminder_cartera`, `escalate_stale_case`, `cleanup_attachments_90d` functions in `app/worker.py`. `cron_jobs` in `WorkerSettings`. `check_pending_cases` cron polling Postgres. Business-hours logic from `app/features/payment/business_hours.py`. Update `work_hours_due_at` on case creation.

**Deps:** 04-04 (cases table being written by payment flow).

**Deploy note:** `railway up --service agent-worker --ci --detach` required after this plan ships.

---

### 04-07: Lambda handoff endpoint + template send (Wave 4)

**What:** `POST /case/handoff/no_answer` FastAPI route in `app/webhooks/handoff.py` (or `app/features/handoff/`). Bearer token auth middleware. `send_template` integration. Register route in `app/main.py`. New `LambdaProyectSettings` live.

**Deps:** 04-02 (`send_template`), 04-01 (cases table for idempotency).

**Prerequisite (out-of-band):** Template `voice_no_answer_followup` must be submitted to and approved by Meta before this endpoint is used in production.

---

### 04-08: Output firewall + E2E integration tests (Wave 5)

**What:** Complete `app/security/output_firewall.py` with regex patterns + `check_outbound`. Wire into `_run_and_dispatch`. Integration tests: happy path (cartera approves → client gets confirmation), rejection path (cartera rejects → Chatwoot), spoofed cartera number (discarded), output firewall blocks (escalates). Worker smoke: timer cron fires, reminder sent.

**Deps:** 04-04, 04-05, 04-06, 04-07.

---

## Open Questions for Planner

1. **Payment graph: extension vs subgraph.**
   The QA graph (`build_qa_graph`) uses `set_conditional_entry_point` to skip identification when `poliza_id` is set. The payment flow needs to activate when the client sends media. Two options:
   - **Extend QA graph** with `payment_*` nodes and a new entry route condition (`payment_status is not None → node_receive_comprobante`). Simpler, one checkpointer.
   - **Separate payment graph** compiled with same checkpointer but different `thread_id` prefix. More isolated but requires handoff of `poliza_id` between graphs.
   Recommendation: extend QA graph. One thread per phone, one graph, minimal coordination complexity.

2. **`cron` availability in ARQ 0.28.**
   The `cron` function is an ARQ 0.28 feature per its changelog, but the exact import path and `WorkerSettings.cron_jobs` attribute need verification against ARQ source before coding. Alternative: a polling function enqueued by itself at the end of each run (`await arq.enqueue_job("check_pending_cases", _defer_by=timedelta(minutes=1))`). This is the safe fallback if `cron_jobs` doesn't exist in 0.28.

3. **`attachment_count` race for multi-file batches.**
   Client sends 3 photos rapidly. Three `_handle_comprobante` calls arrive nearly simultaneously. How to determine `total` for the caption `[1/3]`? WhatsApp doesn't send batch metadata. Options:
   - Use a Redis counter per `thread_id` with a 5-second window to accumulate files, then forward.
   - Forward each file immediately with `[1/?]` caption, then edit the last message with total count. (Meta doesn't support message edits.)
   - Simplest: always use `[{idx}/unknown]` in caption; only send buttons after a 3-second silence (Redis timer). Planner decides UX tradeoff.

4. **`_is_payment_active` peeks at checkpointer.**
   The checkpointer `aget` call in `_dispatch_message` adds latency. Since all inbound messages already go through the checkpoint for Q&A, this is acceptable. But if it's a problem, alternative: a Redis key `payment:active:{phone_hash}` set when payment flow starts, cleared when it ends.

5. **Chatwoot message mirroring for payment conversation.**
   Payment messages between bot and client should also appear in Chatwoot (same `mirror_inbound`/`mirror_outbound` jobs). No change needed — the existing ARQ enqueue in `_handle_text_message` and `_send_outbound` already handles this. Confirm: does the cartera forward message (bot→cartera) need to appear in Chatwoot? Probably yes, as "note" for transparency. Planner decides.

6. **`python-magic` for magic-byte check (D-26).**
   `python-magic` is not in `pyproject.toml`. Options:
   - Add `python-magic` + `libmagic` system dependency (requires system lib on Railway).
   - Use manual magic-byte check (JPEG: `FF D8 FF`, PNG: `89 50 4E 47`, WebP: `52 49 46 46`, PDF: `25 50 44 46`). Simpler, no system dep, covers D-24's four allowed types exactly.
   Recommendation: manual magic-byte dict. 4 patterns, ~10 lines, no new dependency. [ASSUMED safer]

---

## Environment Availability

| Dependency | Required By | Available | Version | Notes |
|------------|------------|-----------|---------|-------|
| Railway volume `/data/comprobantes` | D-01 attachment storage | Provisioned out-of-band | — | Operator must provision volume and mount before 04-04 ships |
| `America/Bogota` timezone data | Business hours | ✓ (stdlib `zoneinfo`) | Python 3.12 | No tzdata package needed — Colombia UTC-5, no DST |
| `alembic` | Migration 0002 | ✓ | 1.18.5 | Already in pyproject.toml |
| Meta template `voice_no_answer_followup` | 04-07 | Out-of-band prereq | — | Operator submits to Meta for approval; blocks 04-07 prod use |
| `CARTERA_PHONE_ALLOWLIST` env var | 04-05 | — | — | Must be set in Railway env before 04-05 deploys |
| `CHATWOOT_WEBHOOK_SECRET` env var | 04-03 | — | — | Set in Railway + Chatwoot admin simultaneously |

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.1.1 + pytest-asyncio 1.4.0 |
| Config | `pyproject.toml [tool.pytest.ini_options]`, `asyncio_mode = "auto"` |
| Quick run | `uv run pytest tests/ -q -x` |
| Full suite | `uv run pytest` |

### Phase Requirements → Test Map

| Req | Behavior | Test Type | Command |
|-----|----------|-----------|---------|
| D-01/02 | Comprobante stored to volume | unit | `pytest app/features/payment/tests/test_attachment.py -x` |
| D-05 | Cartera tap parsed correctly | unit | `pytest app/features/payment/tests/test_cartera_handler.py -x` |
| D-06 | Non-allowlisted cartera number discarded | unit | `pytest app/webhooks/tests/test_meta_cartera_routing.py -x` |
| D-10/11/12 | Business hours + timer logic | unit | `pytest app/features/payment/tests/test_business_hours.py -x` |
| D-26 | Magic-byte validation | unit | `pytest app/features/payment/tests/test_attachment.py::test_magic_bytes -x` |
| D-27 | Comprobante never reaches LLM | unit | mock graph, assert no vision call |
| D-28 | Output firewall blocks payment confirm | unit | `pytest app/security/tests/test_output_firewall.py -x` |
| E2E | Full payment happy path | integration | manual smoke + `pytest tests/integration/ -m payment` |

### Wave 0 Gaps
- [ ] `app/features/payment/tests/test_attachment.py` — magic-byte, size limit, storage
- [ ] `app/features/payment/tests/test_business_hours.py` — edge cases including lunch, Friday→Monday
- [ ] `app/features/payment/tests/test_cartera_handler.py` — tap parsing, resume interrupt mock
- [ ] `app/security/tests/test_output_firewall.py` — pattern matching with/without flag
- [ ] `app/webhooks/tests/test_chatwoot_webhook.py` — HMAC, agent vs bot filter, dedup

---

## Security Domain

| ASVS Category | Applies | Control |
|---------------|---------|---------|
| V2 Authentication | yes | Bearer token for `/case/handoff/no_answer`; HMAC for Meta + Chatwoot webhooks |
| V3 Session Management | yes | LangGraph thread_id = E.164 phone; interrupt state in Postgres checkpointer |
| V4 Access Control | yes | `CARTERA_PHONE_ALLOWLIST` — source-based access; `payment_approved` flag |
| V5 Input Validation | yes | Pydantic v2 on all endpoints; magic-byte check on attachments; D-26 |
| V6 Cryptography | no | No new crypto. HMAC reuses existing stdlib hmac pattern |

### Known Threat Patterns

| Pattern | STRIDE | Mitigation |
|---------|--------|------------|
| Spoofed cartera number | Spoofing | `CARTERA_PHONE_ALLOWLIST` check before routing (D-06) |
| Prompt injection via comprobante caption | Tampering | D-27: LLM never sees attachment content, only metadata |
| Double-confirm via replayed webhook | Repudiation | Dedup by `message_id` in Redis (existing pattern) + idempotency on `cases.status` |
| "Pago confirmado" hallucination by LLM | Elevation of privilege | Output firewall + `payment_approved` flag (D-28) |
| Infinite Chatwoot→Meta→Chatwoot loop | DoS | Filter `sender.type == "user"` (not `agent_bot`) in Chatwoot webhook |
| Large file attack via comprobante | DoS | D-25: 5 MB limit enforced before download completes (check `file_size` from Meta media metadata) |

---

## Sources

### PRIMARY (HIGH confidence — code verified in session)
- `app/integrations/meta_cloud.py` — `_post_message`, `send_buttons`, `_hash_phone`, `get_meta_client` patterns
- `app/integrations/chatwoot.py` — Redis lock, `get_or_create_conversation`, `_cache_set`/`_cache_get`
- `app/webhooks/meta.py` — full HMAC+dedup+allowlist+dispatch chain, `_run_and_dispatch`, interactive routing
- `app/features/qa/graph.py` — `build_qa_graph`, `_route_entry`, `StateGraph` patterns
- `app/features/qa/state.py` — `QAState` TypedDict fields
- `app/security/judge.py` — `affirms_payment_without_cartera_approval` flag, `JudgeRubric`
- `app/worker.py` — `WorkerSettings.functions`, ARQ enqueue patterns
- `pyproject.toml` — exact versions: langgraph 1.2.6, arq 0.28.0, alembic 1.18.5
- `alembic/versions/0001_initial_schema.py` — migration pattern to follow for 0002
- `alembic/env.py` — model import pattern for autogenerate
- `app/config/db.py` — `Base`, `metadata`, `session_scope` patterns
- `app/config/settings.py` — `BaseSettings` + `NoDecode` + `SecretStr` patterns

### ASSUMED (verify before coding)
- LangGraph 1.2.6 `interrupt()` / `aupdate_state` exact API signatures — verify against LangGraph docs
- Meta Cloud API multipart upload field names (`messaging_product`, `type`) — verify against developer.facebook.com
- Meta media download two-step URL flow — verify TTL (~5 min on URL, 30 days on media_id)
- ARQ 0.28 `cron` function import path and `WorkerSettings.cron_jobs` attribute
- Chatwoot outbound webhook `X-Chatwoot-Signature` header name and HMAC format
- Template message `components` array structure for UTILITY category with quick_reply buttons

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | LangGraph 1.2.6 uses `interrupt()` + `aupdate_state` + re-`ainvoke(None)` pattern | LangGraph interrupt() | Wrong API → interrupt doesn't work; need to verify exact 1.2.x docs |
| A2 | Meta media download URL TTL is ~5 minutes | Meta Media + Template | If TTL is shorter, must download immediately in same request context |
| A3 | Meta media_id valid for 30 days | Meta Media + Template | If shorter, Railway volume becomes the only copy sooner |
| A4 | ARQ 0.28 has `cron_jobs` attribute in `WorkerSettings` | ARQ Scheduled Jobs | If absent, use self-re-enqueue pattern instead |
| A5 | Chatwoot outbound webhook header is `X-Chatwoot-Signature` | Chatwoot Outbound Webhook | Different header name → HMAC check fails; falls back to no-auth (security gap) |
| A6 | Chatwoot `sender.type == "agent_bot"` for bot-sent messages | Chatwoot Outbound Webhook | Wrong value → bot messages relay back to client (infinite loop) |
| A7 | Manual magic-byte check sufficient for D-26 (no python-magic) | Open Questions #6 | If wrong mime slips through, only cartera sees it (low risk given D-27) |
| A8 | Meta upload field name is `messaging_product` | Patterns to Extend | API 400 on upload; check official docs |

**High-risk assumptions:** A1 (interrupt API), A6 (infinite loop risk). Verify both before coding 04-04 and 04-03.

---

## Metadata

**Confidence breakdown:**
- Reusable patterns: HIGH — inspected source directly
- Schema design: HIGH — follows established project conventions
- LangGraph interrupt: MEDIUM-HIGH — well-known pattern, specific 1.2.x syntax assumed
- ARQ cron: MEDIUM — version 0.28, feature existence assumed
- Meta API shapes: MEDIUM — well-documented API, specific field names assumed
- Chatwoot webhook: MEDIUM — feature confirmed, exact header names assumed

**Research date:** 2026-06-29
**Valid until:** 2026-07-29 (stable stack)

---

## Operational Quirk (inherited from F3)

> **`agent-worker` Railway service does NOT auto-deploy on git push.**
>
> Any plan that modifies `app/worker.py` (adding functions to `WorkerSettings.functions` or `cron_jobs`) MUST include a deployment step:
> ```bash
> railway up --service agent-worker --ci --detach
> ```
> Plans 04-06 (ARQ schedulers) and any other plan touching `worker.py` must include this as an explicit task, not an afterthought. Confirmed in `03-06-SMOKE.md` § Live Smoke Findings.
