"""Comprobante storage helper — deterministic path + atomic write (D-01, D-03, D-26).

Writes comprobante bytes to the Railway volume at a predictable path with an
atomic ``os.replace`` swap (partial-write protection). Magic-byte and size gates
fire before any disk write so invalid or oversized files never reach the volume.

Comprobante bytes NEVER reach an LLM (D-27). Only ``(path, sha256)`` are
returned; the node layer owns DB persistence so this module stays I/O-pure
(volume only) and is easy to monkeypatch in tests.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from app.config.settings import settings
from app.features.payment.attachment import ATTACHMENT_MAX_BYTES, validate_magic_bytes

# Map of declared mime-type → file extension.
MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}

# Allowlist for wamid characters in the filename (T-04-04-03 path-traversal mitigation).
_SAFE_WAMID_RE = re.compile(r"[^A-Za-z0-9_\-]")


def _sanitize_wamid(wamid: str) -> str:
    """Strip any character that could allow path traversal (T-04-04-03)."""
    return _SAFE_WAMID_RE.sub("", wamid)


def build_attachment_path(
    case_id: str,
    wamid: str,
    mime: str,
    now: datetime | None = None,
) -> Path:
    """Return the canonical volume path for a comprobante file.

    Args:
        case_id: UUID string for the payment case.
        wamid: Meta message ID (sanitised internally — no ``..`` or ``/``).
        mime: Declared MIME type; must be in ``MIME_TO_EXT`` or raises.
        now: UTC datetime for the filename timestamp (defaults to utcnow).

    Returns:
        ``Path(<volume_path>/<case_id>/<ts>-<wamid>.<ext>)``

    Raises:
        ValueError: if ``mime`` is not a supported MIME type.
    """
    ext = MIME_TO_EXT.get(mime)
    if ext is None:
        raise ValueError(f"unsupported_mime_type: {mime!r}")

    now = now or datetime.now(UTC)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    safe_wamid = _sanitize_wamid(wamid)

    volume = settings.payment.volume_path
    return volume / case_id / f"{ts}-{safe_wamid}.{ext}"


def store_attachment(
    data: bytes,
    case_id: str,
    wamid: str,
    declared_mime: str,
) -> tuple[Path, str]:
    """Validate, write, and return ``(final_path, sha256_hex)`` for a comprobante.

    Validation order:
    1. Size gate — raises ``ValueError("attachment_too_large")`` if exceeded.
    2. Magic-byte gate — raises ``ValueError("magic_byte_mismatch")`` on mismatch.
    3. Atomic write: ``.partial`` sibling → ``os.replace`` to final path.

    No disk write occurs if either gate fires.

    Args:
        data: Raw file bytes (already downloaded from Meta CDN).
        case_id: UUID string for the payment case.
        wamid: Meta message ID used in the filename.
        declared_mime: MIME type declared by the sender.

    Returns:
        ``(final_path, sha256_hex)`` — path on Railway volume, hex digest.

    Raises:
        ValueError: ``"attachment_too_large"`` or ``"magic_byte_mismatch"`` on gate fail.
    """
    if len(data) > ATTACHMENT_MAX_BYTES:
        raise ValueError("attachment_too_large")

    if not validate_magic_bytes(data[:16], declared_mime):
        raise ValueError("magic_byte_mismatch")

    final = build_attachment_path(case_id, wamid, declared_mime)
    final.parent.mkdir(parents=True, exist_ok=True)

    partial = final.with_suffix(final.suffix + ".partial")
    partial.write_bytes(data)
    os.replace(partial, final)

    sha = hashlib.sha256(data).hexdigest()
    return (final, sha)


__all__ = ["MIME_TO_EXT", "build_attachment_path", "store_attachment"]
