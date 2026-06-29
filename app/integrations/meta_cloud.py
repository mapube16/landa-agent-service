"""Meta Cloud API client — implemented in Plan 02-02.

HTTP client to ``graph.facebook.com`` v21.0 (D-08). Singleton cached via
``@lru_cache(maxsize=1)`` — same pattern as ``app/integrations/openrouter.py``
(:func:`get_llm`). Builds an ``httpx.AsyncClient`` with the bearer token
header baked in once at construction and re-uses its connection pool for
every outbound message.

**NEVER instantiate httpx clients to Meta elsewhere** — always go through
:func:`get_meta_client` so the connection pool stays warm and headers are
consistent across the codebase.

**Logging discipline (T-02-08 + T-02-11 + RESEARCH Pitfall 9):** raw phone
numbers and message bodies NEVER appear in structured logs. ``send_text``
emits only ``to_hash=_hash_phone(to)``, ``body_len=len(body)``, and the
upstream ``wamid``. Use :func:`_hash_phone` whenever a phone correlation
key is needed in a log line.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any, Final

import httpx
import structlog

from app.config.settings import settings
from app.models.meta import (
    InteractiveButton,
    InteractiveButtonAction,
    InteractiveButtonBody,
    InteractiveListAction,
    InteractiveListBody,
    InteractiveListRow,
    InteractiveListSection,
    OutboundButtons,
    OutboundList,
    OutboundText,
    OutboundTextBody,
)

META_API_VERSION: Final[str] = "v21.0"
META_BASE_URL: Final[str] = f"https://graph.facebook.com/{META_API_VERSION}"

log = structlog.get_logger("integrations.meta_cloud")


def _hash_phone(phone: str) -> str:
    """Return first 8 hex chars of sha256(phone).

    Non-reversible correlation token for log lines (RESEARCH Pitfall 9,
    T-02-08). NEVER log the raw phone — use this helper instead.
    """
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()[:8]


class MetaCloudClient:
    """Async client for the Meta Cloud API (``graph.facebook.com`` v21.0)."""

    def __init__(self, http: httpx.AsyncClient, phone_id: str) -> None:
        self._http = http
        self._phone_id = phone_id

    async def send_text(self, to: str, body: str) -> str:
        """Send a text message; return the Meta ``wamid``.

        POSTs to ``/{phone_id}/messages`` with the ``OutboundText`` shape
        (RESEARCH "Code Examples — Outbound text message"). Raises
        ``httpx.HTTPStatusError`` on 4xx/5xx — the caller (webhook handler)
        decides whether to log/swallow. F2 does NOT retry outbound; F4+ may
        wrap with ``tenacity`` if metrics justify it (Plan note).
        """
        payload = OutboundText(to=to, text=OutboundTextBody(body=body)).model_dump(mode="json")
        r = await self._http.post(f"/{self._phone_id}/messages", json=payload)
        if not r.is_success:
            log.error(
                "meta.send_text.failed",
                status=r.status_code,
                body=r.text[:500],  # ponytail: 500 chars enough to diagnose, no PII in error body
                phone_id=self._phone_id,
            )
        r.raise_for_status()
        data = r.json()
        # Meta success response shape (RESEARCH "Code Examples — Success response"):
        # {"messaging_product":"whatsapp","contacts":[...],"messages":[{"id":"wamid.XYZ"}]}
        wamid: str = data["messages"][0]["id"]
        log.info(
            "meta.send_text.ok",
            to_hash=_hash_phone(to),
            wamid=wamid,
            body_len=len(body),
        )
        return wamid

    async def _post_message(
        self, payload: dict[str, Any], log_event: str, **log_kwargs: Any
    ) -> str:
        """Shared POST + wamid extraction for any outbound message shape."""
        r = await self._http.post(f"/{self._phone_id}/messages", json=payload)
        if not r.is_success:
            log.error(
                "meta.send.failed",
                event=log_event,
                status=r.status_code,
                body=r.text[:500],
                phone_id=self._phone_id,
            )
        r.raise_for_status()
        wamid: str = r.json()["messages"][0]["id"]
        log.info(log_event, wamid=wamid, **log_kwargs)
        return wamid

    async def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> str:
        """Send up to 3 quick-reply buttons.

        ``buttons`` is a list of ``(id, title)`` tuples. ``id`` is what comes
        back in the inbound webhook's ``interactive.button_reply.id`` when the
        client taps; ``title`` is the label shown (max 20 chars).
        """
        if not 1 <= len(buttons) <= 3:
            raise ValueError(f"send_buttons requires 1-3 buttons, got {len(buttons)}")
        payload = OutboundButtons(
            to=to,
            interactive=InteractiveButtonBody(
                body={"text": body},
                action=InteractiveButtonAction(
                    buttons=[
                        InteractiveButton(reply={"id": bid, "title": title})
                        for bid, title in buttons
                    ]
                ),
            ),
        ).model_dump(mode="json")
        return await self._post_message(
            payload,
            "meta.send_buttons.ok",
            to_hash=_hash_phone(to),
            n_buttons=len(buttons),
            body_len=len(body),
        )

    async def send_list(
        self,
        to: str,
        body: str,
        button_label: str,
        rows: list[tuple[str, str, str | None]],
        section_title: str | None = None,
    ) -> str:
        """Send an interactive list (up to 10 rows).

        ``rows`` is a list of ``(id, title, description)`` tuples;
        ``description`` may be None. ``button_label`` is the CTA shown to open
        the list (max 20 chars). When the client picks a row the inbound
        ``interactive.list_reply.id`` carries the row id.
        """
        if not 1 <= len(rows) <= 10:
            raise ValueError(f"send_list requires 1-10 rows, got {len(rows)}")
        payload = OutboundList(
            to=to,
            interactive=InteractiveListBody(
                body={"text": body},
                action=InteractiveListAction(
                    button=button_label,
                    sections=[
                        InteractiveListSection(
                            title=section_title,
                            rows=[
                                InteractiveListRow(id=rid, title=title, description=desc)
                                for rid, title, desc in rows
                            ],
                        )
                    ],
                ),
            ),
        ).model_dump(mode="json", exclude_none=True)
        return await self._post_message(
            payload,
            "meta.send_list.ok",
            to_hash=_hash_phone(to),
            n_rows=len(rows),
            body_len=len(body),
        )

    async def send_media_ack(self, to: str, media_type: str) -> str:
        """Send the media-acknowledgement echo (D-02 + CONTEXT Specifics).

        Delegates to :meth:`send_text` with the formatted media-echo body.
        ``media_type`` is one of ``image|audio|sticker|video|document|voice|
        location`` (whitelist enforced by the webhook handler before this
        is called).
        """
        # Local import to avoid a circular dependency: features.handoff.echo
        # only imports from settings, not from this module.
        from app.features.handoff.echo import format_media_echo

        return await self.send_text(to, format_media_echo(media_type))


@lru_cache(maxsize=1)
def get_meta_client() -> MetaCloudClient:
    """Return the cached :class:`MetaCloudClient` singleton.

    NEVER instantiate httpx clients to Meta elsewhere — always go through
    this factory so the connection pool warmth and ``Authorization`` header
    consistency are preserved. Constructed once per process; ``httpx``
    cleans up sockets at GC time.
    """
    limits = httpx.Limits(
        max_keepalive_connections=20,
        max_connections=50,
        keepalive_expiry=30.0,
    )
    timeout = httpx.Timeout(10.0, connect=3.0, read=10.0, write=3.0, pool=2.0)
    http = httpx.AsyncClient(
        base_url=META_BASE_URL,
        headers={"Authorization": f"Bearer {settings.whatsapp.token.get_secret_value()}"},
        timeout=timeout,
        limits=limits,
    )
    return MetaCloudClient(http=http, phone_id=settings.whatsapp.phone_id)


__all__ = [
    "META_API_VERSION",
    "META_BASE_URL",
    "MetaCloudClient",
    "get_meta_client",
]
