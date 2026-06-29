"""Chatwoot Application API client.

HTTP client to the Chatwoot self-hosted instance (``settings.chatwoot.url``).
Singleton cached via ``@lru_cache(maxsize=1)`` -- same pattern as
``app/integrations/meta_cloud.py`` / ``get_meta_client()``.

Authentication: ``api_access_token`` header (Chatwoot Application API v1,
NOT the bot agent token and NOT ``Authorization: Bearer``). Confirmed in
03-00 probe Task 2 and RESEARCH Pattern 5.

Inbox context: the inbox is an "API Channel" (``channel_type=Channel::Api``),
NOT the WhatsApp native inbox being wired in F4. This keeps the F3 mirror
channel cleanly separated from the F4 bidirectional escalation inbox.

**NEVER instantiate httpx clients to Chatwoot elsewhere** -- always go through
:func:`get_chatwoot_client` to preserve connection pool and header consistency.

Redis is optional at construction time; the lifespan in Plan 03-05 late-binds
it via ``app.state.chatwoot._redis = app.state.redis``. Cache bypasses cleanly
on Redis failure (bypass-on-cache-down, same pattern as softseguros.py).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

import httpx
import structlog

from app.config.settings import settings
from app.integrations.meta_cloud import _hash_phone  # reuse, do NOT redeclare

log = structlog.get_logger("integrations.chatwoot")

__all__ = ["ChatwootClient", "get_chatwoot_client"]


class ChatwootClient:
    """Async client for the Chatwoot Application API v1."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        account_id: int,
        redis: Any | None = None,
    ) -> None:
        self._http = http
        self._account_id = account_id
        # Redis optional -- factory leaves None; lifespan in 03-05 binds via
        # app.state.chatwoot._redis = app.state.redis
        self._redis = redis

    async def post_message(
        self,
        conversation_id: int,
        content: str,
        message_type: Literal["incoming", "outgoing"],
    ) -> None:
        """POST a message to a Chatwoot conversation.

        ``incoming`` = client's WhatsApp message (shown on left in Chatwoot).
        ``outgoing`` = bot's response (shown on right, attributed to the API agent).

        Never logs ``content`` raw -- only ``content_len`` (T-03-03-01 mitigation).
        """
        path = f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}/messages"
        r = await self._http.post(path, json={"content": content, "message_type": message_type})
        r.raise_for_status()
        log.info(
            "chatwoot.post_message.ok",
            conv_id=conversation_id,
            msg_type=message_type,
            content_len=len(content),
        )

    async def get_or_create_conversation(self, phone: str) -> int:
        """Return conversation_id for ``phone``, creating contact + conversation if needed.

        Cache key ``chatwoot:conv:{phone_hash}`` in Redis with 7-day TTL.
        Cache failure bypasses cleanly (bypass-on-cache-down pattern from softseguros.py).

        Two-step creation on cache miss:
        1. POST /contacts  -- create or recover contact_id
        2. POST /conversations  -- create conversation, extract conv_id

        422 on duplicate contact is recovered via GET /contacts/search (Chatwoot
        Application API is not always idempotent on contact creation).
        """
        phone_hash = _hash_phone(phone)
        cache_key = f"chatwoot:conv:{phone_hash}".encode()

        # -- Cache read-through (bypass-on-cache-down) -----------------------
        cached: bytes | None = None
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
            except Exception as exc:  # noqa: BLE001
                log.warning("chatwoot.cache.read_error", error_type=type(exc).__name__)
        if cached is not None:
            return int(cached.decode())

        # -- Cache miss: two-step contact + conversation create --------------
        contact_id = await self._create_or_get_contact(phone)
        conv_id = await self._create_conversation(contact_id, phone)

        # -- Cache write (bypass-on-failure) ---------------------------------
        if self._redis is not None:
            try:
                await self._redis.set(cache_key, str(conv_id).encode(), ex=604800)  # 7 days
            except Exception as exc:  # noqa: BLE001
                log.warning("chatwoot.cache.write_error", error_type=type(exc).__name__)

        log.info("chatwoot.conv.created", phone_hash=phone_hash, conv_id=conv_id)
        return conv_id

    async def _create_or_get_contact(self, phone: str) -> int:
        """POST /contacts; on 422 duplicate, recover via GET /contacts/search.

        Chatwoot expects E.164 phone numbers WITH the ``+`` prefix; Meta
        webhook payloads strip it. Normalize here so we don't double-create
        the same contact under two spellings.
        """
        normalized = phone if phone.startswith("+") else f"+{phone}"
        path = f"/api/v1/accounts/{self._account_id}/contacts"
        payload = {
            "inbox_id": settings.chatwoot.inbox_id,
            "phone_number": normalized,
            "identifier": normalized,
        }
        try:
            r = await self._http.post(path, json=payload)
            r.raise_for_status()
            # Chatwoot Application API create-contact response shape (confirmed
            # against developers.chatwoot.com API Reference):
            # {"payload": {"contact": {"id": <int>, ...}, ...}}
            contact_id: int = r.json()["payload"]["contact"]["id"]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 422:
                log.warning(
                    "chatwoot.contact.create.422",
                    phone_hash=_hash_phone(phone),
                    body=exc.response.text[:300],
                )
                # Chatwoot 422 on duplicate contact -- recover via search
                contact_id = await self._search_contact(normalized)
            else:
                raise
        return contact_id

    async def _search_contact(self, phone: str) -> int:
        """GET /contacts/search?q={phone} to recover existing contact_id."""
        search_path = f"/api/v1/accounts/{self._account_id}/contacts/search"
        r = await self._http.get(search_path, params={"q": phone})
        r.raise_for_status()
        # Search response shape: {"payload": [{"id": <int>, ...}, ...]}
        results: list[dict[str, Any]] = r.json()["payload"]
        if not results:
            raise RuntimeError(
                f"Chatwoot contact not found after 422: phone_hash={_hash_phone(phone)}"
            )
        return int(results[0]["id"])

    async def _create_conversation(self, contact_id: int, phone: str) -> int:
        """POST /conversations to create a new conversation, return conv_id."""
        normalized = phone if phone.startswith("+") else f"+{phone}"
        path = f"/api/v1/accounts/{self._account_id}/conversations"
        payload = {
            "inbox_id": settings.chatwoot.inbox_id,
            "contact_id": contact_id,
            "source_id": normalized,
        }
        r = await self._http.post(path, json=payload)
        r.raise_for_status()
        # Chatwoot Application API create-conversation response shape:
        # {"id": <int>, "inbox_id": ..., "status": "open", ...}
        return int(r.json()["id"])

    async def mark_resolved(self, conversation_id: int) -> None:
        """Mark a Chatwoot conversation as resolved.

        POSTs to toggle_status endpoint with ``{"status": "resolved"}``.
        Called by ``node_close`` when the Q&A session ends cleanly.
        """
        path = f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}/toggle_status"
        r = await self._http.post(path, json={"status": "resolved"})
        r.raise_for_status()
        log.info("chatwoot.conv.resolved", conv_id=conversation_id)


