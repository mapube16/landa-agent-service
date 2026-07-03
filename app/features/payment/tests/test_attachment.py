"""Tests for app.features.payment.attachment (D-24/D-25/D-26).

Pure-function magic-byte validator — no I/O, no env vars needed.
"""

from __future__ import annotations


class TestValidateMagicBytes:
    def test_jpeg_magic_accepted(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"\xff\xd8\xff\xe0\x00\x10", "image/jpeg") is True

    def test_png_magic_accepted(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"\x89PNG\r\n", "image/png") is True

    def test_webp_riff_prefix_accepted(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8", "image/webp") is True

    def test_pdf_magic_accepted(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"%PDF-1.4", "application/pdf") is True

    def test_exe_spoofed_as_jpeg_rejected(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"MZ\x90\x00", "image/jpeg") is False

    def test_pdf_bytes_declared_jpeg_rejected(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"%PDF-1.4", "image/jpeg") is False

    def test_disallowed_declared_mime_rejected(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        # D-24: video/mp4 is not an allowed comprobante mime even with
        # valid-looking bytes.
        assert validate_magic_bytes(b"\xff\xd8\xff", "video/mp4") is False

    def test_empty_chunk_rejected(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"", "image/jpeg") is False


class TestConstants:
    def test_attachment_max_bytes_is_5mb(self) -> None:
        from app.features.payment.attachment import ATTACHMENT_MAX_BYTES

        assert ATTACHMENT_MAX_BYTES == 5 * 1024 * 1024

    def test_allowed_mime_types(self) -> None:
        from app.features.payment.attachment import ALLOWED_MIME_TYPES

        assert ALLOWED_MIME_TYPES == frozenset(
            {"image/jpeg", "image/png", "image/webp", "application/pdf"}
        )
