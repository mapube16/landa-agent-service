# Agente de WhatsApp para Cartera DPG

## What This Is

Un agente de WhatsApp para DPG Seguros que continúa la conversación con el deudor justo después de que el bot de voz (Pipecat, ya existente) termina la llamada. Cuando el deudor dice que ya pagó, el agente pide el comprobante, lo reenvía al número de WhatsApp de cartera ya existente para validación humana, y según la respuesta de cartera cierra la conversación con el cliente (pago confirmado) o la escala a Chatwoot para que un humano la continúe (pago inválido o el cliente pidió ayuda humana). Toda la interacción queda registrada en Chatwoot para trazabilidad.

## Core Value

Que cartera deje de cambiar entre chats y tareas manuales: solo confirma sí/no sobre un comprobante, y el agente se encarga de cerrar o escalar la conversación con el cliente automáticamente.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Bot de voz, al detectar "ya pagué" o solicitud de ayuda humana, deriva al deudor a continuar por WhatsApp (mismo caso/contexto)
- [ ] Agente de WhatsApp (número WhatsApp Business API de LANDA/DPG) le pide el comprobante de pago al deudor
- [ ] Agente reenvía el comprobante recibido al número de WhatsApp de cartera ya existente, dentro de un chat interno bot↔cartera
- [ ] Cartera responde en ese chat interno (pago válido / no válido) y el agente actúa en consecuencia
- [ ] Si pago válido: agente envía mensaje de agradecimiento/confirmación al deudor y cierra la conversación automáticamente
- [ ] Si pago inválido o inconsistente: agente avisa al deudor que un humano va a continuar, y notifica a cartera que debe entrar a Chatwoot a resolver el caso
- [ ] Si el deudor no contesta la llamada: bot envía automáticamente un WhatsApp informando que DPG intentó comunicarse por temas de su póliza
- [ ] Toda interacción (mensajes del bot, comprobante, decisión de cartera, escalación) queda registrada como conversación trazable en Chatwoot

### Out of Scope

- Validación automática/OCR del comprobante de pago — v1 depende de revisión humana de cartera, no de validación automática contra SoftSeguros
- Multi-tenant / soporte a otros clientes de cartera además de DPG — se construye específico para DPG por ahora, generalizar es trabajo futuro
- Dashboard nuevo de LANDA para revisión de comprobantes — la revisión ocurre vía el chat interno bot↔cartera en WhatsApp, no en un panel nuevo
- Construcción del bot de voz — se asume ya existente (Pipecat + Claude), este proyecto es el tramo de WhatsApp y su integración con la derivación de la llamada

## Context

- Cliente: DPG Seguros, caso de uso de cobranza (cartera).
- Ya existe un bot de voz (Pipecat + Claude para decisiones) que llama a deudores; este proyecto extiende ese flujo a WhatsApp.
- Cartera ya usa un número de WhatsApp propio (no Business API) para gestionar casos manualmente — ese número se reutiliza como canal de validación interna, no se reemplaza.
- Cuando un caso se escala a humano, el agente humano continúa la MISMA conversación de WhatsApp del bot dentro de Chatwoot (mismo número, sin perder contexto).
- Forma parte de la arquitectura más amplia de LANDA: número WhatsApp Business API `+16415416615` (actualmente vía Twilio, en plan de migración a Meta Cloud API directo), Chatwoot self-hosted en Railway como inbox de agentes.
- Este repo ES `landa-agent-service`: el microservicio separado (FastAPI) para agentes IA + voz, independiente de la plataforma core `lambda-proyect`. Chatwoot y el resto de integraciones de este agente viven aquí, no en `lambda-proyect`.
- Ver memoria del proyecto LANDA (arquitectura de dos repos, plan de migración WhatsApp, reglas de desarrollo) para contexto técnico más amplio que no se repite aquí.

## Constraints

- **Canal de validación interna**: el chat bot↔cartera ocurre sobre el número de WhatsApp normal que cartera ya usa — no se reemplaza por un dashboard nuevo en v1.
- **Trazabilidad**: toda conversación (incluida la interna con cartera y la del cliente) debe quedar visible/registrada en Chatwoot.
- **Validación humana**: la decisión sobre si un comprobante es válido la toma siempre un humano de cartera, no el sistema.
- **Stack**: sigue la arquitectura LANDA ya definida — agente vive en `landa-agent-service` (FastAPI), Chatwoot self-hosted en Railway, Claude para decisiones del bot.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Revisión de comprobante vía chat interno bot↔cartera en WhatsApp (no dashboard nuevo) | Cartera ya tiene ese número y flujo; evita construir UI nueva para v1 y reduce cambio de herramienta para el equipo | — Pending |
| Validación de comprobante 100% humana en v1 | Simplicidad y confiabilidad — automatizar OCR/validación contra SoftSeguros se evalúa después de validar el flujo base | — Pending |
| Escalación humana ocurre dentro de la misma conversación de WhatsApp en Chatwoot | Mantiene contexto completo para el agente humano, evita que el cliente repita información | — Pending |
| Alcance v1 limitado al tramo de WhatsApp (voz se asume ya construida) | El bot de voz ya existe; este proyecto es la pieza que falta para cerrar el ciclo cobranza-WhatsApp | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-27 after initialization*
