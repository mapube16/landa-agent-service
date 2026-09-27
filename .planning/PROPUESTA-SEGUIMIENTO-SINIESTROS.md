# Propuesta: seguimiento agentico de siniestros del terremoto

Estado: propuesta para arrancar 16-sep-2026. Dueno: LANDA + DPG.
Contexto previo: `.planning/PLAN-SINIESTROS-TERREMOTO.md`.

---

## De donde salen los datos (ya resuelto)

| Fuente | Endpoint | Que da |
|---|---|---|
| Modulo Siniestros | `api/siniestro/list_paginado/` + `texto_busqueda=TERREMOTO` | 200 reclamaciones con estado, fechas, monto, responsable, aseguradora |
| Listado de polizas | `api/poliza/` con `filterType=searchGeneral` | 54.145 polizas -> 6.492 personas |
| Chatwoot | API v1 | 6.505 contactos, 154 marcados por sismo, 145 conversaciones etiquetadas |

**Token**: el modulo Siniestros NO responde con el token del app (403). Requiere el
token de la cuenta con permisos de siniestros. Guardar en Railway como
`SOFTSEGUROS_SINIESTROS_TOKEN`, nunca en el repo.

**Cuidado con el filtro de fechas**: `fecha_inicio_sel`/`fecha_fin_sel` NO filtran
por fecha del siniestro (aparecen avisos de marzo dentro del rango de agosto).
Lo que si discrimina es `texto_busqueda=TERREMOTO`, confirmado: 200 resultados,
todos con `fecha_aviso` entre 10-ago y 11-sep.

---

## Estado al 15-sep-2026

- 200 reclamaciones por terremoto, 154 personas, 197 de Hogar/Pyme/Copropiedad.
- **189 de 200 siguen en APERTURA**. Solo 2 pagadas.
- 36 llevan mas de 21 dias sin avanzar.
- Un solo responsable (Jorge Ivan Duque) figura en 188 de los 189 abiertos.
- Solo 11 de 200 tienen monto reclamado registrado.
- $948M reclamados. Suramericana 63, Previsora 55, Allianz 41 = 80%.

---

## Las 7 etapas (vision del cliente)

Salen del proceso que describio el cliente: aviso -> reclamacion -> presupuesto ->
perito -> ofrecimiento -> acuerdo -> pago. Ya creadas como etiquetas en Chatwoot.

| Etiqueta | Que vive el cliente | Senal tipica en WhatsApp | Nudge |
|---|---|---|---|
| `siniestro-aviso` | Aviso dado, falta formalizar | "ya reporte", "llame a la linea" | 24 h |
| `siniestro-reclamacion` | Reuniendo/enviando documentos | "me pidieron papeles", "mande la carta" | 3 d |
| `siniestro-presupuesto` | Cotizando la reparacion | "estoy pidiendo cotizaciones" | 5 d |
| `siniestro-perito` | Esperando o recibiendo al ajustador | "viene el perito", "ya vino a ver" | 7 d |
| `siniestro-ofrecimiento` | La aseguradora puso cifra | "me ofrecieron tanto" | 10 d |
| `siniestro-acuerdo` | Aceptando o negociando | "firme", "no estoy de acuerdo" | 5 d |
| `siniestro-pago` | Indemnizacion recibida | "ya me consignaron" | cierre |

Dos etiquetas que NO son etapa:
- `siniestro-sismo`: marca permanente del segmento. **El agente nunca la toca.**
- `siniestro-trabado`: se cruza con cualquier etapa cuando no hay movimiento.

**Estados de SoftSeguros son otra cosa** (APERTURA, EN TRAMITE, REVISION COMPANIA,
OBJETADO, PAGADO, CERRADO). Los pone DPG en el sistema; los de arriba describen lo
que vive el cliente. Conviven: un caso en APERTURA para SoftSeguros puede estar en
`siniestro-perito` para el cliente, y **esa distancia es justo la senal de donde
esta trabado**.

### Pendiente de definir con DPG

1. **Orden presupuesto/perito**: varia por aseguradora. Sin esto el agente puede
   marcar retroceso donde no lo hay.
2. **Gastos profesionales** (ajustador propio, abogado): NO es etapa, va como
   atributo del contacto. Puede aparecer en cualquier momento.
3. **Plazo ampliado de aviso** por aseguradora: va literal en la plantilla.
4. **Quien atiende siniestros**: mismo equipo de cartera o aparte. Define si basta
   la vista por etiqueta o hace falta un Team de Chatwoot.
