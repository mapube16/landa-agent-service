"""NIT normalization — companies must not need the dígito de verificación."""

from __future__ import annotations


def test_nit_dv_matches_probe_fixture() -> None:
    """The F3 probe fixture 900144220-7 pins the DIAN algorithm."""
    from app.features.qa.nodes import _nit_dv

    assert _nit_dv("900144220") == "7"


def test_candidates_nit_without_dv_appends_computed() -> None:
    from app.features.qa.nodes import _documento_candidates

    assert _documento_candidates("900144220") == ["900144220", "900144220-7"]


def test_candidates_cleans_dots_and_spaces() -> None:
    from app.features.qa.nodes import _documento_candidates

    assert _documento_candidates(" 900.144.220-7 ") == ["900144220-7", "900144220"]


def test_candidates_cedula_gets_dv_variant_but_as_typed_first() -> None:
    """A cédula-shaped number still tries as-typed first (cédulas have no DV)."""
    from app.features.qa.nodes import _documento_candidates

    cands = _documento_candidates("18496452")
    assert cands[0] == "18496452"
