---
phase: 03-bot-q-a-inbound-chatwoot-mirror
plan: "04"
subsystem: security
tags:
  - prompt-firewall
  - llm-judge
  - kb-auditor
  - adversarial-fixtures
  - ci
dependency_graph:
  requires:
    - 03-01  # get_llm factory + QAState
  provides:
    - app/security/prompt_firewall.py::sanitize (SanitizeResult, INJECTION_PATTERNS)
    - app/security/judge.py::JudgeRubric (8 bool flags D-05)
    - app/security/judge.py::is_approved + judge_response
    - app/security/kb_auditor.py::audit_kb (5-layer, FAIL-CLOSED)
    - app/security/kb_auditor.py::KBAuditRubric (6 bool flags + deterministic score)
    - tests/security/ (29 tests)
    - tests/fixtures/kb_adversarial/ (7 fixtures, risk frontmatter)
    - .github/workflows/kb-audit.yml
  affects:
    - 03-05 (consumes sanitize, judge_response, is_approved, audit_kb in lifespan)
key_files:
  modified:
    - app/security/judge.py            # full implementation (was skeleton)
  created:
    - app/security/prompt_firewall.py
    - app/security/kb_auditor.py
    - tests/security/__init__.py
    - tests/security/test_prompt_firewall.py
    - tests/security/test_judge.py
    - tests/security/test_kb_auditor.py
    - tests/fixtures/kb_adversarial/01_ignore_previous.md
    - tests/fixtures/kb_adversarial/02_role_override.md
    - tests/fixtures/kb_adversarial/03_data_exfiltration.md
    - tests/fixtures/kb_adversarial/04_hidden_chars.md
    - tests/fixtures/kb_adversarial/05_pii_patterns.md
    - tests/fixtures/kb_adversarial/06_link_injection.md
    - tests/fixtures/kb_adversarial/07_clean_control.md
    - .github/workflows/kb-audit.yml
commit: 0753ce1
---

## What Was Built

### prompt_firewall.py — 5-step sanitization pipeline
1. NFKC normalize
2. Strip invisible chars (U+200B–200F, U+202A–202E, U+2060–2064, U+FEFF) via `chr(cp)` — no literal chars in source
3. Strip control chars `[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]`
4. Length cap 4000 → `blocked=True, reason="length_exceeded"` si excede
5. Match 12 OWASP injection patterns (case-insensitive) → `blocked=True, reason="pattern_match:<truncated>"`

`SanitizeResult(blocked, reason, cleaned)`. `INJECTION_PATTERNS` exported como `list[re.Pattern[str]]` (12 entries).

### judge.py — LLM-as-judge
`JudgeRubric(BaseModel)` con exactamente 8 bool flags (D-05):
`is_in_scope`, `leaks_other_polizas`, `affirms_payment_without_cartera_approval`, `factually_grounded`, `no_jailbreak_echo`, `no_pii_leak`, `no_external_links`, `sentiment_appropriate` + `rationale: str`.

`is_approved(rubric)` — True solo si 6 flags positivos son True Y 2 negativos son False.
`judge_response(messages, candidate)` — llama `get_llm("judge")` con `with_structured_output(JudgeRubric, strict=True)`, devuelve `JudgeRubric | None`. None tratado como reject por caller. Logs solo `rationale_len` (Pitfall 5 guard — no raw rationale en logs).

### kb_auditor.py — 5-layer audit pipeline
1. Hash cache (Redis, TTL 1h) — skip si KB no cambió
2. Static patterns — mismos INJECTION_PATTERNS del firewall
3. Diff — si KB fue modificado, analiza el diff contra baseline
4. LLM judge — `KBAuditRubric` (6 bool flags) via `get_llm("judge")`
5. Deterministic score — `compute_risk_score()` recomputa desde flag bools, no confía en el score reportado por LLM (T-AUTH-RUBRIC mitigation)

FAIL-CLOSED: `score > 50` → `sys.exit(1)`. CLI `__main__` entrypoint para CI + startup gate (03-05).

### Adversarial fixtures (7)
`tests/fixtures/kb_adversarial/` — cada archivo tiene YAML frontmatter `risk: <int>`.
Distribución: `01` ignore previous (risk:8), `02` role override (risk:7), `03` data exfiltration (risk:9), `04` hidden chars (risk:6), `05` PII patterns (risk:7), `06` link injection (risk:8), `07` clean control (risk:0).

### CI workflow
`.github/workflows/kb-audit.yml` — trigger en PR que toca `knowledge/dpg_cartera.md`, `app/security/kb_auditor.py`, o `app/security/prompt_firewall.py`. Redis service incluido. Falla build si exit != 0.

## Test Coverage
29 tests en `tests/security/`:
- `test_prompt_firewall.py` — 9 tests (happy path + cada step del pipeline)
- `test_judge.py` — incluyendo invariant test: exactamente 8 bool flags, rationale_len-only log assertion
- `test_kb_auditor.py` — parametrized sobre 7 fixtures + FAIL-CLOSED assertion + deterministic score recompute

## Key Decisions
- Invisible chars via `chr(cp)` (no literals en source) — T-INVISIBLE-LEAK mitigation
- `compute_risk_score()` determinístico desde flags — LLM no puede sub-reportar riesgo (T-AUTH-RUBRIC)
- `judge_response` devuelve `None` en cualquier fallo — caller trata como reject sin exception propagation
- `KBAuditRubric.risk_score` ignorado por FAIL-CLOSED; `compute_risk_score()` es autoritativo

## What 03-05 Consumes
```python
from app.security.prompt_firewall import sanitize, SanitizeResult
from app.security.judge import judge_response, is_approved, JudgeRubric
from app.security.kb_auditor import audit_kb  # lifespan startup gate
```