5. **Documentos exigidos por etapa y aseguradora**: alimenta el KB para que el bot
   responda "que sigue" sin humano.

---

## Como funciona a partir de manana

### 1. Vista, no canal nuevo (DECIDIDO)

Se descarto el inbox aparte. Razon: en Chatwoot el contacto pertenece a un inbox,
asi que la misma persona en dos inboxes son dos hilos. Alguien con reclamacion del
sismo Y cuota vencida quedaria partido en dos conversaciones — el mismo bug que se
arreglo el 4-sep normalizando el telefono antes de la cache key.

En su lugar: filtro guardado por etiqueta `siniestro-sismo`. El agente lo crea una
vez en la UI y le queda fijo en el menu lateral. Segunda vista con
`siniestro-trabado` para la atencion urgente.

Ventaja: el hilo del cliente sigue siendo uno solo.
Revisar si: siniestros pasa a atenderlo gente distinta que no debe ver cartera.

### 2. Agente que mueve etiquetas (A CONSTRUIR)

Cron cada 2 dias. Por cada conversacion con `siniestro-sismo`:

1. Lee los mensajes desde la ultima revision.
2. `get_llm("judge").with_structured_output(...)` -> `{etapa, bloqueo, hubo_movimiento, confianza}`.
   Mismo patron que `app/features/metrics/quality.py::audit_conversation`.
3. Llama a `ChatwootClient.set_stage_label(conv_id, etiqueta, "siniestro-")`
   (ya existe, 5 tests en `test_chatwoot_stage_label.py`).

**Cuatro reglas no negociables** — el modelo toca datos con los que DPG decide:

- **Solo avanza o marca trabado, nunca retrocede.** Si lee "perito" y la
  conversacion ya esta en "ofrecimiento", no la devuelve. Un error de lectura no
  puede borrar progreso real.
- **Confianza baja no cambia nada.** Deja nota privada (`post_private_note`) y ahi
  queda, para que un humano mire.
- **`siniestro-sismo` es intocable.**
- **Cada cambio al audit log** con la frase del cliente que lo justifico. Si DPG
  pregunta por que un caso paso a perito, hay respuesta.

**SoftSeguros gana sobre el modelo**: el mismo cron refresca contra
`list_paginado`. Si el sistema dice PAGADO, eso manda sobre cualquier
interpretacion de la conversacion.

**Arranque en modo silencioso**: primera corrida escribe lo que HARIA sobre las 145
conversaciones existentes, sin aplicar. Se compara contra la realidad y ahi si se
activa la escritura.

### 3. Conversacion agentica, sin botones (DECIDIDO)

El cliente escribe en sus palabras, el modelo deriva la etapa. NO botones.

- Plantilla cada 3 dias: "Cuentanos en tus palabras como va tu reclamacion: que has
  hecho, que te respondio la aseguradora y que te tiene detenido."
- Extraccion estructurada del relato -> etapa + bloqueo + resumen + confianza.
- Confianza baja: UNA pregunta de aclaracion en lenguaje natural, maximo dos
  vueltas, luego humano.
- Con la etapa clara, responde el "que sigue" desde el KB y ofrece pasar a persona.
- Bloqueo de tipo aseguradora o perito: abre tarea para DPG sin que el cliente pida.

**En codigo, no en prompt**: nunca afirma montos, no promete indemnizacion, no
confirma nada con la aseguradora. Judge y output firewall aplican igual, con dos
patrones nuevos prohibidos: promesas de pago y plazos que no esten en el KB.

### 4. Informe cada 3 dias

Job ARQ, mismo dia que reenvia la plantilla. Agregacion determinista desde
`db.cases` (casos por etapa, dias en etapa, sin respuesta, bloqueos por tipo) y
encima un parrafo ejecutivo del modelo con los 3-4 casos donde DPG debe intervenir
hoy. Formato: el del artifact publicado 15-sep.

Quien destraba, por tipo de bloqueo:
- documentos -> nudge al cliente
- aseguradora / perito -> DPG presiona a la compania
- sin respuesta 2 ciclos -> llamada

---

## Donde vive el codigo

