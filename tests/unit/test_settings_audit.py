"""Tests for AuditSettings + RateLimitSettings — Task 3 (05-01-PLAN.md).

TDD RED: Written before implementation.
"""

from __future__ import annotations

import os
from pathlib import Path


def test_audit_settings_defaults() -> None:
    """AuditSettings must have correct defaults for sink_path and sink_enabled."""
    # Clear any overrides so defaults are tested
    os.environ.pop("AUDIT_SINK_PATH", None)
    os.environ.pop("AUDIT_SINK_ENABLED", None)

    from app.config.settings import AuditSettings

    s = AuditSettings()
    assert s.sink_path == Path("/data/comprobantes/audit")
    assert s.sink_enabled is True


def test_audit_settings_env_override() -> None:
    """AUDIT_SINK_PATH env var must override default sink_path."""
    os.environ["AUDIT_SINK_PATH"] = "/tmp/audit"
    os.environ["AUDIT_SINK_ENABLED"] = "false"

    # Force re-parse by creating a new instance (settings singleton is not re-parsed)
    from app.config.settings import AuditSettings

    s = AuditSettings()
    assert s.sink_path == Path("/tmp/audit")
    assert s.sink_enabled is False

    # Cleanup
    os.environ.pop("AUDIT_SINK_PATH", None)
    os.environ.pop("AUDIT_SINK_ENABLED", None)


def test_rate_limit_settings_defaults() -> None:
    """RateLimitSettings must have correct defaults for all 5 fields."""
    os.environ.pop("RATE_LIMIT_ENABLED", None)
    os.environ.pop("RATE_LIMIT_PHONE_LIMIT", None)
    os.environ.pop("RATE_LIMIT_POLIZA_LIMIT", None)
    os.environ.pop("RATE_LIMIT_GLOBAL_LIMIT", None)
    os.environ.pop("RATE_LIMIT_WINDOW_S", None)

    from app.config.settings import RateLimitSettings

    s = RateLimitSettings()
    assert s.enabled is True
    assert s.phone_limit == 20
    assert s.poliza_limit == 10
    assert s.global_limit == 500
    assert s.window_s == 60


def test_rate_limit_settings_env_override() -> None:
    """RATE_LIMIT_* env vars must override defaults."""
    os.environ["RATE_LIMIT_PHONE_LIMIT"] = "5"
    os.environ["RATE_LIMIT_POLIZA_LIMIT"] = "3"
    os.environ["RATE_LIMIT_GLOBAL_LIMIT"] = "100"
    os.environ["RATE_LIMIT_WINDOW_S"] = "30"
    os.environ["RATE_LIMIT_ENABLED"] = "false"

    from app.config.settings import RateLimitSettings

    s = RateLimitSettings()
    assert s.phone_limit == 5
    assert s.poliza_limit == 3
    assert s.global_limit == 100
    assert s.window_s == 30
    assert s.enabled is False

    # Cleanup
    os.environ.pop("RATE_LIMIT_PHONE_LIMIT", None)
    os.environ.pop("RATE_LIMIT_POLIZA_LIMIT", None)
    os.environ.pop("RATE_LIMIT_GLOBAL_LIMIT", None)
    os.environ.pop("RATE_LIMIT_WINDOW_S", None)
    os.environ.pop("RATE_LIMIT_ENABLED", None)


def test_settings_composite_has_audit_and_rate_limit() -> None:
    """Composite Settings must expose .audit and .rate_limit namespaces."""
    # These are defaults; the singleton may not reflect env changes,
    # so test via direct instantiation of sub-settings.
    from app.config.settings import AuditSettings, RateLimitSettings, Settings

    # AuditSettings and RateLimitSettings must be importable
    assert AuditSettings is not None
    assert RateLimitSettings is not None

    # Settings must have both fields in the model fields
    fields = Settings.model_fields
    assert "audit" in fields, "Settings must have 'audit' field"
    assert "rate_limit" in fields, "Settings must have 'rate_limit' field"
