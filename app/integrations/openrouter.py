"""OpenRouter LLM factory — the SINGLE point of LLM instantiation for the service.

Per CLAUDE.md ("Llama LLMs solo vía ``get_llm(role)``") + RESEARCH.md Pattern 2:

- All LLM calls flow through OpenRouter (NO direct Anthropic / OpenAI SDK use)
- ``get_llm(role)`` returns a configured ``ChatOpenAI`` whose ``base_url`` points
  at the OpenRouter gateway. The model name, temperature, fallbacks, and
  default headers are role-derived from ``settings``.
- The 4 canonical roles are ``conversation``, ``judge``, ``intent_classifier``,
  and ``summarizer``. ``intent`` is an alias for ``intent_classifier``
  (keeps call-sites in feature code terse).
- ``HTTP-Referer`` + ``X-Title`` default headers are OpenRouter's attribution
  surface (RESEARCH.md Pattern 2). Neither contains PII.
- ``temperature=0.0`` for ``judge`` (determinism — CLAUDE.md Stack table);
  other roles default to ``0.7``.
- Per-OpenRouter native fallbacks pass via ``model_kwargs={"models": [...]}``
  (RESEARCH.md Pattern 2 + Assumptions Log A8); empty list means "no fallback".
- ``@lru_cache(maxsize=8)`` warm-reuses the internal httpx client so connection
  pools persist across calls. The cache is keyed by the (resolved) role string.
- LangSmith auto-tracing kicks in via env vars (``LANGSMITH_TRACING=true`` +
  ``LANGSMITH_API_KEY`` + ``LANGSMITH_PROJECT``) — ``langchain`` instruments
  every ``ChatOpenAI`` it sees on import, no explicit callbacks needed.

NEVER instantiate ``ChatOpenAI`` directly elsewhere in the codebase; always go
through this factory so the OpenRouter gateway invariant + LangSmith tracing
remain enforced.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from langchain_openai import ChatOpenAI

from app.config.settings import settings

LLMRole = Literal["conversation", "judge", "intent_classifier", "summarizer"]

# Canonical role set; ``intent`` is exposed publicly via ``_ROLE_ALIASES`` only.
_CANONICAL_ROLES: frozenset[LLMRole] = frozenset(
    {"conversation", "judge", "intent_classifier", "summarizer"}
)

# Alias map — both keys ultimately yield the same ChatOpenAI instance from cache
# because ``_resolve_role`` normalises and ``get_llm`` re-enters itself with the
# canonical name (see ``_get_llm_resolved``).
_ROLE_ALIASES: dict[str, LLMRole] = {
    "intent": "intent_classifier",
}


def _resolve_role(role: str) -> LLMRole:
    """Normalise input role to canonical form; raise ``KeyError`` if unknown."""
    normalised: str = _ROLE_ALIASES.get(role, role)
    if normalised not in _CANONICAL_ROLES:
        raise KeyError(f"Unknown LLM role: {role!r}")
    # mypy narrows ``normalised`` to ``LLMRole`` after the membership check above.
    return normalised


def _model_for(role: LLMRole) -> str:
    """Return the OpenRouter model slug for the given canonical role."""
    return {
        "conversation": settings.llm.model_conversation,
        "judge": settings.llm.model_judge,
        "intent_classifier": settings.llm.model_intent,
        "summarizer": settings.llm.model_summarizer,
    }[role]


def _fallbacks_for(role: LLMRole) -> list[str]:
    """Return the OpenRouter native fallback model list for the role.

    Only ``conversation`` and ``judge`` carry fallbacks in v1 — the cheaper
    ``intent_classifier`` and ``summarizer`` paths have no fallback by design
    (their failure modes are recoverable upstream).
    """
    return {
        "conversation": settings.llm.fallbacks_conversation,
        "judge": settings.llm.fallbacks_judge,
        "intent_classifier": [],
        "summarizer": [],
    }[role]


def _temperature_for(role: LLMRole) -> float:
    """Return the role-specific temperature.

    ``judge`` MUST be deterministic (temperature=0); other roles get a mild
    creative budget (0.7) suitable for conversational + summarisation work.
    """
    return 0.0 if role == "judge" else 0.7


@lru_cache(maxsize=8)
def _get_llm_resolved(role: LLMRole) -> ChatOpenAI:
    """Construct (and cache) a ``ChatOpenAI`` instance for the resolved role."""
    model = _model_for(role)
    fallbacks = _fallbacks_for(role)
    kwargs: dict[str, Any] = {
        "model": model,
        "base_url": settings.openrouter.base_url,
        "api_key": settings.openrouter.api_key.get_secret_value(),
        "default_headers": {
            "HTTP-Referer": settings.app.public_url,
            "X-Title": "landa-agent-service",
        },
        "temperature": _temperature_for(role),
        "timeout": 30,
        "max_retries": 2,
    }
    if fallbacks:
        # OpenRouter-native multi-model fallback: client posts ``models: [...]``
        # in the request body and OpenRouter cycles through them on upstream
        # failure (RESEARCH.md Pattern 2 + Assumptions Log A8).
        kwargs["model_kwargs"] = {"models": fallbacks}
    return ChatOpenAI(**kwargs)


def get_llm(role: str) -> ChatOpenAI:
    """Return the canonical ``ChatOpenAI`` instance for ``role`` (cached).

    Aliases (``intent`` → ``intent_classifier``) resolve to the same cached
    instance as the canonical role. Unknown roles raise ``KeyError``.
    """
    resolved = _resolve_role(role)
    return _get_llm_resolved(resolved)


# Public read-only view of the role → model mapping. Useful for /debug routes
# and for tests that want to assert the deployed mapping without calling
# ``settings`` directly. Not used by ``get_llm`` itself.
ROLE_MODEL_MAP: dict[LLMRole, str] = {
    "conversation": settings.llm.model_conversation,
    "judge": settings.llm.model_judge,
    "intent_classifier": settings.llm.model_intent,
    "summarizer": settings.llm.model_summarizer,
}


__all__ = [
    "LLMRole",
    "ROLE_MODEL_MAP",
    "get_llm",
]