| Pieza | Ubicacion | Estado |
|---|---|---|
| `set_stage_label` | `app/integrations/chatwoot.py` | HECHO + 5 tests |
| Etiquetas en Chatwoot | cuenta DPG | HECHAS (7) |
| Atributos de contacto | cuenta DPG | HECHOS (9) |
| Lectura de etapa | `app/features/siniestros/stages.py` | HECHO + 15 tests |
| Barrido | `app/features/siniestros/sync.py` | HECHO + 7 tests |
| Cron cada 2 dias | `app/worker.py` (`sync_siniestro_stages`) | HECHO (dias impares 06:00 UTC) |
| Estado del caso | `db.cases` (L3, ya existe) | Reusar, sin tabla nueva |
| Enrutador de respuestas | `app/features/siniestros/routing.py` | HECHO + 27 tests, conectado al webhook |
| Plantillas Meta (2) | `seguimiento_siniestro_sismo_v2`, `deteccion_siniestro_sismo` | ENVIADAS 19-sep, PENDING |
| Script de envio | `scripts/broadcast_siniestros.py` | HECHO, dry-run OK (154 + 309) |
| Informe cada 3 dias | `app/worker.py` | FALTA |

## Las dos campanas (textos DPG, 18-sep — sin opciones numeradas)

| Campana | A quien | Marca Chatwoot | Estados que asigna el enrutador |
|---|---|---|---|
| seguimiento | 154 con reclamacion (`lista_terremoto.csv`) | `siniestro-sismo` | sin-radicar, radicada-esperando, en-ajuste, indemnizada, negada-problema |
| deteccion | 309 vigentes sin info (`lista_sin_info.csv`) | `deteccion-sismo` | afectado-sin-reportar, afectado-reportado, sin-afectacion, requiere-orientacion |

Reglas del enrutador: la marca decide el clasificador; sin marca no toca nada;
el estado anterior se reemplaza; `negada-problema`, `afectado-sin-reportar` y
`requiere-orientacion` agregan `escalado-humano`; quien en deteccion dice que ya
reporto recibe ademas `siniestro-sismo` y entra a seguimiento. Un digito suelto
NO clasifica (los mensajes no ofrecen menu).

**Envio ejecutado 19-sep-2026 (~13:40-14:10 Bogota):** seguimiento 154/154,
deteccion 309/309, 0 fallos del script. Meta reporto 36 entregas fallidas
(14 sin WhatsApp, 15 tope de engagement de Meta, 6 numero en experimento, 1
opt-out) -> `no_entregados.csv` (gitignored) + etiqueta `no-entregado` en los
36 hilos. Los de tope/experimento se pueden reintentar en unos dias; los sin
WhatsApp y el opt-out van a llamada. Estados de Meta pueden seguir llegando
horas despues: re-consultar con el filtro
`@event:"webhook.status.received" @status:failed` en Railway.

Comandos usados:
```
python scripts/broadcast_siniestros.py status
python scripts/broadcast_siniestros.py send seguimiento lista_terremoto.csv --limit 10
python scripts/broadcast_siniestros.py send deteccion lista_sin_info.csv --limit 10
```

**Desplegado a produccion el 27-sep-2026** (web + worker, commits 5c4263b..8ad6764,
CI en verde). Incluye la ventana de 24 h para mensajes del equipo
(`app/features/escalation/window.py`): fuera de ventana el mensaje del agente se
guarda, sale la plantilla `respuesta_pendiente_asesor` (APPROVED) con boton
"Ver respuesta", y se entrega cuando el cliente responde. Cron diario
`watch_pending_queues` avisa de colas con pendientes de 3+ dias. El Redis de
Railway SI tiene volumen persistente (`redis-volume`, /data): las colas y mutes
sobreviven reinicios.

**Para activar la escritura**: `SINIESTROS_SYNC_APPLY=true` en Railway. Sin esa
var el cron corre en silencioso y solo deja en el log lo que haria.

Fuente de verdad del informe: `db.cases`. Chatwoot es el espejo para el humano.
Si discrepan, manda la base.

---

## Riesgo principal

El modelo lee conversaciones reales y de ahi sale una etiqueta que DPG usa para
priorizar. Si se equivoca, un caso urgente queda mal clasificado. Mitigacion: las
cuatro reglas de arriba + arranque silencioso + SoftSeguros gana sobre el modelo.

## Fuera de alcance

OCR de comprobantes, integracion directa con aseguradoras, dashboard nuevo,
difusion disparada desde Chatwoot (el envio sigue por `broadcast_conversatorio.py`
con plantilla aprobada por Meta).
