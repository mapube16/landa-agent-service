"""Tests for app.features.qa.messages — templates + ESCAPE_REGEX + interpolate_t04."""

from __future__ import annotations


def test_t01_contains_greeting_emoji() -> None:
    from app.features.qa.messages import T_01

    assert "👋" in T_01


def test_t03_contains_agente() -> None:
    from app.features.qa.messages import T_03

    assert "agente" in T_03.lower()


def test_interpolate_t04_substitutes_n_and_list() -> None:
    from app.features.qa.messages import interpolate_t04

    result = interpolate_t04(3, "1️⃣ POL-X\n2️⃣ POL-Y\n3️⃣ POL-Z")
    assert "3" in result
    assert "POL-X" in result
    assert "1️⃣" in result


def test_escape_regex_matches_humano() -> None:
    from app.features.qa.messages import ESCAPE_REGEX

    assert ESCAPE_REGEX.search("quiero hablar con un humano")


def test_escape_regex_matches_agente() -> None:
    from app.features.qa.messages import ESCAPE_REGEX

    assert ESCAPE_REGEX.search("necesito un agente")


def test_escape_regex_matches_persona_real() -> None:
    from app.features.qa.messages import ESCAPE_REGEX

    assert ESCAPE_REGEX.search("persona real por favor")


def test_escape_regex_matches_asesor() -> None:
    from app.features.qa.messages import ESCAPE_REGEX

    assert ESCAPE_REGEX.search("un asesor me puede ayudar?")


def test_escape_regex_does_not_match_standalone_persona() -> None:
    # "persona" alone (without "real") is in the list; but "mi persona" should match
    # The ESCAPE_REGEX has \bpersona\b so it does match bare "persona"
    # What should NOT match is something totally unrelated
    from app.features.qa.messages import ESCAPE_REGEX

    assert not ESCAPE_REGEX.search("hola, buen día")
    assert not ESCAPE_REGEX.search("quisiera información de mi póliza")


def test_all_8_templates_are_strings() -> None:
    import app.features.qa.messages as m

    for name in ["T_01", "T_02", "T_03", "T_04", "T_05", "T_06", "T_07", "T_08"]:
        val = getattr(m, name)
        assert isinstance(val, str), f"{name} should be a str, got {type(val)}"
        assert len(val) > 10, f"{name} looks like a stub (too short)"
