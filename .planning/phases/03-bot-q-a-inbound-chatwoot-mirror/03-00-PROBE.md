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

**Status:** PENDING — awaiting operator action.

(See main session for the checkpoint protocol.)

---

## Task 3 — Gemini Flash structured output via OpenRouter

**Status:** PENDING — runs after Task 2 confirms Chatwoot creds (independent of Chatwoot, but executed atomically with Task 4 consolidation).

---

## Task 4 — Consolidation

This file IS the consolidation artifact. Section above for Task 1 is final. Tasks 2 + 3 sections will be filled in once the operator completes Chatwoot setup and the Gemini Flash probe runs.

---

*Generated 2026-06-29 from operator-run curl probes against `https://app.softseguros.com/` with cartera.dpg credentials.*
