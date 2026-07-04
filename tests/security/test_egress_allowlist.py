"""Egress allowlist static scan — CI compensating control (ADR-006, SEC-05).

Walks app/**/*.py and regex-scans for hardcoded ``https?://`` literals.
Every captured hostname must appear in ALLOWED_EGRESS_HOSTS or be a recognized
test/localhost placeholder.  Unknown hosts fail the test with file:line context
so new hardcoded endpoints cannot silently enter the codebase.

This is the app-level compensating control for the Railway-egress network gap
documented in ADR-006.  It does NOT block network-level exfiltration by a
compromised dependency — that risk is accepted for v1 per ADR-006.

ALLOWED_EGRESS_HOSTS covers:
  - SoftSeguros (integration ERP)
  - Meta Graph API (WhatsApp Cloud API)
  - Facebook CDN / lookaside (media download URLs that Meta returns)
  - Chatwoot self-hosted (env-driven; pattern covers landatech.org domain)
  - OpenRouter (LLM gateway)
  - LangSmith / smith.langchain.com (LLM observability)
  - Sentry (error tracking — DSN is env-driven, but SDK endpoints use sentry.io)
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Allowlist — every hostname in app/ must match one of these entries.
# Entries are exact lowercase hostnames or suffixes (checked via endswith).
# ---------------------------------------------------------------------------

ALLOWED_EGRESS_HOSTS: frozenset[str] = frozenset(
    {
        # SoftSeguros ERP
        "app.softseguros.com",
        # Meta / Facebook (WhatsApp Cloud API + media CDN)
        "graph.facebook.com",
        "lookaside.fbsbx.com",
        # Chatwoot self-hosted (landatech.org domain)
        "chat.landatech.org",
        # OpenRouter (LLM gateway)
        "openrouter.ai",
        # LangSmith (LLM observability)
        "api.smith.langchain.com",
        "smith.langchain.com",
        # Sentry (error tracking — DSN is env-driven but endpoint suffix is sentry.io)
        "sentry.io",
        # Lambda-proyect voice agent (internal service, env-driven base URL)
        "localhost",
        "127.0.0.1",
    }
)

# Placeholder / test domains — always allowed (never production traffic)
_TEST_PLACEHOLDER_SUFFIXES: tuple[str, ...] = (
    "localhost",
    "127.0.0.1",
    "example.com",
    "test",
    # Chatwoot test fixture domain used in test conftest
    "chat-test.example.com",
)

_URL_RE = re.compile(r"https?://([A-Za-z0-9.\-]+)", re.IGNORECASE)

_APP_DIR = Path(__file__).parent.parent.parent / "app"


def _is_allowed(host: str) -> bool:
    """Return True if ``host`` is in the allowlist or is a test/localhost placeholder."""
    h = host.lower()
    # Exact match in allowlist
    if h in ALLOWED_EGRESS_HOSTS:
        return True
    # Suffix match for subdomains (e.g. *.sentry.io, *.langchain.com)
    for allowed in ALLOWED_EGRESS_HOSTS:
        if h.endswith("." + allowed):
            return True
    # Test/placeholder check
    for suffix in _TEST_PLACEHOLDER_SUFFIXES:
        if h == suffix or h.endswith("." + suffix):
            return True
    return False


def test_no_hardcoded_external_host_outside_allowlist() -> None:
    """All hardcoded https?:// hostnames in app/ must be in ALLOWED_EGRESS_HOSTS.

    Fails with file:line and the offending hostname so the developer can either
    route through the appropriate integration client (whose base_url comes from
    settings) or add a justified entry to ALLOWED_EGRESS_HOSTS.
    """
    violations: list[str] = []

    for py_file in sorted(_APP_DIR.rglob("*.py")):
        # Skip __pycache__ bytecode artifacts
        if "__pycache__" in py_file.parts:
            continue

        text = py_file.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            # Skip pure comment lines (line-level; inline comments still scanned)
            stripped = line.lstrip()
            if stripped.startswith("#"):
                # Comment-only line: skip (docstring URLs are informational)
                continue
            for match in _URL_RE.finditer(line):
                host = match.group(1)
                if not _is_allowed(host):
                    rel = py_file.relative_to(_APP_DIR.parent)
                    violations.append(
                        f"{rel}:{lineno}: unauthorized egress host {host!r} "
                        f"(url={match.group(0)!r})"
                    )

    assert not violations, (
        f"Found {len(violations)} hardcoded URL(s) outside ALLOWED_EGRESS_HOSTS:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_allowed_egress_hosts_contains_required_entries() -> None:
    """ALLOWED_EGRESS_HOSTS must cover all five integration categories."""
    required: dict[str, str] = {
        "app.softseguros.com": "SoftSeguros ERP",
        "graph.facebook.com": "Meta Graph API",
        "openrouter.ai": "OpenRouter LLM gateway",
        "api.smith.langchain.com": "LangSmith observability",
        "sentry.io": "Sentry error tracking",
    }
    missing = {host: label for host, label in required.items() if host not in ALLOWED_EGRESS_HOSTS}
    assert not missing, f"Missing required allowlist entries: {missing}"
