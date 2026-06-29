"""Tests for app.features.handoff.echo — pure functions (no I/O).

Allowlist tests depend on the autouse ``_test_env`` fixture in conftest
which sets ``WA_ECHO_ALLOWLIST=+15555550100,+15555550101`` (Plan 02-01).

NOTE: imports happen inside test functions so the autouse session-scoped
``_test_env`` fixture has a chance to populate env vars before any
``Settings()`` instantiation (same pattern as ``test_llm_factory.py``).
"""

from __future__ import annotations


def test_normalize_e164_adds_plus_if_missing() -> None:
    from app.features.handoff.echo import _normalize_e164

    assert _normalize_e164("16505551234") == "+16505551234"


def test_normalize_e164_idempotent_when_plus_present() -> None:
    from app.features.handoff.echo import _normalize_e164

    assert _normalize_e164("+5491134567890") == "+5491134567890"


def test_normalize_e164_strips_whitespace() -> None:
    from app.features.handoff.echo import _normalize_e164

    # Leading/trailing whitespace is stripped before the '+' check.
    assert _normalize_e164("  16505551234  ") == "+16505551234"
    assert _normalize_e164("  +16505551234  ") == "+16505551234"


def test_is_echo_allowed_true_for_allowlisted_with_plus() -> None:
    from app.features.handoff.echo import is_echo_allowed

    assert is_echo_allowed("+15555550100") is True


def test_is_echo_allowed_true_for_allowlisted_without_plus() -> None:
    from app.features.handoff.echo import is_echo_allowed

    # Meta sends ``from`` without '+'; allowlist stores with '+'. The
    # normalisation gate makes membership work regardless.
    assert is_echo_allowed("15555550100") is True


def test_is_echo_allowed_false_for_unknown() -> None:
    from app.features.handoff.echo import is_echo_allowed

    assert is_echo_allowed("+19999999999") is False


def test_is_echo_allowed_false_for_empty_string() -> None:
    from app.features.handoff.echo import is_echo_allowed

    # Defensive: stripped empty string normalises to "+" which is not in allowlist.
    assert is_echo_allowed("") is False


def test_format_echo_prefixes_text() -> None:
    from app.features.handoff.echo import format_echo

    assert format_echo("hola") == "echo: hola"


def test_format_echo_preserves_unicode() -> None:
    from app.features.handoff.echo import format_echo

    assert format_echo("¿qué tal?") == "echo: ¿qué tal?"


def test_format_media_echo_image() -> None:
    from app.features.handoff.echo import format_media_echo

    assert format_media_echo("image") == "echo: [image] received"


def test_format_media_echo_audio() -> None:
    from app.features.handoff.echo import format_media_echo

    assert format_media_echo("audio") == "echo: [audio] received"
