# SoftSeguros API — discovery notes (DPG tenant)

**Captured:** 2026-06-28 via authenticated probes with user `cartera.dpg` against `https://app.softseguros.com/`.

These notes resolve the LOW-confidence area flagged by the Phase 2 researcher (`02-RESEARCH.md` §"Open Questions" #1). Use them to model Pydantic schemas in Phase 3 (Q&A bot) and to harden the Phase 2 SoftSeguros client.

> All PII values stripped — only field names, types, and well-known constants (enum values) shown.

---

## Auth flow

`POST https://app.softseguros.com/api-token-auth/`

Body:
```json
{"username":"cartera.dpg","password":"<from env>"}
```

Response (200): rich user/tenant profile object including:
- `token` (40-char hex)
- `id`, `user_id`, `username`, `nombre_completo`, `email`, `email_user`
- `marca_id: 1122`, `nombre_marca: "DPG Seguros Sociedad Ltda"`, `marca_nit: "900.783.180-1"`, `sufijo_pais: "CO"`
- `perfil: 4`, `perfil_name: "Cartera"`
- `phone_code: "57"` (Colombia)
- Operational flags: `consecutivo_cuotas`, `numero_pago_editable`, `precision_moneda: 2`, etc.
- License & quota info: `licencia_code`, `pólizas_vigentes: 8000`, etc.

Subsequent calls authenticate with header: `Authorization: Token <token>`.

**Token lifetime:** opaque. Plan to refresh on `401 Unauthorized`. (`days_remaining: 58` in auth response is the license window, not the token TTL.)

---

## Endpoints discovered

| Endpoint | Method | Status | Notes |
|---|---|---|---|
| `/api-token-auth/` | POST | 200 | Auth. Returns token + rich tenant profile. |
| `/api/poliza/?limit=N` | GET | 200 | DRF pagination (`count`, `next`, `previous`, `results`). 8000+ polizas in DPG. |
| `/api/poliza/{id}/` | GET | 200 | Full detail. **184 root fields** — see schema below. |
| `/api/cliente/{id}/` | GET | 200 | Full client detail. **122 root fields** — see schema below. |
| `/api/estadopoliza/?limit=N` | GET | 200 | Lookup table of 8 estado codes. **See enum below.** |
| `/api/estadopoliza/{id}/` | GET | 404 | The lookup-style detail endpoint does NOT exist — use the list. The `poliza.estado_poliza` int FK joins to this table; the embedded `estado_poliza_nombre` + `estado_poliza_codigo` already suffices for the bot. |
| `/api/pagopoliza/?poliza_id={id}&limit=N` | GET | 504 | Gateway timeout. **SUPERSEDED — use `list_pagospolizas_filtro_paginados` below.** |
| `/api/pagopoliza/?numero_poliza=...&limit=N` | GET | timeout | Same. |
| `/api/pagopoliza/list_pagospolizas_filtro_paginados/` | GET | 200 | **FAST. Solves the 504 (open Q #3, 2026-07-04).** Cartera-por-cobrar view. Scope to one policy: `?sede=1047&texto_busqueda={numero_poliza}&search_in=poliza_numero_poliza`. Returns DRF-paginated `{count, next, previous, results[]}` — one row per cuota. Auth: the app's OWN token (`/api-token-auth/`) works — no browser token needed. See field map + caveats below. |
| `/api/pago/` | GET | 404 | Not a real route. |

---

## Estado de póliza — enum values

The lookup table at `/api/estadopoliza/`:

| id | codigo | nombre | activo |
|---|---|---|---|
| 6576 | `01` | `Vigente` | true |
| 6577 | `02` | `Cotizacion` | true |
| 6578 | `03` | `Devengada` | true |
| 6579 | `04` | `Expedicion` | true |
| 6580 | `05` | `No renovada` | true |
| 6581 | `06` | `Cancelada` | true |
| 9976 | `01` | `Nueva` | true |
| 18887 | `01` | `Vencida` | true |

Note: `codigo` is NOT unique (`01` appears 3 times for `Vigente`, `Nueva`, `Vencida`). Use `id` as the primary key or `nombre` as the human-readable label.

The bot's Q&A in Phase 3 will likely care about these mappings:
- **Vigente / Devengada / Nueva** → "tu póliza está activa"
- **Cotizacion / Expedicion** → "estamos procesando tu póliza"
- **No renovada / Cancelada / Vencida** → "tu póliza no está activa"

---

## Poliza schema (184 fields)

Grouped by what's likely relevant for the bot's Q&A. **Bold = the bot will read this field in F3.**

### Identity & dates
- `id: int` PK
- **`numero_poliza: str` (~7 chars)** — what the cliente uses to identify themselves
- **`fecha_inicio: str (YYYY-MM-DD)`**
- **`fecha_fin: str (YYYY-MM-DD)`**
- `fecha_creacion: str (ISO datetime)`
- `fecha_cancelacion: str | null`
- `fecha_recaudo: str | null`
- `fecha_limite_pago: str | null`

### Estado
- **`estado_poliza: int` FK to estadopoliza.id**
- **`estado_poliza_nombre: str`** ← human-readable; see enum above
- **`estado_poliza_codigo: str`**
- `motivo_estado: null`

### Cliente (embedded summary — full detail at `/api/cliente/{cliente}/`)
- **`cliente: int` FK**
- **`cliente_nombres: str`**
- `cliente_apellidos: str` (often empty for empresas)
- `cliente_numero_documento: str`
- `cliente_es_consorcio: bool`
- **`cliente_celular: str` (10 chars, no `+57` prefix — local Colombia format)**
- `cliente_telefono: str`
- **`cliente_email: str`**
- `cliente_direccion: str`
- `cliente_ciudad: str`
- `cliente_observaciones: str`

### Asegurado / Beneficiario / Tomador
- `nombre_asegurado: str`
- `cedula_asegurado: str` (10 chars)
- `nombre_beneficiario: str`
- `cedula_beneficiario: str`
- `cedula_beneficiario_poliza: str` (10 chars)
- `nombre_tomador: str`
- `apellido_tomador: str | null`
- `cedula_tomador: str`

### Ramo (línea de negocio)
- **`ramo: int` FK**
- **`ramo_nombre: str` (e.g., `AUTOMÓVILES`)**
- **`ramo_global_nombre: str` (e.g., `AUTOS/VEHÍCULOS`)**
- **`ramo_global: str` (numeric code as str, e.g., `'11'`)**
- `ramo_aseguradora_nombre: str` (e.g., `PREVISORA`)
- `ramo_aseguradora_id: str`
- `ramo_porcentaje_iva_prima: str` (e.g., `'19.00'`)
- `ramo_codigo_inhouse: str | null`
- `tipo_poliza: str` (`'individual'` | …)

### Vendedor / Sede
- `vendedor: int` FK
- `vendedores_nombre: str`
- `vendedores_cedula: str`
- `sede: int` FK
- `sede_id: str`
- `sede_nombre: str`
- `vendedores: array[1]` (full vendedor object embedded; ~20 sub-fields)

### Aseguradora
- `aseguradora_nit: str`
- `aseguradora_direccion: str`

### Financiero (montos)
- **`prima: str` (numeric as string)**
- **`total: str` (numeric as string)**
- `iva: str | null`
- `total_pagos_poliza: int` (count of payments registered for this poliza)
- `pago_poliza_consecutivo: int` (next consecutivo number)
- `prima_equivalente: str`
- `gastos_expedicion: str | null`
- `accesorios: str | null`
- `prima_asistencia: str` (often `'0'`)
- `prima_asistencia_comision: bool`
- `tasa_cambio: str` (e.g., `'1.0000'` for COP)
- `valor_asegurado_riesgo: str | null`

### Comisiones (vendedor / agencia / sede / técnico)
- `comicion: str`
- `comision_total: str`
- `porcentaje_comision_vendedor`, `comision_vendedor`, etc. (`null` for many)
- `porcentaje_sobrecomision: str | null`
- `sobrecomision: str | null`

### Financiación
- `porcentaje_financiacion: str`
- `valor_financiacion: str`
- `total_poliza_financiada: str`
- `financiacion_incluye_comision: bool`
- `financiacion_calcular_iva: bool`
- `beneficiario_oneroso: bool`
- `numero_de_cuotas: int | null`

### Forma de pago
- `forma_pago: int | null` FK
- `forma_pago_texto: str | null`
- `forma_pago_texto_parametros: str`
- `medio_pago: str | null`
- `recaudado: bool`
- `recaudado_en_oficina: bool`

### Coberturas (vacío para esta póliza de muestra)
- `coberturas: array` (probably array of cobertura objects; need to find a poliza with coberturas to map)
- `categorias: array`
- `nombre_categorias: str`

### SOAT-specific (null para no-SOAT)
- `soat: bool`
- `soat_tipo_vehiculo: str | null`
- `soat_tipo_cartera: str | null`
- `soat_movimiento: str | null`
- `soat_virtual: bool | null`
- `soat_codigo_fasecolda: str | null`
- `soat_anulado: bool`

### Notification preferences (flags pre-existentes en SoftSeguros — útil si en F3 queremos honrar las prefs)
- `enviar_whatsapp_poliza_por_vencer: bool`
- `enviar_whatsapp_cartera_por_vencer: bool`
- `enviar_whatsapp_polizas_vencidos: bool`
- `enviar_whatsapp_pagos_vencidos: bool`
- `enviar_whatsapp_aviso_cancelacion: bool`
- Plus 14 más para correo + SMS

### Misc
- `is_editable: bool`
- `activo: bool`
- `renovable: bool`
- `numero_renovacion: int`
- `tipo_moneda: int | null` FK
- `tipo_moneda_nombre: str`
- `periodicidad: str | null`
- `json_data: object` (extension point — TBD shape)
- `json_respuesta: object | null`
- `json_form_ramo_global: object | null`
- `file_storage_data: object` (file attachments metadata)

---

## Cliente schema (122 fields)

### Identity
- `id: int` PK
- **`nombres: str`**
- `apellidos: str` (often empty for empresas)
- `alias: str | null`
- `tipo_documento: str` (`'nit'` | `'cc'` | otros)
- `model_tipo_documento_code: str` (2-char code)
- `model_tipo_documento_name: str`
- `model_tipo_documento: int` FK
- **`numero_documento: str`**
- `digito_verificacion: str` (NIT check digit)
- `genero: str` (`'company'` | otros — empresas devuelven `'company'`)

### Contact
- **`celular: str` (10 chars, sin `+57`)**
- `telefono: str`
- **`email: str`**
- `direccion: str`
- `ciudad: str`
- `provincia: str`
- `pais: str | null`
- `nacionalidad: str | null`
- `direccion_secundaria: str`
- `codigo_postal: str | null`
- `localidad: str | null`
- `distrito: str | null`
- `pagina_web: str`
- `perfil_social: str`

### Tenant context
- `sede: int` FK (sub-organización dentro de DPG)
- `nombre_sedes: str` (e.g., `'principal'`)
- `sedes: array` (full sede objects)
- `marca: int = 1122` (DPG)
- `categorias: array`
- `tipo_cliente: str`
- `clientevendedor_tipo: str`

### Demografía / financiero
- `fecha_nacimiento: str | null`
- `fecha_expedicion_cedula: str | null`
- `fecha_vencimiento_documento: str | null`
- `estado_civil: str | null`
- `nivel_academico: str | null`
- `ocupacion: int | null` FK
- `ocupacion_string: str`
- `otra_ocupacion: str`
- `empresa_name: str | null`
- `tiene_hijos: bool`, `numero_hijos: int`
- `tiene_casa_propia: bool`, `numero_casas_propias: int`
- `tiene_vehiculo: bool`, `numero_vehiculos: int`
- `ingreso_mensual: str` (numeric as string)
- `patrimonio: str`
- `estrato: int | null`
- `licencia_conduccion: str | null`
- `categoria_licencia: str | null`

### Notification preferences (~22 booleans — espejo de los de poliza pero a nivel cliente)
- `enviar_whatsapp_cartera_por_vencer: bool`
- `enviar_whatsapp_polizas_vencidos: bool`
- `enviar_whatsapp_pagos_vencidos: bool`
- `enviar_whatsapp_aviso_cancelacion: bool`
- Y otros 18 para correo/SMS

### Estado
- `activo: bool`
- `email_verified: bool`
- `last_bounce_date: str | null`
- `bounce_count: int`
- `motivo_desuscripcion: str | null`
- `unsubscribed_date: str | null`
- `synchronized: bool`
- `last_synchronized: str | null`
- `created_at: str (ISO datetime)`
- `updated_at: str (ISO datetime)`
- `created_by: int | null` FK
- `tratamiento_de_datos: bool` (consent for data processing)
- `send_birthday_card: bool`

### Misc
- `aviso_operaciones: str | null`
- `pendiente_editar: bool`
- `es_consorcio: bool`
- `from_create: str | null`
- `compania: int | null`
- `riesgo_asegurado: int | null`
- `id_contacto_getresponse: str | null`
- `campaign_id_contacto_getresponse: str | null`
- `import_document: str | null`
- `nombres_contacto: str`
- `last_failure_reason: str | null`
- `last_failure_code: str | null`
- `estado_correo: int = 1`
- `estado_celular: int = 1`
- `segmentos: array`
- `datosExtras: array`
- `file_storage_data: object`

---

## Implicaciones para Phase 2 + Phase 3

### Phase 2 (`SoftSegurosClient` + `/test/poliza/{id}`)
- F2 returns `Dict[str, Any]` passthrough (raw response). NO Pydantic modelling yet.
- Use `httpx.AsyncClient(timeout=httpx.Timeout(connect=2, read=10, write=10, pool=2))`. The `/pagopoliza/` endpoint is unbearably slow → must use a generous `read` timeout if/when we add it later.
- Auth flow: cache token in process memory; on `401` clear cache + reauth + retry once. asyncio.Lock around the refresh path to avoid stampede.
- Cache key: `softseguros:{tenant}:{endpoint}:{poliza_id}` with TTL 60s per ROADMAP. For F2 only `/api/poliza/{id}/` matters.

### Phase 3 (Q&A bot tools)
- The 184-field poliza is overkill for the bot. Tool output sanitization MUST whitelist only:
  - `numero_poliza`, `estado_poliza_nombre`, `estado_poliza_codigo`
  - `fecha_inicio`, `fecha_fin`, `fecha_limite_pago`
  - `prima`, `total`, `total_pagos_poliza`
  - `ramo_nombre`, `ramo_global_nombre`
  - `cliente_nombres`, `cliente_celular`, `cliente_email` (only if absolutely needed)
- The cliente endpoint (122 fields) is NOT needed for v1 Q&A. Only the embedded cliente_* fields in poliza response are needed.
- Pagos: skip until we find a fast filter. The embedded `total_pagos_poliza: int` covers "how many payments have I made".

### Phase 4 (Payment flow)
- The bot reports payment **counts** from `total_pagos_poliza` but never registers payments — write happens in cartera's WhatsApp flow (human).
- If F4 needs payment **details**, we'll have to solve the `/pagopoliza/` timeout (maybe a `?fecha_desde=` filter or a different endpoint exists).

### Phase 5 (Security audit)
- The poliza endpoint exposes ALL commissions, IVA, sede info — way more than the bot should ever leak. **Tool output sanitization (Capa 4 of the 13) is the enforcement layer**. The bot's tool function MUST extract only whitelisted fields before passing to the LLM.

### Phase 6 (Voice handoff)
- The same client serves both the WhatsApp bot and lambda-proyect's voice bot. Refactor to `landa-shared` submodule. No changes to schemas needed.

---

## Open questions for the operator (Maxi) to validate with DPG

1. **¿El user `cartera.dpg` está OK para producción del bot, o pedimos uno dedicado (`bot.landa` con scope read-only)?** Defense in depth — capa adicional sobre nuestro code-level READ-ONLY enforcement.
2. **¿Hay rate limits en SoftSeguros?** No documentado. Probable que haya tier-based limits. Si el bot escala, hay que medir y posiblemente coordinar.
3. ~~¿`/api/pagopoliza/` tiene un endpoint optimizado?~~ **RESUELTO 2026-07-04.**
   `GET /api/pagopoliza/list_pagospolizas_filtro_paginados/?sede=1047&texto_busqueda={numero_poliza}&search_in=poliza_numero_poliza&order_by=fecha_pago&sort_by=asc&page=1` → HTTP 200, rápido. Auth = token propio del app (`/api-token-auth/`). Devuelve `{count,next,previous,results[]}`, una fila por cuota.
   **Mapa de campos (semántica del INFORME TÉCNICO §3):**
   - `fecha_pago` = vencimiento original de la cuota → base para días de mora.
   - `fecha_realizara_pago` = compromiso de pago (agenda de seguimiento). *(nombre plausible; CONFIRMAR con DPG que no es otro campo).*
   - `fecha_realizo_pago` + `saldo_pendiente` = ¿pagó?/cuánto debe. `saldo_pendiente="0.00"` = cuota saldada.
   - `poliza_numero_poliza`, `poliza_cliente_nombres/apellidos`, `poliza_cliente_celular`, `poliza_aseguradora_link_pago`, `edad_cartera`.
   **CAVEATS al cablear:** (a) response trae ~150 campos incl. comisiones/PII → **whitelist estricta Capa 4** antes del LLM; (b) es endpoint `list_*` → SOLO uso código scopeado por póliza, NUNCA tool de búsqueda al LLM; (c) `sede=1047` asumido = DPG (confirmar si multi-sede); (d) reemplaza el `get_pagos` con 504 en `app/integrations/softseguros.py:258`. Cablear cuando F6/debtor_flags lo consuma (YAGNI: no hay consumidor hoy).
4. **¿Cuáles son los campos que DPG considera PII estrictamente confidencial vs revelable al cliente?** Para la whitelist de Capa 4. Mi propuesta inicial está en "Phase 3" arriba — validar con DPG/legal.
5. **¿Tokens expiran?** `days_remaining: 58` en la auth response — es el license, no el token TTL. Asumimos refresh on 401 hasta que tengamos respuesta.

---

*Captured during Phase 2 planning — 2026-06-28.*
