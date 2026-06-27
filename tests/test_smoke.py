"""Smoke test: confirms the app package is importable and pytest is wired up.

Until plan 01-04 lands real fixtures, this is the only CI gate that runs.
"""

import app


def test_app_version() -> None:
    assert app.__version__ == "0.1.0"
