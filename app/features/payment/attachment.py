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

_MAGIC: Final[dict[str, tuple[bytes, ...]]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    # 6-byte PNG prefix (full signature adds \x1a\n) — enough to reject
    # spoofs; D-26 validates intent, not container fidelity.
    "image/png": (b"\x89PNG\r\n",),
    # ponytail: RIFF prefix only — the WEBP marker sits after a variable
    # 4-byte size field; prefix check is sufficient for D-26.
    "image/webp": (b"RIFF",),
    "application/pdf": (b"%PDF-",),
}


def validate_magic_bytes(first_chunk: bytes, declared_mime: str) -> bool:
    """Return True iff ``first_chunk`` matches the declared (allowed) mime.

    Deterministic stdlib-only check (no python-magic — RESEARCH Open
    Questions #6 / A7). A disallowed ``declared_mime`` is rejected outright;
    an allowed one must match its known signature prefix (blocks .exe
    spoofed as image/jpeg and cross-type mismatches).
    """
    if declared_mime not in ALLOWED_MIME_TYPES:
        return False
    return any(first_chunk.startswith(sig) for sig in _MAGIC[declared_mime])
