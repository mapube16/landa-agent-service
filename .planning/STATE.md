---
gsd_state_version: 1.0
milestone: v21.0
milestone_name: milestone
status: phase-03-stabilized
last_updated: "2026-06-29T20:15:00.000Z"
progress:
  total_phases: 8
  completed_phases: 0
  total_plans: 23
  completed_plans: 15
  percent: 0
notes:
  - 2026-06-29 Phase 03 conversational flow estable contra prod (DPG SoftSeguros + Meta + Chatwoot).
  - 13 bugs cazados en smoke en vivo y arreglados. Detalle completo en .planning/phases/03-bot-q-a-inbound-chatwoot-mirror/03-06-SMOKE.md §Live Smoke Findings.
  - Bug operacional grande - agent-worker no auto-deploya con git push; usar `railway up --service agent-worker --ci --detach` tras cambios en app/worker.py.
  - Next - completar validación de Chatwoot mirror con mensaje real, después arrancar WhatsApp Interactive Messages (botones + lista) sobre la base estable.
---
