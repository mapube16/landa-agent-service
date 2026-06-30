"""payment tables: cases + attachments

Revision ID: 0002_payment_tables
Revises: 0001
Create Date: 2026-06-30 00:00:00.000000

Adds two tables for Phase 4 payment flow (04-01-PLAN.md):
  - ``cases``      — one row per comprobante submission batch (L3 memory)
  - ``attachments`` — one comprobante file per row (FK → cases ON DELETE CASCADE)

Plus four indexes:
  - ``ix_cases_phone``                — lookup by client phone
  - ``ix_cases_status_open``          — partial index on non-terminal statuses
  - ``ix_cases_work_hours_due_at``    — partial index for timer cron query
  - ``ix_attachments_case_id``        — FK join acceleration

Downgrade drops indexes then tables in FK-safe order (attachments before cases).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_payment_tables"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create cases + attachments tables and all indexes."""
    # ------------------------------------------------------------------
    # cases table
    # ------------------------------------------------------------------
    op.create_table(
        "cases",
        sa.Column(
            "case_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("poliza_id", sa.Text(), nullable=True),
        sa.Column("cliente_doc", sa.Text(), nullable=True),
        sa.Column("cliente_nombre", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'awaiting_receipt'"),
        ),
        sa.Column(
            "attachment_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("reminder_sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("work_hours_due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cartera_message_wamid", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_receipt', 'forwarded', 'awaiting_cartera',"
            " 'approved', 'rejected', 'escalated', 'closed')",
            name="ck_cases_status",
        ),
    )

    # ------------------------------------------------------------------
    # attachments table
    # ------------------------------------------------------------------
    op.create_table(
        "attachments",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "case_id",
            sa.UUID(),
            sa.ForeignKey("cases.case_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("meta_media_id", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------
    # Lookup by client phone (most common query path)
    op.create_index("ix_cases_phone", "cases", ["phone"])

    # Partial index — only non-terminal rows (keeps timer cron fast)
    op.create_index(
        "ix_cases_status_open",
        "cases",
        ["status"],
        postgresql_where=sa.text("status NOT IN ('approved', 'rejected', 'closed')"),
    )

    # Partial index for ARQ cron: cases where timer is due
    op.create_index(
        "ix_cases_work_hours_due_at",
        "cases",
        ["work_hours_due_at"],
        postgresql_where=sa.text("status = 'awaiting_cartera'"),
    )

    # FK join acceleration
    op.create_index("ix_attachments_case_id", "attachments", ["case_id"])


def downgrade() -> None:
    """Drop indexes then tables in FK-safe order (attachments before cases)."""
    op.drop_index("ix_attachments_case_id", table_name="attachments")
    op.drop_index("ix_cases_work_hours_due_at", table_name="cases")
    op.drop_index("ix_cases_status_open", table_name="cases")
    op.drop_index("ix_cases_phone", table_name="cases")
    op.drop_table("attachments")
    op.drop_table("cases")
