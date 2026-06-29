"""Chatwoot Application API client — implemented in Plan 03-03.

HTTP client to the Chatwoot self-hosted instance (``settings.chatwoot.url``).
Singleton cached via ``@lru_cache(maxsize=1)`` — same pattern as
``app/integrations/meta_cloud.py`` / ``get_meta_client()``.

Authentication: ``api_access_token`` header (Chatwoot Application API v1,
NOT the bot agent token). Confirmed in 03-00 probe Task 2.

Inbox context: the inbox is an "API Channel" (``channel_type=Channel::Api``),
NOT the WhatsApp native inbox being wired in F4. This keeps the F3 mirror
channel cleanly separated from the F4 bidirectional escalation inbox.

**NEVER instantiate httpx clients to Chatwoot elsewhere** — always go through
:func:`get_chatwoot_client` to preserve connection pool and header consistency.

Methods implemented in Plan 03-03:
- ``get_or_create_conversation(phone)`` → conversation_id (int)
- ``post_message(conversation_id, content, message_type)`` → None
- ``mark_resolved(conversation_id)`` → None

Implemented in: Plan 03-03.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

import httpx
import structlog

log = structlog.get_logger("integrations.chatwoot")

__all__ = ["ChatwootClient", "get_chatwoot_client"]


class ChatwootClient:
    """Async client for the Chatwoot Application API v1."""

    def __init__(self, http: httpx.AsyncClient, account_id: int) -> None:
        self._http = http
        self._account_id = account_id

    async def get_or_create_conversation(self, phone: str) -> int:
        """Return existing open conversation_id for ``phone``, or create one.

        Looks up contacts by phone, creates contact if missing, then looks up
        open conversations in the API Channel inbox, creates conversation if
        none open. Returns ``conversation_id`` (int).

        Implemented in Plan 03-03.
        """
        raise NotImplementedError("Implemented in Plan 03-03")

    async def post_message(
        self,
        conversation_id: int,
        content: str,
        message_type: Literal["incoming", "outgoing"],
    ) -> None:
        """Post a message to Chatwoot conversation as incoming or outgoing.

        ``incoming`` = client's message (shown on left).
        ``outgoing`` = bot's response (shown on right, attributed to agent).

        Implemented in Plan 03-03.
        """
        raise NotImplementedError("Implemented in Plan 03-03")

    async def mark_resolved(self, conversation_id: int) -> None:
        """Mark a Chatwoot conversation as resolved (``status=resolved``).

        Called by ``node_close`` when the Q&A session ends cleanly.

        Implemented in Plan 03-03.
        """
        raise NotImplementedError("Implemented in Plan 03-03")


@lru_cache(maxsize=1)
def get_chatwoot_client() -> ChatwootClient:
    """Return the cached :class:`ChatwootClient` singleton.

    NEVER instantiate httpx clients to Chatwoot elsewhere — always go through
    this factory. Constructed once per process; ``httpx`` cleans up sockets
    at GC time. Returns a stub until Plan 03-03 implements the factory.

    Implemented in Plan 03-03.
    """
    raise NotImplementedError("Implemented in Plan 03-03")
