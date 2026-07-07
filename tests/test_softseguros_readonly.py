"""CI guard: SoftSegurosClient must remain READ-ONLY (operator directive).

Per CLAUDE.md '## Reglas críticas → Don't', adding a write method
(POST/PUT/PATCH/DELETE over client data) to ``SoftSegurosClient`` requires:

  1. ADR documented in ``.planning/adr/``
  2. Threat model updated in PROJECT.md §"Seguridad"
  3. Scope update approved by the operator
  4. Operator approval explicit

This test introspects the class and fails the build if any method name
(case-insensitive) contains one of the forbidden verbs. The single
``_get`` exception is the only HTTP primitive allowed; ``_cached_get``
is the cache wrapper around it; the four ``get_<resource>`` methods are
the public read API.

Top-level functions like ``_get_token`` and ``_refresh_token_on_401`` are
NOT inspected here; they live at module scope, hit ``/api-token-auth/``
(auth bootstrap, not client-data writes), and are documented in the
module docstring's READ-ONLY INVARIANT block.
"""

from __future__ import annotations

import inspect

FORBIDDEN_VERBS: frozenset[str] = frozenset(
    {
        "post",
        "put",
        "patch",
        "delete",
        "create",
        "update",
        "set_",
        "modify_",
    }
)

# Methods that are part of the public/internal READ contract — explicit allowlist.
# Any method NOT here that matches a forbidden verb fails the build.
METHOD_ALLOWLIST: frozenset[str] = frozenset(
    {
        "_get",  # the ONLY HTTP primitive (verb 'get' is not in FORBIDDEN)
        "_cached_get",  # cache wrapper around _get
        "get_poliza",
        "get_cliente",
        "get_estado",
        "get_cartera_status",  # Fase 6: replaces the old 504 get_pagos
        "get_clientes_by_documento",  # Plan 03-02: D-01 identification by document
        "get_polizas_by_cliente",  # Plan 03-02: secondary poliza call (two-call pattern)
    }
)


def test_softseguros_client_has_no_write_methods() -> None:
    """Fail if any method name contains a forbidden write verb."""
    from app.integrations.softseguros import SoftSegurosClient

    methods = [
        name
        for name, _ in inspect.getmembers(SoftSegurosClient, predicate=inspect.isfunction)
        if not name.startswith("__")
    ]

    offenders: list[str] = []
    for name in methods:
        lname = name.lower()
        if any(verb in lname for verb in FORBIDDEN_VERBS):
            if name not in METHOD_ALLOWLIST:
                offenders.append(name)

    assert not offenders, (
        f"SoftSegurosClient has unauthorized write method(s): {offenders!r}. "
        "Adding writes requires ADR + threat model + scope update + operator "
        "approval. See CLAUDE.md '## Reglas críticas → Don't'."
    )


def test_softseguros_client_has_only_one_http_primitive() -> None:
    """Sanity: confirm ``_get`` is the only underscore-prefixed HTTP primitive."""
    from app.integrations.softseguros import SoftSegurosClient

    http_verbs = {"_get", "_post", "_put", "_patch", "_delete", "_head", "_options"}
    methods = {
        name for name, _ in inspect.getmembers(SoftSegurosClient, predicate=inspect.isfunction)
    }
    primitives = methods & http_verbs

    assert primitives == {"_get"}, (
        f"Expected ONLY '_get' as HTTP primitive on SoftSegurosClient; "
        f"found {primitives!r}. ``_cached_get`` is allowed (cache wrapper). "
        "All write verbs are forbidden."
    )


def test_softseguros_module_docstring_declares_readonly() -> None:
    """Ensure the READ-ONLY INVARIANT block is present in the module docstring."""
    from app.integrations import softseguros

    doc = softseguros.__doc__ or ""
    assert "READ-ONLY INVARIANT" in doc, (
        "app/integrations/softseguros.py module docstring must include the "
        "'READ-ONLY INVARIANT' block. See CLAUDE.md '## Reglas críticas → Don't'."
    )