@lru_cache(maxsize=1)
def get_chatwoot_client() -> ChatwootClient:
    """Return the cached :class:`ChatwootClient` singleton.

    NEVER instantiate httpx clients to Chatwoot elsewhere -- always go through
    this factory so the connection pool warmth and ``api_access_token`` header
    consistency are preserved. Constructed once per process; ``httpx`` cleans
    up sockets at GC time.

    Note: ``redis=None`` here; Plan 03-05 lifespan late-binds Redis via
    ``app.state.chatwoot._redis = app.state.redis``.
    """
    limits = httpx.Limits(
        max_keepalive_connections=10,
        max_connections=20,
        keepalive_expiry=30.0,
    )
    timeout = httpx.Timeout(10.0, connect=3.0, read=10.0, write=3.0, pool=2.0)
    http = httpx.AsyncClient(
        base_url=settings.chatwoot.url,
        # Chatwoot Application API uses custom header, NOT Authorization: Bearer
        # (RESEARCH Pattern 5 + RESEARCH Pitfall 4 confirmed in 03-00 probe Task 2)
        headers={"api_access_token": settings.chatwoot.api_key.get_secret_value()},
        timeout=timeout,
        limits=limits,
    )
    return ChatwootClient(http=http, account_id=settings.chatwoot.account_id, redis=None)
