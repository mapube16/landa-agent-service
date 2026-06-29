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


class ButtonReply(BaseModel):
    """``interactive.button_reply`` payload when the user taps a quick-reply button."""

    model_config = ConfigDict(extra="ignore")

    id: str  # the id we sent in the button definition
    title: str  # the label the user saw


class ListReply(BaseModel):
    """``interactive.list_reply`` payload when the user picks a list option."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    description: str | None = None


class InteractiveReply(BaseModel):
    """``message.interactive`` wrapper — exactly one of button_reply / list_reply set."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["button_reply", "list_reply"]
    button_reply: ButtonReply | None = None
    list_reply: ListReply | None = None

    def selected_id(self) -> str | None:
        """Return the id of the chosen button or list item, regardless of which kind."""
        if self.button_reply is not None:
            return self.button_reply.id
        if self.list_reply is not None:
            return self.list_reply.id
        return None


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
    # Poblado cuando type=="interactive" — respuesta de botón o lista.
    interactive: InteractiveReply | None = None


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
# Outbound — Interactive messages (button reply + list)
# https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-message-templates
# ---------------------------------------------------------------------------


class InteractiveButton(BaseModel):
    """Single quick-reply button definition.

    Meta limits:
    - id: max 256 chars (we use short slugs like "saldo", "agente")
    - title: max 20 chars, no emojis required but tolerated
    - up to 3 buttons per message
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["reply"] = "reply"
    reply: dict[str, str]  # {"id": "...", "title": "..."} per Meta schema


class InteractiveButtonAction(BaseModel):
    """``interactive.action`` block for button messages."""

    model_config = ConfigDict(extra="ignore")

    buttons: list[InteractiveButton]


class InteractiveButtonBody(BaseModel):
    """``interactive`` block for type=button messages."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["button"] = "button"
    body: dict[str, str]  # {"text": "..."} — the prompt above the buttons
    action: InteractiveButtonAction


class OutboundButtons(BaseModel):
    """Outbound interactive button message (up to 3 quick-reply buttons)."""

    model_config = ConfigDict(extra="ignore")

    messaging_product: Literal["whatsapp"] = "whatsapp"
    recipient_type: Literal["individual"] = "individual"
    to: str
    type: Literal["interactive"] = "interactive"
    interactive: InteractiveButtonBody


class InteractiveListRow(BaseModel):
    """One row inside a section of an interactive list.

    Meta limits:
    - id: max 200 chars
    - title: max 24 chars
    - description: max 72 chars (optional)
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    description: str | None = None


class InteractiveListSection(BaseModel):
    """A section groups list rows under a heading.

    Single-section lists work fine; multi-section is for >10 grouped rows.
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    rows: list[InteractiveListRow]


class InteractiveListAction(BaseModel):
    """``interactive.action`` block for list messages."""

    model_config = ConfigDict(extra="ignore")

    button: str  # CTA label, e.g. "Ver pólizas"
    sections: list[InteractiveListSection]


class InteractiveListBody(BaseModel):
    """``interactive`` block for type=list messages."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["list"] = "list"
    body: dict[str, str]  # {"text": "..."}
    action: InteractiveListAction


class OutboundList(BaseModel):
    """Outbound interactive list message (up to 10 rows total)."""

    model_config = ConfigDict(extra="ignore")

    messaging_product: Literal["whatsapp"] = "whatsapp"
    recipient_type: Literal["individual"] = "individual"
    to: str
    type: Literal["interactive"] = "interactive"
    interactive: InteractiveListBody


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
    "ButtonReply",
    "Change",
    "ChangeValue",
    "Entry",
    "InboundEnvelope",
    "InboundMessage",
    "InteractiveButton",
    "InteractiveButtonAction",
    "InteractiveButtonBody",
    "InteractiveListAction",
    "InteractiveListBody",
    "InteractiveListRow",
    "InteractiveListSection",
    "InteractiveReply",
    "ListReply",
    "MessageText",
    "MetaError",
    "MetaErrorDetail",
    "OutboundButtons",
    "OutboundList",
    "OutboundText",
    "OutboundTextBody",
]
