"""Pydantic v2 models for the Meta Cloud API surface used in Phase 2.

Scope: ONLY the shapes the F2 echo handler reads inbound and the F2 Meta
client posts outbound. F3 will extend with interactive/button/contacts/
template/etc. as the bot graph needs them — DO NOT speculatively model
the full Meta envelope here (PATTERNS.md Pitfall 10).

All inbound models set ``extra="ignore"`` so Meta can add fields without
breaking the parser; the handler only reads what it needs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Inbound (POST /webhooks/meta payload — RESEARCH "Code Examples")
# ---------------------------------------------------------------------------


class MessageText(BaseModel):
    """Body of a ``type=text`` inbound message."""

    model_config = ConfigDict(extra="ignore")

    body: str


class InboundMessage(BaseModel):
    """One entry of ``value.messages[]`` — only the fields F2 reads."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # ``from`` is a Python keyword — alias to ``from_``.
    from_: str = Field(alias="from")
    id: str
    timestamp: str
    type: Literal[
        "text",
        "image",
        "audio",
        "sticker",
        "location",
        "video",
        "document",
        "voice",
        "contacts",
        "interactive",
        "button",
        "unknown",
    ]
    # Only poblado cuando type=="text"; otros media payloads (image/audio/etc.)
    # NO se modelan acá — el handler sólo necesita ``type`` para enrutar.
    text: MessageText | None = None


class ChangeValue(BaseModel):
    """``entry[].changes[].value`` — messages list + statuses passthrough."""

    model_config = ConfigDict(extra="ignore")

    messaging_product: Literal["whatsapp"] = "whatsapp"
    # No extraer fields del metadata: display_phone_number / phone_number_id
    # no se usan en F2.
    metadata: dict[str, Any] = Field(default_factory=dict)
    messages: list[InboundMessage] | None = None
    # D-05: F2 acknowledges statuses pero no los procesa — passthrough dict.
    statuses: list[dict[str, Any]] | None = None


class Change(BaseModel):
    """``entry[].changes[]`` wrapper."""

    model_config = ConfigDict(extra="ignore")

    value: ChangeValue
    field: str


class Entry(BaseModel):
    """``InboundEnvelope.entry[]`` — wraps a list of ``Change``."""

    model_config = ConfigDict(extra="ignore")

    id: str
    changes: list[Change]


class InboundEnvelope(BaseModel):
    """Top-level webhook payload from Meta Cloud API v21.0."""

    model_config = ConfigDict(extra="ignore")

    object: str
    entry: list[Entry]


# ---------------------------------------------------------------------------
# Outbound (POST /v21.0/{phone_id}/messages body — RESEARCH "Code Examples")
# ---------------------------------------------------------------------------


class OutboundTextBody(BaseModel):
    """Body of an outbound ``type=text`` message."""

    model_config = ConfigDict(extra="ignore")

    body: str


class OutboundText(BaseModel):
    """Outbound text message POSTed to Meta Cloud API."""

    model_config = ConfigDict(extra="ignore")

    messaging_product: Literal["whatsapp"] = "whatsapp"
    recipient_type: Literal["individual"] = "individual"
    to: str
    type: Literal["text"] = "text"
    text: OutboundTextBody


# ---------------------------------------------------------------------------
# Error (response body when Meta returns 4xx/5xx — RESEARCH "Code Examples")
# ---------------------------------------------------------------------------


class MetaErrorDetail(BaseModel):
    """Inner ``error`` object from a Meta API error response."""

    model_config = ConfigDict(extra="ignore")

    message: str
    type: str | None = None
    code: int
    error_subcode: int | None = None
    fbtrace_id: str | None = None


class MetaError(BaseModel):
    """Top-level Meta API error response wrapper."""

    model_config = ConfigDict(extra="ignore")

    error: MetaErrorDetail


__all__ = [
    "Change",
    "ChangeValue",
    "Entry",
    "InboundEnvelope",
    "InboundMessage",
    "MessageText",
    "MetaError",
    "MetaErrorDetail",
    "OutboundText",
    "OutboundTextBody",
]
