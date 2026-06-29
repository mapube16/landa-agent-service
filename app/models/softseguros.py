"""Pydantic-friendly types for SoftSeguros REST responses (Phase 2).

In F2 we do NOT model individual fields because we don't have a real
captured response from DPG yet (RESEARCH Open Question #1 + Assumption
A1). Passthrough as ``dict[str, Any]`` keeps the contract honest: the
F2 test endpoint ``/test/poliza/{poliza_id}`` returns raw JSON for
operator inspection.

F3 (when QA tools land) will replace this alias with concrete Pydantic
models per endpoint, derived from real responses captured during F2
smoke-testing.
"""

from __future__ import annotations

from typing import Any

# Deliberate passthrough alias — see module docstring. The F2 SoftSeguros
# client returns this from every public read method. F3 will narrow.
PolizaRaw = dict[str, Any]


__all__ = ["PolizaRaw"]
