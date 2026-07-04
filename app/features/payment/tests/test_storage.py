"""Tests for app.features.payment.storage (D-01, D-03, D-26).

All tests use ``tmp_path`` and monkeypatch ``settings.payment.volume_path``
so no disk write touches the real Railway volume directory.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def patched_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``settings.payment.volume_path`` at ``tmp_path`` for isolation."""
    from app.config.settings import settings

    monkeypatch.setattr(settings.payment, "volume_path", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers — minimal valid bytes per magic-byte spec
# ---------------------------------------------------------------------------

_JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 100
_PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_PDF_HEADER = b"%PDF-1.4" + b"\x00" * 100


# ---------------------------------------------------------------------------
# build_attachment_path
# ---------------------------------------------------------------------------


class TestBuildAttachmentPath:
    def test_path_layout(self, patched_volume: Path) -> None:
        """Path follows ``<volume>/<case_id>/<ts>-<wamid>.<ext>`` layout."""
        from app.features.payment.storage import build_attachment_path

        fixed_now = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        p = build_attachment_path(
            case_id="abc123", wamid="WA1", mime="image/jpeg", now=fixed_now
        )
        assert p.parent == patched_volume / "abc123"
        assert p.name == "20250115T103000Z-WA1.jpg"

    def test_pdf_ext(self, patched_volume: Path) -> None:
        from app.features.payment.storage import build_attachment_path

        p = build_attachment_path("caseX", "WA2", "application/pdf")
        assert p.suffix == ".pdf"

    def test_png_ext(self, patched_volume: Path) -> None:
        from app.features.payment.storage import build_attachment_path

        p = build_attachment_path("caseX", "WA3", "image/png")
        assert p.suffix == ".png"

    def test_unsupported_mime_raises(self, patched_volume: Path) -> None:
        from app.features.payment.storage import build_attachment_path

        with pytest.raises(ValueError, match="unsupported_mime_type"):
            build_attachment_path("caseX", "WA4", "video/mp4")

    def test_wamid_path_traversal_sanitized(self, patched_volume: Path) -> None:
        """Dots and slashes are stripped from wamid (T-04-04-03)."""
        from app.features.payment.storage import build_attachment_path

        p = build_attachment_path("caseX", "../../../etc/passwd", "image/jpeg")
        # The filename must not contain separators
        assert "/" not in p.name
        assert "\\" not in p.name
        assert ".." not in p.name


# ---------------------------------------------------------------------------
# store_attachment
# ---------------------------------------------------------------------------


class TestStoreAttachment:
    def test_atomic_write_creates_file_and_hash(self, patched_volume: Path) -> None:
        """Valid JPEG bytes → file on disk, correct sha256."""
        from app.features.payment.storage import store_attachment

        path, sha = store_attachment(_JPEG_HEADER, "case1", "WA10", "image/jpeg")
        assert path.exists()
        assert sha == hashlib.sha256(_JPEG_HEADER).hexdigest()

    def test_no_partial_file_left_after_write(self, patched_volume: Path) -> None:
        """The ``.partial`` sibling must not exist after a successful write."""
        from app.features.payment.storage import store_attachment

        path, _ = store_attachment(_JPEG_HEADER, "case2", "WA11", "image/jpeg")
        partial = path.with_suffix(path.suffix + ".partial")
        assert not partial.exists()

    def test_creates_case_subdirectory(self, patched_volume: Path) -> None:
        """``store_attachment`` creates the ``<volume>/<case_id>/`` dir."""
        from app.features.payment.storage import store_attachment

        case_id = "new-case-uuid"
        store_attachment(_JPEG_HEADER, case_id, "WA20", "image/jpeg")
        assert (patched_volume / case_id).is_dir()

    def test_rejects_magic_byte_mismatch(self, patched_volume: Path) -> None:
        """PE header declared as JPEG → ValueError, no file written."""
        from app.features.payment.storage import store_attachment

        bad_bytes = b"MZ\x90\x00" + b"\x00" * 100
        with pytest.raises(ValueError, match="magic_byte_mismatch"):
            store_attachment(bad_bytes, "case3", "WA30", "image/jpeg")

        # No file must have been created
        assert not any((patched_volume / "case3").glob("*")) if (
            patched_volume / "case3"
        ).exists() else True

    def test_rejects_oversize(self, patched_volume: Path) -> None:
        """Data exceeding 5 MB → ValueError("attachment_too_large")."""
        from app.features.payment.storage import store_attachment

        oversized = b"\xff\xd8\xff" + b"\x00" * (5 * 1024 * 1024 + 1)
        with pytest.raises(ValueError, match="attachment_too_large"):
            store_attachment(oversized, "case4", "WA40", "image/jpeg")

    def test_rejects_disallowed_mime(self, patched_volume: Path) -> None:
        """Disallowed MIME → ValueError from build_attachment_path."""
        from app.features.payment.storage import store_attachment

        with pytest.raises(ValueError):
            store_attachment(b"\xff\xd8\xff" + b"\x00" * 100, "case5", "WA50", "video/mp4")

    def test_pdf_roundtrip(self, patched_volume: Path) -> None:
        """Valid PDF bytes → correct path and hash."""
        from app.features.payment.storage import store_attachment

        path, sha = store_attachment(_PDF_HEADER, "case6", "WA60", "application/pdf")
        assert path.suffix == ".pdf"
        assert sha == hashlib.sha256(_PDF_HEADER).hexdigest()
