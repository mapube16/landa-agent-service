"""AuditPayload — flat-primitives validation model for audit log entries.

Enforces that payloads passed to ``audit_log.emit()`` contain only scalar
types (str, int, bool, None). Floats are rejected because they serialize
non-deterministically across Python versions, which would break the
SHA-256 hash chain (RESEARCH Pitfall 4).

Monetary amounts MUST be cast to int cents or str before auditing:
    Good: {"amount_cents": 12345}   # int cents
    Good: {"amount": "123.45"}      # str
    Bad:  {"amount": 123.45}        # float — rejected with ValidationError
"""

from __future__ import annotations

from pydantic import RootModel

# Permitted leaf types: str, int, bool, None (no float, no nested structures).
# Note: bool must come BEFORE int in the Union because bool is a subclass of int
# in Python; Pydantic v2 checks discriminators left-to-right.
AuditPayload = RootModel[dict[str, str | int | bool | None]]

__all__ = ["AuditPayload"]
