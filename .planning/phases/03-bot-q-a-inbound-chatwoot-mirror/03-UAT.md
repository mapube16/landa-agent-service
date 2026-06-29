---
status: complete
phase: 03-bot-q-a-inbound-chatwoot-mirror
source:
  - 03-00-SUMMARY.md
  - 03-01-SUMMARY.md
  - 03-02-SUMMARY.md
  - 03-03-SUMMARY.md
  - 03-04-SUMMARY.md
  - 03-05-SUMMARY.md
started: "2026-06-29T21:50:00Z"
updated: "2026-06-29T21:52:00Z"
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: |
  El servicio arranca limpio desde cero. Logs muestran lifespan.startup →
  KB audit risk=0 → lifespan.startup.complete → Uvicorn running, sin
  excepciones. agent-worker arranca con functions=[mirror_inbound,
  mirror_outbound]. Mandar "hola" devuelve el saludo T-01.
result: pass

### 2. Identificación con documento válido
expected: |
  Mandar "hola" → bot pide documento. Mandar documento DPG válido →
  bot manda "Buscando..." y después una lista interactiva con las
  pólizas (botón "Elegir póliza", hasta 10 filas con POL-XXX).
result: pass

### 3. Identificación con documento inexistente
expected: |
  Documento que NO existe en SoftSeguros → bot responde "No encontré
  ningún cliente con ese número de documento... ¿Podés verificar que
  el número esté correcto?" Sin escalar, permite reintentar.
result: pass

### 4. Selección de póliza vía tap en lista
expected: |
  Tap en una fila de la lista → bot responde "Listo, sobre la póliza
  POL-XXX. ¿Qué querés saber?" con 3 botones: Saldo / Estado / Coberturas.
result: pass

### 5. Q&A con tools (Saldo / Estado / Coberturas)
expected: |
  Tocar botón o tipear "saldo"/"estado"/"coberturas" → bot llama al
  tool de SoftSeguros y responde con el dato. Después aparecen 3
  botones (Saldo / Estado / Hablar humano) para seguir.
result: pass

### 6. Pregunta fuera de scope
expected: |
  Pregunta no relacionada con seguros → bot rechaza elegante sin
  escalar y vuelve a ofrecer ayuda con la póliza.
result: pass

### 7. Escape hatch ("Hablar humano")
expected: |
  Botón "Hablar humano" o palabra "agente"/"humano" → bot manda T-08
  inmediato (zero LLM cost), conversación queda en escalating.
result: pass

### 8. Reset command (volver al inicio)
expected: |
  "hola"/"reiniciar"/"menu" en cualquier punto → checkpoint borrado,
  bot pide documento desde cero.
result: pass

### 9. Chatwoot mirror end-to-end
expected: |
  Toda la conversación espejada en Chatwoot inbox `landa-agent-mirror`,
  asignada al agente, visible en web y móvil. Una sola conversación
  por cliente (sin duplicados).
result: pass

## Summary

total: 9
passed: 9
issues: 0
pending: 0
skipped: 0

## Gaps

[none — all tests validated live in production during smoke session 2026-06-29]
