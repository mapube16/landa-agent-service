# Contrato de integración: lambda-proyect (voz) ↔ landa-agent-service (WhatsApp)

**Estado:** DRAFT v1 — 2026-07-04. Este documento es la fuente de verdad del
contrato de la Fase 6. Ambos repos construyen contra él. Cambios al contrato
requieren bump de versión aquí + aviso al otro lado.

**Partes:**
- **VOICE** = `lambda-proyect` (`backend/cobranza/`) — agente de voz/cobranza.
- **WA** = `landa-agent-service` (este repo) — agente WhatsApp + validación de pago.

**Transporte:** REST/JSON sobre HTTPS. **Auth (ambas direcciones):** bearer token
compartido, comparado con `hmac.compare_digest` (constante en tiempo). WA ya usa
`LAMBDA_PROYECT_INTERNAL_TOKEN` para esto (ver `app/webhooks/handoff.py`). VOICE
debe validar el suyo igual. Recomendado: **dos tokens distintos** (uno por
dirección) para poder rotar sin romper el otro sentido.

---

## Contrato A — VOICE → WA: `POST /case/handoff`

Lo expone **WA**. VOICE lo llama cuando cede un caso vivo al canal WhatsApp
(distinto del `/case/handoff/no_answer` que YA existe para llamadas no
contestadas — ese se queda como está).

**Auth:** `Authorization: Bearer <LAMBDA_PROYECT_INTERNAL_TOKEN>`

**Request body:**
```json
{
  "case_id": "550e8400-e29b-41d4-a716-446655440000",  // UUID v4, lo crea VOICE
  "debtor_id": "dpg-deudor-123",                        // id del deudor en el mundo voz
  "poliza_number": "POL-000123",
  "call_id": "twilio-CAxxxx",                           // id de la llamada origen
  "user_id": "agente-voz-7",                            // quién cedió (auditoría)
  "phone": "+573001234567",                             // E.164, obligatorio
  "initial_context": "Cliente dice que ya pagó pero no encuentra el comprobante.",
  "message": "Hola, soy el asistente de DPG. Vi que hablaste con..."  // 1er mensaje opcional
}
```

**Validaciones WA:** `phone` E.164 (`^\+\d{8,15}$`), `case_id` UUID válido,
`poliza_number` 1-40 chars. Campos opcionales: `call_id`, `user_id`,
`initial_context`, `message`.

**Comportamiento WA:**
1. Idempotencia por `case_id` (PK de `cases`). Retransmisión → `200 {sent:false}`,
   sin duplicar caso ni reenviar mensaje.
2. Crea/actualiza el Case: `status="awaiting_receipt"`, guarda `poliza_id`,
   `phone`, y anexa `call_id` a `call_ids[]` (ver extensión de esquema abajo).
3. Si viene `message`, lo envía al cliente por WhatsApp (template si la ventana
   24h está cerrada; freeform si abierta). Si no, WA usa su saludo default.
4. Registra evento de audit (`action="handoff_received"`, actor="voice").

**Responses:**
| Código | Body | Cuándo |
|--------|------|--------|
| 200 | `{"case_id": "...", "sent": true}` | caso creado + mensaje enviado |
| 200 | `{"case_id": "...", "sent": false}` | retransmisión idempotente |
| 401 | `{"detail": "invalid bearer"}` | token malo/ausente |
| 422 | detalle Pydantic | body inválido |

---

## Contrato B — WA → VOICE: mutaciones del deudor

Los expone **VOICE**. WA los llama cuando una tool muta estado del deudor.
Auth: `Authorization: Bearer <token WA→VOICE>`.

### B1. `POST /cobranza/case/{case_id}/escalate`
WA lo llama cuando escala un caso (rechazo de cartera, firewall, etc.).
```json
// request
{ "reason": "cartera_rejected", "channel": "whatsapp", "note": "Comprobante ilegible" }
// response 200
{ "case_id": "...", "status": "escalated" }
```

### B2. `POST /cobranza/debtor/{debtor_id}/update`
WA lo llama para propagar flags del deudor a `db.debtors` del mundo voz.
```json
// request — todos los campos opcionales, se actualiza lo que venga
{
  "estado": "escalado",
  "promesa_de_pago": true,
  "promesa_fecha": "2026-07-10",
  "ultima_interaccion_wa": "2026-07-04T05:00:00Z",
  "intentos": 3
}
// response 200
{ "debtor_id": "...", "updated": true }
```

**Idempotencia B:** VOICE debe tolerar reintentos (WA puede reintentar en fallo
de red). Updates son de "última escritura gana" por campo; escalate es
idempotente por `(case_id, reason)`.

---

## Propiedad del `case_id` e idempotencia

