"""SQLAlchemy 2.0 declarative models for L3 case storage (Phase 4).

Two tables:
- ``cases`` — one row per payment case (one phone + poliza + submission batch).
- ``attachments`` — one row per comprobante file within a case.

Both models subclass ``app.config.db.Base`` so alembic autogenerate detects
them when this module is imported in ``alembic/env.py``.

READ-ONLY invariant reminder (CLAUDE.md): these models are used exclusively
via the app's session factory. No write methods are exposed on SoftSegurosClient.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.db import Base

# ---------------------------------------------------------------------------
# Status enum values for the cases table (enforced via CheckConstraint).
# ---------------------------------------------------------------------------
_CASE_STATUSES = (
    "awaiting_receipt",
    "forwarded",
    "awaiting_cartera",
    "approved",
    "rejected",
    "escalated",
    "closed",
)

_STATUS_CHECK_EXPR = "status IN ({})".format(", ".join(f"'{s}'" for s in _CASE_STATUSES))


class Case(Base):
    """L3 payment case — one row per comprobante submission batch.

    ``case_id`` is a server-generated UUID v4 (gen_random_uuid()). Python code
    may also pass a ``str(uuid.uuid4())`` when it needs the id before INSERT.

    PII note (T-04-01-02): ``phone`` and ``cliente_doc`` are stored for case
    correlation only. Phase 5 audit log will hash these fields. They are never
    sent to the LLM (D-27).
    """

    __tablename__ = "cases"
    __table_args__ = (
        sa.CheckConstraint(
            _STATUS_CHECK_EXPR,
            name="ck_cases_status",
        ),
    )

    case_id: Mapped[str] = mapped_column(
        sa.UUID(as_uuid=False),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        default=lambda: str(uuid.uuid4()),
    )
    phone: Mapped[str] = mapped_column(sa.Text, nullable=False)
    poliza_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    cliente_doc: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    cliente_nombre: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        server_default=sa.text("'awaiting_receipt'"),
        default="awaiting_receipt",
    )
    attachment_count: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        server_default=sa.text("0"),
        default=0,
    )
    reminder_sent_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )
    escalated_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )
    work_hours_due_at: Mapped[sa.DateTime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )
    cartera_message_wamid: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # Fase 6 (voice<->WA handoff, migration 0004). NULL for WhatsApp-only cases.
    debtor_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    call_ids: Mapped[list[str]] = mapped_column(
        sa.JSON, nullable=False, server_default=sa.text("'[]'"), default=list
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        onupdate=sa.func.now(),
    )

    # Relationship — backref populated by Attachment.case
    attachments: Mapped[list[Attachment]] = relationship(
        "Attachment",
        back_populates="case",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Attachment(Base):
    """One comprobante file attached to a Case.

    ``path`` is the Railway volume path
    ``/data/comprobantes/{case_id}/{timestamp}-{wamid}.{ext}``.
    It is NEVER exposed to the LLM or the client (D-03).

    ``sha256`` allows dedup detection if the client re-sends the same file.
    ``meta_media_id`` is valid for 30 days (Meta CDN TTL); after that, the
    Railway volume copy is the only source of truth.
    """

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        sa.UUID(as_uuid=False),
        sa.ForeignKey("cases.case_id", ondelete="CASCADE"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    meta_media_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    received_at: Mapped[sa.DateTime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    # Back-reference to parent Case
    case: Mapped[Case] = relationship("Case", back_populates="attachments")


__all__ = ["Case", "Attachment"]
