"""Meta Cloud API client skeleton — implemented in Plan 02-02.

HTTP client to ``graph.facebook.com`` v21.0 (D-08). Singleton cached via
``@lru_cache(maxsize=1)`` — same pattern as ``app/integrations/openrouter.py``
(:func:`get_llm`). Plan 02-02 implements :meth:`MetaCloudClient.send_text`
and :meth:`MetaCloudClient.send_media_ack`.

**NEVER instantiate httpx clients to Meta elsewhere** — always go through
:func:`get_meta_client` so the connection pool stays warm and headers are
consistent across the codebase.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

import httpx
import structlog

from app.config.settings import settings

META_API_VERSION: Final[str] = "v21.0"
META_BASE_URL: Final[str] = f"https://graph.facebook.com/{META_API_VERSION}"

log = structlog.get_logger("integrations.meta_cloud")


class MetaCloudClient:
    """Async client for the Meta Cloud API. Implemented in Plan 02-02."""

    def __init__(self, http: httpx.AsyncClient, phone_id: str) -> None:
        self._http = http
        self._phone_id = phone_id

    async def send_text(self, to: str, body: str) -> str:
        """Send a text message; return the Meta ``wamid``. Implemented in Plan 02-02."""
        raise NotImplementedError("Implemented in Plan 02-02")

    async def send_media_ack(self, to: str, media_type: str) -> str:
        """Send the media-acknowledgement echo; return wamid. Implemented in Plan 02-02."""
        raise NotImplementedError("Implemented in Plan 02-02")


@lru_cache(maxsize=1)
def get_meta_client() -> MetaCloudClient:
    """Return the cached :class:`MetaCloudClient` singleton. Implemented in Plan 02-02."""
    # Reference ``settings`` so mypy --strict sees the dependency. Body raises
    # so plan 02-02 owns the actual httpx.AsyncClient construction + token wire-up.
    _ = settings.whatsapp.phone_id
    raise NotImplementedError("Implemented in Plan 02-02")


__all__ = [
    "META_API_VERSION",
    "META_BASE_URL",
    "MetaCloudClient",
    "get_meta_client",
]
