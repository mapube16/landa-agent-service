# 03-00 — Wave 0 probe findings

**Captured:** 2026-06-29
**Operator:** Maximiliano Pulido (cartera.dpg credentials, DPG sandbox real)
**Purpose:** Resolve RESEARCH Open Questions #1, #2, #3 before Wave 1+

> All PII sanitized — only field names, status codes, and structural shape exposed. The DPG test document `900144220-7` (corporate NIT, used for testing only — NOT a real-person cédula) is recorded for downstream test fixture use.

---

## Task 1 — `/api/cliente/listar_cliente_por_documento/` endpoint shape

### Final decision

`DECISION = use_listar_endpoint_with_secondary_poliza_call`

Two-call pattern required:
1. `GET /api/cliente/listar_cliente_por_documento/?numero_documento=<doc>` → returns single `Cliente` object with `id` field
2. `GET /api/poliza/?cliente=<cliente_id>` → returns paginated list of pólizas owned by that cliente

The single-call fallback (`/api/poliza/?cliente_numero_documento=<doc>`) was tested and **does NOT filter** — see "Fallback rejected" below.

### Probe 1 — discover query param name

- **Request:** `GET /api/cliente/listar_cliente_por_documento/?documento=900144220-7`
- **Status:** `400 Bad Request`
- **Body:** `"numero_documento requerido"`
- **Takeaway:** server tells you the required param name verbatim. Correct param is `numero_documento` (not `documento`, not `cedula`, not `nro_documento`).

### Probe 2 — happy path with correct param

- **Request:** `GET /api/cliente/listar_cliente_por_documento/?numero_documento=900144220-7`
- **Status:** `200 OK`
- **Response type:** single `dict` (NOT paginated `{count, next, previous, results}`)
- **Top-level keys count:** **122** — same shape as `/api/cliente/{id}/` (per Phase 2 `SOFTSEGUROS_API_NOTES.md`)
- **`id`** field is the `cliente_id` used by downstream queries
- **Embedded pólizas:** ❌ NO. The 5 keys matching `poliza` regex are notification-preference booleans (`enviar_correo_polizas_vencidas`, `enviar_sms_poliza_por_vencer`, `enviar_whatsapp_poliza_por_vencer`, `enviar_correo_polizas_vencidos`, `enviar_whatsapp_polizas_vencidos`) — NOT actual policy data.

#### Fields relevant for Plan 03-01 `ClienteRaw` Pydantic model (subset of 122):

| Field | Type | Notes |
|---|---|---|
| `id` | `int` | PK — used for `/api/poliza/?cliente=<id>` |
| `nombres` | `str` | First name(s). Empresas: razón social |
| `apellidos` | `str` | Last name(s). Empresas: often empty |
| `numero_documento` | `str` | Echo of query param |
| `tipo_documento` | `str` | Document type label |
| `model_tipo_documento_code` | `str` | Document type machine code |
| `model_tipo_documento_name` | `str` | Document type human label |
| `digito_verificacion` | `str \| None` | Check digit (NIT) — may be `None` |
| `email` | `str` | Customer email |
| `celular` | `str` | Mobile phone (10 chars, no `+57`) — Colombia local format |
| `telefono` | `str` | Landline |
| `direccion` | `str` | Address line |
| `pais` / `provincia` / `ciudad` | `str` | Location parts |
| `es_consorcio` | `bool` | Company-like flag |
| `tipo_cliente` | `str` | Category code |
| `activo` | `bool` | Active flag |
| `created_at` / `updated_at` | `str` (ISO) | Timestamps |

Additional 100 keys (notifications, fiscal data, segmentation, vehicle/house ownership flags, etc.) are present in the response but **NOT needed** for Phase 3 Q&A tools. `ClienteRaw` should model only the subset above; the rest stays out of the sanitized DTO that flows to the LLM.

### Probe 3 — secondary call: pólizas by cliente_id

