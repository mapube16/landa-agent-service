---
phase: 03-bot-q-a-inbound-chatwoot-mirror
plan: "01"
subsystem: config + models + qa-feature + security + integrations
tags:
  - langgraph
  - pydantic
  - chatwoot
  - skeletons
  - contracts
dependency_graph:
  requires:
    - 03-00  # Wave 0 probe confirmed ClienteRaw shape + Chatwoot Channel::Api + structured output
  provides:
    - app/config/settings.py::ChatwootSettings
    - app/models/softseguros.py::{ClienteRaw,EstadoCodigo,SaldoResponse,EstadoResponse,CoberturasResponse,PolizaSummary}
    - app/features/qa/state.py::QAState
    - app/features/qa/messages.py::{T_01..T_08,ESCAPE_REGEX,interpolate_t04}
    - app/security/judge.py::JudgeRubric
    - 13 additional module skeletons (raise NotImplementedError, implemented in 03-02..03-05)
  affects:
    - tests/conftest.py (CHATWOOT_* env vars added)
    - .pre-commit-config.yaml (LangGraph/LangChain/LangSmith mypy deps)
tech_stack:
  added:
    - langgraph==1.2.6 (pre-commit mypy env)
    - langchain==1.3.11 (pre-commit mypy env)
    - langsmith==0.9.3 (pre-commit mypy env)
  patterns:
    - TypedDict + Annotated[list[BaseMessage], add_messages] for LangGraph state
    - StrEnum for estado codes (ruff UP042 compliance)
    - InjectedState pattern declared in tools.py skeletons
    - SecretStr for ChatwootSettings.api_key (same as WA/SoftSeguros pattern)
key_files:
  created:
    - app/config/settings.py  # ChatwootSettings added
    - app/models/softseguros.py  # ClienteRaw TypedDict + EstadoCodigo + 4 DTOs
    - app/features/qa/state.py
    - app/features/qa/graph.py
    - app/features/qa/nodes.py
    - app/features/qa/tools.py
    - app/features/qa/prompts.py
    - app/features/qa/knowledge_base.py
    - app/features/qa/messages.py
    - app/security/prompt_firewall.py
    - app/security/judge.py
    - app/security/kb_auditor.py
    - app/integrations/chatwoot.py
    - knowledge/dpg_cartera.md
  modified:
    - app/config/settings.py
    - app/models/softseguros.py
    - tests/conftest.py
    - .env.example
    - .pre-commit-config.yaml
decisions:
  - "ClienteRaw narrowed to TypedDict (20 fields) per 03-00 probe Task 1 — extra='ignore' pattern"
  - "EstadoCodigo uses StrEnum (UP042) not str+Enum per ruff enforcement"
  - "JudgeRubric 8-flag schema locked as data contract (D-05) — not a stub, shape is final"
  - "T_01..T_08 string literals written as parenthesized implicit concat to satisfy ruff-format+black simultaneously (both configured at line-length=100, em-dashes in comments replaced with -- per ruff)"
  - "ESCAPE_REGEX (D-15 Layer 1) implemented as final data in messages.py — not a stub"
  - ".env.example already had Chatwoot stubs from F1; updated with richer comments explaining API Channel vs WhatsApp inbox"
metrics:
  duration: "~90 minutes"
  completed: "2026-06-29"
  tasks_completed: 2
  tasks_total: 2
  files_created: 14
  files_modified: 5
---

# Phase 03 Plan 01: Foundation Contracts + Skeletons Summary

Wave 1 foundation: all contracts and skeletons that Wave 2+ plans consume.
`ChatwootSettings`, narrowed `ClienteRaw` TypedDict + `EstadoCodigo` StrEnum,
`QAState` TypedDict for LangGraph, D-16 template data in `messages.py`,
`JudgeRubric` 8-flag locked schema, 13 module skeletons (raise
NotImplementedError), 338-word KB stub, conftest Chatwoot env vars, pre-commit
mypy deps updated for LangGraph/LangChain.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | ChatwootSettings + ClienteRaw + EstadoCodigo + sanitized DTOs + .env.example | `2385854` | app/config/settings.py, app/models/softseguros.py, tests/conftest.py, .env.example |
| 2 | QAState + 14 module skeletons + KB stub + pre-commit deps | `944df21` | 13 new files + .pre-commit-config.yaml |

## What Was Built

### Task 1

