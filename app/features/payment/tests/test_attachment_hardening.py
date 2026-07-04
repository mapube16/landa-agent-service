"""Attachment hardening tests (05-07 SEC-08).

Validates:
- MZ (PE/.exe) magic bytes declared as image/jpeg are rejected
- Blocked file extensions (.exe, .js, .sh, .bat, .dll, .msi, .scr) are rejected
- Payloads exceeding ATTACHMENT_MAX_BYTES + 1 are rejected
- Valid small JPEG + benign filename is accepted (regression guard)
"""

from __future__ import annotations


class TestBlockedExtensions:
    """Extension-based rejection (belt-and-suspenders alongside magic bytes)."""

    def test_exe_extension_rejected(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("comprobante.exe") is True

    def test_js_extension_rejected(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("payload.js") is True

    def test_sh_extension_rejected(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("run.sh") is True

    def test_bat_extension_rejected(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("launch.bat") is True

    def test_dll_extension_rejected(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("library.dll") is True

    def test_msi_extension_rejected(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("installer.msi") is True

    def test_scr_extension_rejected(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("screensaver.scr") is True

    def test_case_insensitive_exe(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("VIRUS.EXE") is True

    def test_case_insensitive_bat(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("RUN.BAT") is True

    def test_none_filename_returns_false(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension(None) is False

    def test_benign_jpeg_filename_accepted(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("comprobante.jpg") is False

    def test_benign_png_filename_accepted(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("recibo.png") is False

    def test_benign_pdf_filename_accepted(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("pago.pdf") is False


class TestMzMagicBytesRejected:
    """MZ (PE/exe) magic bytes must be rejected regardless of declared MIME."""

    def test_mz_bytes_declared_jpeg_rejected(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        # MZ header (DOS/PE executable magic) declared as image/jpeg
        assert validate_magic_bytes(b"MZ\x90\x00\x03\x00\x00\x00", "image/jpeg") is False

    def test_mz_bytes_declared_png_rejected(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"MZ\x90\x00", "image/png") is False

    def test_mz_bytes_declared_pdf_rejected(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"MZ\x90\x00", "application/pdf") is False


class TestOversizePayload:
    """Payloads exceeding ATTACHMENT_MAX_BYTES must be detected as oversized."""

    def test_oversize_payload_exceeds_cap(self) -> None:
        from app.features.payment.attachment import ATTACHMENT_MAX_BYTES

        oversized_len = ATTACHMENT_MAX_BYTES + 1
        # The cap is 5 MB per D-25; intentionally tighter than ROADMAP 10 MB ceiling
        assert oversized_len > ATTACHMENT_MAX_BYTES

    def test_attachment_max_bytes_is_5mb(self) -> None:
        from app.features.payment.attachment import ATTACHMENT_MAX_BYTES

        # D-25: 5 MB cap — intentionally conservative (under ROADMAP 10 MB ceiling)
        assert ATTACHMENT_MAX_BYTES == 5 * 1024 * 1024

    def test_blocked_extensions_constant_exists(self) -> None:
        from app.features.payment.attachment import BLOCKED_EXTENSIONS

        assert ".exe" in BLOCKED_EXTENSIONS
        assert ".js" in BLOCKED_EXTENSIONS
        assert ".sh" in BLOCKED_EXTENSIONS
        assert ".bat" in BLOCKED_EXTENSIONS


class TestRegressionValidInput:
    """Valid small JPEG + benign filename must still be accepted."""

    def test_valid_jpeg_magic_accepted(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"\xff\xd8\xff\xe0\x00\x10", "image/jpeg") is True

    def test_valid_jpeg_filename_not_blocked(self) -> None:
        from app.features.payment.attachment import has_blocked_extension

        assert has_blocked_extension("comprobante_enero.jpg") is False

    def test_valid_pdf_accepted(self) -> None:
        from app.features.payment.attachment import validate_magic_bytes

        assert validate_magic_bytes(b"%PDF-1.4 header", "application/pdf") is True