- **Llamada primero:** VOICE crea el `case_id` (UUID v4) al iniciar la llamada y
  lo pasa en el handoff. WA lo reutiliza.
- **WhatsApp primero (sin llamada previa):** WA crea su propio `case_id` (ya lo
  hace hoy en `node_receive_comprobante` / `_get_or_create_case`). Ese caso queda
  discoverable por VOICE vía la colección compartida `db.cases`.
- **Regla de oro:** un `phone` con un caso NO terminal se reutiliza; nunca dos
  casos abiertos para el mismo deudor a la vez.

---

## Modelos compartidos (`Debtor`, `Policy`, `ConversationContext`)

El ROADMAP pide un git submodule `landa-shared`. **Recomendación v1:** NO montar
el submodule todavía — es un tercer repo con versionado propio, over-engineering
para arrancar. En su lugar:

1. Definir los 3 modelos Pydantic v2 **acá, en este contrato** (abajo), como
   fuente de verdad.
2. Cada repo los copia (duplicación deliberada, ~40 líneas) y un test de contrato
   valida un ejemplo JSON canónico contra su copia.
3. Cuando el drift entre copias empiece a doler (o entre un 2º cliente), se
   extrae `landa-shared` como submodule. Documentar ese trigger en un ADR.

**Esquemas mínimos (fijar antes de codear):**
```python
class Policy(BaseModel):
    numero: str            # "POL-000123"
    estado: str            # "vigente" | "vencida" | ...
    # (campos SoftSeguros que ambos lados necesiten — LISTAR juntos)

class Debtor(BaseModel):
    debtor_id: str
    phone: str             # E.164
    nombre: str
    poliza: Policy | None = None
    promesa_de_pago: bool = False
    escalado_previo: bool = False

class ConversationContext(BaseModel):
    case_id: str
    canal_origen: str      # "voice" | "whatsapp"
    initial_context: str | None = None
    call_id: str | None = None
```
> ACCIÓN CONJUNTA: completar los campos de `Policy`/`Debtor` con lo que VOICE ya
> maneja en `db.debtors`, para no adivinar. Ese es el único punto que requiere
> mirar el repo de voz.

---

## Extensión de esquema en WA (`cases` cross-canal)

La tabla `cases` actual (`app/memory/case_store.py`) NO tiene los arrays
cross-canal que pide el ROADMAP. La Fase 6 agrega (migración 0004):
- `call_ids: JSONB []` — llamadas asociadas
- `conversation_ids: JSONB []` — conversaciones Chatwoot/WA
- `escalations: JSONB []` — historial de escalaciones
- `events: JSONB []` — timeline del caso cross-canal
- `debtor_id: Text` — link al mundo voz

---

## Reparto de construcción

| # | Entregable | Repo | Notas |
|---|-----------|------|-------|
| 1 | `POST /case/handoff` (Contrato A) | **WA** | reusa patrón de `handoff.py` (bearer + idempotencia) |
| 2 | Migración 0004: cases cross-canal | **WA** | JSONB arrays + debtor_id |
| 3 | `memory/case_store.py`: read/write cross-canal | **WA** | extiende el existente |
| 4 | `memory/debtor_flags.py` + inyección al system prompt | **WA** | lee flags antes de responder; actualiza tras cada turno |
| 5 | Tools WA que llaman B1/B2 (cliente REST a VOICE) | **WA** | `integrations/lambda_proyect.py` ya es el placeholder |
| 6 | `POST /cobranza/case/{id}/escalate` (Contrato B1) | **VOICE** | |
| 7 | `POST /cobranza/debtor/{id}/update` (Contrato B2) | **VOICE** | |
| 8 | Reemplazo del stub muerto `whatsapp_notifier.py` | **VOICE** | en vez de encolar `send_whatsapp_job` (no registrado), hace `POST /case/handoff` a WA |
| 9 | Copia de modelos compartidos + test de contrato | **AMBOS** | frozen contra este doc |

**Criterios de éxito (E2E, requieren ambos lados vivos):** ver ROADMAP Fase 6.
El test de retransmisión (mismo handoff 3× → un solo caso) se puede validar
contra WA sola apenas exista el entregable #1.

---

## Decisiones abiertas para el operador

1. ¿Un token compartido o dos (uno por dirección)? (Recomendado: dos.)
2. ¿Completar campos de `Policy`/`Debtor` ahora? (Necesita mirar `db.debtors` de voz.)
3. ¿`landa-shared` submodule ya, o duplicación frozen v1? (Recomendado: duplicación.)
4. URL base de VOICE para que WA llame B1/B2 → env `LAMBDA_PROYECT_BASE_URL` (ya existe).