**`ChatwootSettings`** (`app/config/settings.py`): new `BaseSettings` subclass with `env_prefix="CHATWOOT_"`, fields `url: str`, `api_key: SecretStr`, `account_id: int`, `inbox_id: int`. Root `Settings.chatwoot` wired with `Field(default_factory=ChatwootSettings)`.

**`ClienteRaw`** (`app/models/softseguros.py`): `TypedDict` narrowed to 20 fields per 03-00 probe Task 1 findings (122-field endpoint → kept id/nombres/apellidos/numero_documento/celular/email/activo/etc.). `extra="ignore"` on Pydantic consumers.

**`EstadoCodigo`** (`app/models/softseguros.py`): `StrEnum` (ruff UP042) with 8 values from `/api/estadopoliza/` probe — Vigente, Cotizacion, Devengada, Expedicion, No renovada, Cancelada, Nueva, Vencida.

**Sanitized DTOs**: `SaldoResponse`, `EstadoResponse`, `CoberturasResponse` (with `Cobertura` submodel), `PolizaSummary` — all `BaseModel(extra="ignore")` with only the allowlisted fields per CONTEXT.md "Tool output sanitization allowlist".

**`tests/conftest.py`**: 4 Chatwoot dummy env vars added to `_test_env` fixture.

**`.env.example`**: existing Chatwoot block upgraded with detailed comments explaining "API Channel" vs WhatsApp inbox distinction.

### Task 2

**`QAState`** (`app/features/qa/state.py`): `TypedDict` with `messages: Annotated[list[BaseMessage], add_messages]`, `poliza_id: str | None`, `cliente_doc: str | None`, `polizas_list: list[dict[str, Any]]`, `doc_retries: int`, `judge_retries: int`, `node: Literal[5-nodes]`. Literal nodes: `awaiting_identification`, `awaiting_policy_choice`, `answering_qa`, `escalating`, `closed`.

**`app/features/qa/messages.py`** — DATA not stubs:
- T_01..T_08 locked per D-16 (Spanish colombiano informal, emoji in T-01)
- `ESCAPE_REGEX` = Layer 1 escape hatch per D-15
- `interpolate_t04(n, lista_numerada)` helper
- All strings as parenthesized implicit concat to satisfy ruff-format+black both at line-length=100

**`app/security/judge.py`** — schema locked as data contract (not stub):
- `JudgeRubric` with 8 required bool fields + `rationale: str` (D-05, confirmed feasible in 03-00 probe Task 3)