- **Request:** `GET /api/poliza/?cliente=<id>&limit=20`
- **Status:** `200 OK`
- **Response type:** standard DRF paginated `{count, next, previous, results, data_cache_stats}`
- **Filter behavior:** ✅ correct — all `results[].cliente == <id>`, `count` reflects only that cliente's pólizas
- **Test cliente has 20 pólizas across ≥6 ramos** (RESPONSABILIDAD CIVIL, MANEJO, PYME, TRANSPORTE DE VALORES, AUTOMÓVILES × multiple) — **perfect fixture for D-02 multi-poliza disambiguation flow.**
- **Per-poliza schema is the same 180-field shape from Phase 2 `SOFTSEGUROS_API_NOTES.md` `/api/poliza/{id}/`** — Phase 3 reuses `PolizaRaw` model.

### Fallback rejected — `/api/poliza/?cliente_numero_documento=<doc>`

- **Request:** `GET /api/poliza/?cliente_numero_documento=900144220-7`
- **Status:** `200 OK` — but ⚠️
- **Top-level `count`:** `52898` — entire DPG poliza universe
- **`results[].cliente_numero_documento`:** 10 distinct documents in 10 results — **filter completely IGNORED by server**
- **Takeaway:** This query param is silently dropped. Cannot rely on single-call fallback. Two-call pattern is mandatory.

### Implications for downstream plans

**Plan 03-02 — `SoftSegurosClient.get_clientes_by_documento`:**
- Method signature: `async def get_clientes_by_documento(self, numero_documento: str) -> ClienteRaw` (returns ONE cliente, not a list, since the endpoint returns a single object)
- Cache key: `softseguros:doc:{numero_documento}:cliente` (TTL 60s, matches existing pattern)
- Implementation: single call to `/api/cliente/listar_cliente_por_documento/?numero_documento={doc}`. Returns parsed `ClienteRaw`.
- HTTP 404 handling: if cliente doesn't exist (per D-03 retry logic), the endpoint returns... **NOT TESTED YET** — open follow-up: probe with a fake document like `000000000-0` to confirm 404 shape (or empty body, or different status). Plan 03-02 must handle this gracefully.
- Plan 03-02 must ALSO add a SECOND READ method: `async def get_polizas_by_cliente(self, cliente_id: int) -> list[PolizaRaw]` (paginated; for F3 take first N=20 — limit param works). Both methods are READ-ONLY and pass the existing CI guard test (`tests/test_softseguros_readonly.py`).

**Plan 03-01 — `ClienteRaw` Pydantic model:**
- Model only the subset of fields listed above (NOT all 122 — keep DTO small)
- Use `model_config = ConfigDict(extra="ignore")` so the 100 unused fields don't break parsing if SoftSeguros adds/changes them
- Sanitized DTO for LLM: `ClienteSanitized(id, nombres, apellidos)` — strip celular/email/direccion/etc. The LLM does NOT need PII to disambiguate pólizas.

**Plan 03-05 — `node_identify`:**
- Receives `numero_documento` from cliente input
- Calls `softseguros.get_clientes_by_documento(doc)`
- Then calls `softseguros.get_polizas_by_cliente(cliente.id)` to get the poliza list
- If `count == 0` → escalate with T-03 (after 1 retry per D-03)
- If `count == 1` → skip `awaiting_policy_choice`, go straight to `answering_qa` with that poliza locked
- If `count >= 2` → transition to `awaiting_policy_choice`, send T-04 with the list (use `numero_poliza`, `ramo_nombre`, `estado_poliza_nombre` columns)

**Test fixture:** Document `900144220-7` (corporate NIT, returns 20 pólizas across 6+ ramos) is the canonical multi-poliza test client for D-02 verification in Smoke 1.

---

## Task 2 — Chatwoot API Channel inbox setup

### Final decision

`CHATWOOT_INBOX_CHANNEL_TYPE = Channel::Api` ✅ verified end-to-end against the live inbox.

### Operator actions completed

1. Operator logged into Chatwoot UI.
2. Inbox created via wizard: **Settings → Inboxes → Add Inbox → API channel** → name `landa-agent-mirror`, webhook URL left for F4 to wire.
3. Access token captured from **Profile Settings → Access Token**.

