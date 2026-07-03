"""Comprobante attachment constraints (D-24/D-25/D-26).

Size cap + mime allowlist + magic-byte validation for payment receipts.
Downstream payment nodes MUST gate on :func:`validate_magic_bytes` before
any disk write; comprobante bytes NEVER reach an LLM (D-27).
"""

from __future__ import annotations

from typing import Final

ATTACHMENT_MAX_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MB per D-25

ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "application/pdf"}
)  # D-24


def validate_magic_bytes(first_chunk: bytes, declared_mime: str) -> bool:
    """Return True iff ``first_chunk`` matches the declared (allowed) mime."""
    raise NotImplementedError
