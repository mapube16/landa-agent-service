"""Pytest configuration for landa-agent-service.

Real fixtures land in plan 01-04. This file exists to keep the test root
importable as a package and to make the pytest plugin surface explicit.
"""

import pytest  # noqa: F401  -- kept for downstream conftest extensions

pytest_plugins: list[str] = []