### Probe — `GET /api/v1/accounts/1/inboxes/2` (smoke verification)

- **Status:** `200 OK`
- **Top-level response keys count:** 39
- **`channel_type`:** `Channel::Api` ✅ (NOT `Channel::Whatsapp` — that one is for F4)
- **`name`:** `landa-agent-mirror`
- **`inbox_identifier`:** present (UUID-like) — F3 does NOT use this directly; Chatwoot Application API authenticates via `api_access_token` header (per RESEARCH Pattern 5)
- **`webhook_url`:** operator pre-filled with `https://landa-agent-service-production.up.railway.app/webhooks/chatwoot`. **F3 does NOT implement this endpoint** (it's F4 territory). Chatwoot will get 404s when posting events there; this is benign for F3 (mirror is one-way landa→chatwoot). F4 will own `/webhooks/chatwoot`.

### Env vars (captured in operator transcript, NOT committed)

The following env vars are recorded in the operator's session transcript and are scheduled to be set in Railway during **Plan 03-06** (smoke deploy) via `railway variable set`. **They are intentionally NOT pasted into this file** to avoid token leak through git history:

| Variable | Source | Status |
|---|---|---|
| `CHATWOOT_URL` | URL del Railway deploy de Chatwoot (`https://chatwoot-production-d073.up.railway.app`) | captured ✅ |
| `CHATWOOT_API_KEY` | Chatwoot Profile → Access Token | captured ✅ (rotación pendiente al cerrar F3) |
| `CHATWOOT_ACCOUNT_ID` | `1` (single tenant) | captured ✅ |
| `CHATWOOT_INBOX_ID` | `2` (numérico, del path `/app/accounts/1/inbox/2`) | captured ✅ |
| `CHATWOOT_INBOX_CHANNEL_TYPE` | `Channel::Api` (validated por probe ✅) | captured ✅ |

### Custom domain note (deferred — not a blocker)

Chatwoot is currently served at `https://chatwoot-production-d073.up.railway.app`, NOT the planned `chat.landatech.org`. The custom domain + SSL was deferred at the close of Phase 1 (Phase 2 CONTEXT close summary). F3 works with whichever URL Chatwoot answers on — when the custom domain lands, only `CHATWOOT_URL` env var changes (no code change, no redeploy of landa-agent-service required).

### Implications for downstream plans

**Plan 03-01 — `ChatwootSettings`:**
- All 4+1 env vars above declared with `SecretStr` for `CHATWOOT_API_KEY`, plain types for the rest. `env_prefix="CHATWOOT_"`. Add to `app/config/settings.py` following the `WhatsAppSettings` / `SoftSegurosSettings` pattern.

**Plan 03-03 — `ChatwootClient`:**
- httpx async client with base URL = `CHATWOOT_URL`, header `api_access_token: <CHATWOOT_API_KEY>`, `Content-Type: application/json`.
- Methods: `create_conversation`, `post_message_incoming`, `post_message_outgoing`, `mark_resolved` (all hitting `/api/v1/accounts/{account_id}/conversations/...`).
- `account_id` and `inbox_id` are constructor args (from settings), not per-call args.

**Plan 03-04 — `chatwoot_settings_test`:**
- Test that validates `settings.chatwoot.inbox_channel_type == "Channel::Api"` (literal check, NOT `Channel::Whatsapp` — prevents accidentally wiring F3 to the F4 inbox when F4 lands).

---

## Task 3 — Gemini Flash structured output via OpenRouter

### Final decision

`STRUCTURED_OUTPUT_OK = true` ✅ — `with_structured_output(JudgeRubric)` via LangChain → OpenRouter → Gemini Flash is feasible.

### Probe details

- **Endpoint:** `POST https://openrouter.ai/api/v1/chat/completions`
- **Model:** `google/gemini-2.5-flash` (env `LLM_MODEL_JUDGE` literal value)
- **Temperature:** `0`
- **Headers:** `Authorization: Bearer <OPENROUTER_API_KEY>`, `Content-Type: application/json`
- **Body:** OpenAI-compatible chat completions, with `response_format.type = "json_schema"` and `response_format.json_schema.strict = true`
- **Test schema:** `{is_in_scope: bool, factually_grounded: bool, rationale: str}` (3-field subset; full 8-flag `JudgeRubric` shape is structurally identical, just longer)

### Probe results

- **HTTP status:** `200 OK`
- **`finish_reason`:** `stop` (clean termination)
- **`provider`:** present in response (which actual backend served the request)
- **`choices[0].message.content`:** parseable as JSON, matches schema exactly
- **`is_in_scope`:** `true` (correctly identified saldo question as in-scope)
- **`factually_grounded`:** `false` (correctly identified that no tool output was provided to verify grounding) — judge actually applied reasoning, not just template fill
- **`rationale`:** 228-char Spanish text explaining the reasoning
- **Token usage:** `prompt_tokens=76, completion_tokens=81` (~157 total per judge call — cost predictable, ~$0.000035 per call at Flash pricing)

### Implications for downstream plans

**Plan 03-01 — `JudgeRubric` Pydantic model + skeleton:**
- Use full 8-flag schema per CONTEXT D-05 (`is_in_scope`, `leaks_other_polizas`, `affirms_payment_without_cartera_approval`, `factually_grounded`, `no_jailbreak_echo`, `no_pii_leak`, `no_external_links`, `sentiment_appropriate`, plus `rationale: str`).
- All 8 boolean fields are `Required` in the JSON schema (not `Optional`) — OpenAI-compat strict mode rejects optional booleans.

**Plan 03-04 — `judge.py`:**
- Use `get_llm("judge").with_structured_output(JudgeRubric)` — LangChain handles the `response_format.json_schema` wiring automatically.
- `temperature=0` already enforced at LLM factory level for judge role (CLAUDE.md + CONTEXT D-07).
- Token budget per judge call: ~150-200 total tokens (input prompt depends on system prompt + outbound message length; F3 has no hard cap per CONTEXT D-08).

**Plan 03-04 — `kb_auditor.py` LLM judge layer:**
- Same pattern with a different schema (`KBAuditRubric` — Layer 4 of the 5-layer pipeline). Same model (`LLM_MODEL_JUDGE` env var value, Gemini Flash temp=0).

---

## Task 4 — Consolidation (this file)

All three probes have produced actionable findings:

| Open Question | Status | Blocker resolved |
|---|---|---|
| RESEARCH #1 — `listar_cliente_por_documento` shape | ✅ resolved | Plan 03-01 `ClienteRaw` + Plan 03-02 `get_clientes_by_documento` + `get_polizas_by_cliente` |
| RESEARCH #2 — Chatwoot operability at chat.landatech.org | ✅ resolved (with deferral note) | Plan 03-01 `ChatwootSettings` + Plan 03-03 `ChatwootClient` |
| RESEARCH #3 — Gemini Flash structured output via OpenRouter | ✅ resolved | Plan 03-04 `judge.py` + `kb_auditor.py` |

**Wave 1 is unblocked.** Plans 03-01..03-06 can now proceed against confirmed contracts.

### Operator-side TODOs (deferred to plan 03-06)

When Plan 03-06 runs the smoke deploy, the operator must set these in Railway (already captured in transcript):

```bash
railway variable set CHATWOOT_URL=<value from probe>
railway variable set CHATWOOT_API_KEY=<value from probe>
railway variable set CHATWOOT_ACCOUNT_ID=1
railway variable set CHATWOOT_INBOX_ID=2
railway variable set CHATWOOT_INBOX_CHANNEL_TYPE=Channel::Api
```

Plus rotate `SOFTSEGUROS_PASSWORD` and `CHATWOOT_API_KEY` once F3 closes (both values leaked through this session's transcript per established F2 pattern with `WA_TOKEN`).

---

*Generated 2026-06-29 from operator-run curl probes against `https://app.softseguros.com/` with cartera.dpg credentials.*
