"""Comprobante attachment constraints (D-24/D-25/D-26).

Size cap + mime allowlist + magic-byte validation + extension blocklist for
payment receipts.  Downstream payment nodes MUST gate on
:func:`validate_magic_bytes` AND :func:`has_blocked_extension` before any disk
write; comprobante bytes NEVER reach an LLM (D-27).

Defense layering (RESEARCH Pattern 7 — belt-and-suspenders):
1. MIME allowlist — ``ALLOWED_MIME_TYPES`` rejects unknown content types outright.
2. Magic-byte check — ``validate_magic_bytes`` rejects spoofed content types
   (e.g. .exe declared as image/jpeg).
3. Extension blocklist — ``has_blocked_extension`` + ``BLOCKED_EXTENSIONS``
   rejects executable filenames regardless of declared MIME.  Belt-and-suspenders
   for the magic-byte primary control.

Size cap: ``ATTACHMENT_MAX_BYTES = 5 MB`` (D-25).  This intentionally
under-cuts the ROADMAP 10 MB ceiling — the conservative choice is documented
here and in ADR-005. Revisit when attachment volume or threat model changes.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final

ATTACHMENT_MAX_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MB per D-25 (conservative vs ROADMAP 10 MB)

ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "application/pdf"}
)  # D-24

# Executable / script extensions blocked regardless of declared MIME (D-26, RESEARCH Pattern 7).
# This is belt-and-suspenders alongside the magic-byte primary control.
BLOCKED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".exe",
        ".js",
        ".sh",
        ".bat",
        ".cmd",
        ".dll",
        ".msi",
        ".scr",
        ".ps1",
        ".jar",
    }
)

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


def has_blocked_extension(filename: str | None) -> bool:
    """Return True iff ``filename`` ends with a blocked executable extension.

    Case-insensitive.  ``None`` returns False (no filename = extension check
    skipped; magic-byte check remains the primary control).

    This is belt-and-suspenders alongside :func:`validate_magic_bytes`.
    """
    if filename is None:
        return False
    suffix = PurePosixPath(filename).suffix.lower()
    return suffix in BLOCKED_EXTENSIONS


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
