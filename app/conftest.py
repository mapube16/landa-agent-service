"""Env bootstrap for tests colocated under ``app/`` (CLAUDE.md convention).

pytest only auto-loads ``tests/conftest.py`` for the top-level ``tests/``
tree; feature-local tests under ``app/**/tests/`` import ``app.config.settings``
at collection time and need the same placeholder env vars BEFORE the first
``Settings()`` instantiation. Values mirror ``tests/conftest.py`` — dummy
credentials only; tests that touch IO stub their clients.
"""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/landa_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-test-key")
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("LANGSMITH_API_KEY", "ls-test-key")
os.environ.setdefault("LANGSMITH_PROJECT", "landa-agent-test")
os.environ.setdefault("WA_TOKEN", "wa-test-token")
os.environ.setdefault("WA_PHONE_ID", "1267241483129092")
os.environ.setdefault("WA_BUSINESS_ACCOUNT_ID", "1451322196454283")
os.environ.setdefault("WA_WEBHOOK_SECRET", "test-webhook-secret-do-not-use-in-prod")
os.environ.setdefault("WA_VERIFY_TOKEN", "test-verify-token-do-not-use-in-prod")
os.environ.setdefault("WA_ECHO_ALLOWLIST", "+15555550100,+15555550101")
os.environ.setdefault("SOFTSEGUROS_BASE_URL", "https://app.softseguros.com/")
os.environ.setdefault("SOFTSEGUROS_USERNAME", "test-user")
os.environ.setdefault("SOFTSEGUROS_PASSWORD", "test-pass")
os.environ.setdefault("CHATWOOT_URL", "https://chat-test.example.com")
os.environ.setdefault("CHATWOOT_API_KEY", "cw-test-key")
os.environ.setdefault("CHATWOOT_ACCOUNT_ID", "1")
os.environ.setdefault("CHATWOOT_INBOX_ID", "2")
os.environ.setdefault("CHATWOOT_WEBHOOK_SECRET", "test-cw-webhook-secret")
os.environ.setdefault("CARTERA_PHONE_ALLOWLIST", "")
os.environ.setdefault("LAMBDA_PROYECT_INTERNAL_TOKEN", "test-lambda-token")