**KB stub** (`knowledge/dpg_cartera.md`): 338 words, 4 sections (## Coberturas generales, ## FAQs frecuentes, ## Procedimientos de cartera, ## Horarios de atención). DPG-coherent placeholder content. Risk-benign (no injection patterns, no hidden chars).

**Module skeletons** (all `raise NotImplementedError("Implemented in Plan 03-NN")`):
- `app/features/qa/graph.py` → 03-05
- `app/features/qa/nodes.py` (5 async functions) → 03-05
- `app/features/qa/tools.py` (4 tools with `@tool` + `InjectedState`) → 03-05
- `app/features/qa/prompts.py` → 03-05
- `app/features/qa/knowledge_base.py` (with `@lru_cache(maxsize=1)`) → 03-05
- `app/security/prompt_firewall.py` (SanitizeResult dataclass) → 03-04
- `app/security/kb_auditor.py` (KBAuditRubric + audit_kb) → 03-04
- `app/integrations/chatwoot.py` (ChatwootClient + get_chatwoot_client) → 03-03

**`.pre-commit-config.yaml`**: added `langgraph==1.2.6`, `langchain==1.3.11`, `langsmith==0.9.3` to mypy `additional_dependencies`.

## Verification Results

- `uv run mypy --strict app/` — Success: no issues found in 41 source files
- `uv run pytest` — 66 passed, 3 warnings (existing baseline, no regression)
- `uv run ruff check .` — All checks passed
- `grep -c "^CHATWOOT_" .env.example` = 4
- `python -c "from app.features.qa.messages import T_01; assert 'documento' in T_01"` — passes
- `python -c "from app.features.qa.messages import ESCAPE_REGEX; assert ESCAPE_REGEX.search('quiero hablar con un humano')"` — passes
- `python -c "from app.security.judge import JudgeRubric; flags = [f for f, info in JudgeRubric.model_fields.items() if info.annotation is bool]; assert len(flags) == 8"` — passes
- `grep -c "^## " knowledge/dpg_cartera.md` = 4
- `wc -w knowledge/dpg_cartera.md` = 338 words (in range 250-500)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ruff E501 line length on template strings**
- **Found during:** Task 2 commit (pre-commit hook)
- **Issue:** T_01 (`¡Hola! 👋...`) is 96-char content + assignment = 111 chars, over `line-length=100`. ruff-format and black disagreed on wrap style (formatter loop).
- **Fix:** Split as parenthesized implicit string concatenation on natural word boundaries. Both ruff-format and black agree on the split form.
- **Files modified:** `app/features/qa/messages.py`
- **Commit:** included in `944df21`

**2. [Rule 1 - Bug] ruff UP042 — `str, Enum` deprecated**
- **Found during:** Task 1 commit (pre-commit hook)
- **Issue:** `class EstadoCodigo(str, Enum)` triggers ruff UP042; Python 3.11+ prefers `StrEnum`.
- **Fix:** Changed to `class EstadoCodigo(StrEnum)` with `from enum import StrEnum`.
- **Files modified:** `app/models/softseguros.py`
- **Commit:** included in `2385854`

**3. [Rule 1 - Bug] ruff-format strips em-dashes/arrows from comments**
- **Found during:** Task 2 commit iteration
- **Issue:** ruff-format replaces `—` with `--` and `→` with `->` in docstrings/comments when normalizing. After ruff-format, black would re-wrap; they looped.
- **Fix:** Pre-emptively used `--` and `->` in new file comments; let ruff-format run before final staging.
- **Files modified:** `app/features/qa/messages.py`
- **Commit:** included in `944df21`

**4. [Observation] `.env.example` Chatwoot block already existed**
- Plan called for adding the Chatwoot block; it was already present from F1 scaffolding with minimal comments.
- Action: Updated existing block with richer comments (API Channel explanation, account_id/inbox_id hints).
- No deviation in behavior, just additive improvement.

## Known Stubs

All stubs are intentional `raise NotImplementedError("Implemented in Plan 03-NN")` per the plan's purpose (contract locking before Wave 2 parallel execution). Complete list:

| File | Stub | Implemented by |
|------|------|----------------|
| `app/features/qa/graph.py` | `build_qa_graph()` | 03-05 |
| `app/features/qa/nodes.py` | 5 async node functions | 03-05 |
| `app/features/qa/tools.py` | 4 tool functions | 03-05 |
| `app/features/qa/prompts.py` | `system_prompt()` | 03-05 |
| `app/features/qa/knowledge_base.py` | `load_kb()` | 03-05 |
| `app/security/prompt_firewall.py` | `sanitize()` | 03-04 |
| `app/security/judge.py` | `is_approved()`, `judge_response()` | 03-04 |
| `app/security/kb_auditor.py` | `audit_kb()` | 03-04 |
| `app/integrations/chatwoot.py` | `ChatwootClient` methods + `get_chatwoot_client()` | 03-03 |

Data that is NOT stub (implemented/locked in this plan):
- `JudgeRubric` 8-flag schema in `app/security/judge.py`
- `T_01..T_08` strings + `ESCAPE_REGEX` + `interpolate_t04` in `app/features/qa/messages.py`
- `ClienteRaw` TypedDict (narrowed shape from 03-00 probe)
- `EstadoCodigo` StrEnum (8 values from probe)
- `KBAuditRubric` schema in `app/security/kb_auditor.py`

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes at trust boundaries introduced in this plan. All new files are skeletons (no runtime behavior) or data constants. `ChatwootSettings.api_key: SecretStr` enforces the T-03-01-01 mitigation (no plaintext logging). KB stub `knowledge/dpg_cartera.md` is benign by construction (T-03-01-02 mitigation).

## Self-Check: PASSED

All files created:
- `app/features/qa/state.py` FOUND
- `app/features/qa/graph.py` FOUND
- `app/features/qa/nodes.py` FOUND
- `app/features/qa/tools.py` FOUND
- `app/features/qa/prompts.py` FOUND
- `app/features/qa/knowledge_base.py` FOUND
- `app/features/qa/messages.py` FOUND
- `app/security/prompt_firewall.py` FOUND
- `app/security/judge.py` FOUND
- `app/security/kb_auditor.py` FOUND
- `app/integrations/chatwoot.py` FOUND
- `knowledge/dpg_cartera.md` FOUND

Commits exist:
- `2385854` (Task 1) FOUND
- `944df21` (Task 2) FOUND
