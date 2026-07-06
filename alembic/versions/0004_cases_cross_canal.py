"""cases cross-canal: debtor_id + call_ids

Revision ID: 0004_cases_cross_canal
Revises: 0003_audit_log
Create Date: 2026-07-05 00:00:00.000000

Fase 6 (voice<->WhatsApp handoff, contract A):
  - ``debtor_id``  — links a case to lambda-proyect's debtor when the case
    originated from (or was later linked to) a voice call. NULL for
    WhatsApp-only cases. Gates the B1/B2 notify calls in the payment nodes.
  - ``call_ids``   — JSONB array of voice call ids associated with this case
    (appended by POST /case/handoff on each handoff received).

``conversation_ids``/``escalations``/``events`` from the original contract
draft are deferred — nothing in the current WA-side entregables reads or
writes them (YAGNI); add when a real consumer needs cross-canal timeline data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_cases_cross_canal"
down_revision: str | Sequence[str] | None = "0003_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add debtor_id + call_ids to cases, plus a lookup index on debtor_id."""
    op.add_column("cases", sa.Column("debtor_id", sa.Text(), nullable=True))
    op.add_column(
        "cases",
        sa.Column(
            "call_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.create_index(
        "ix_cases_debtor_id",
        "cases",
        ["debtor_id"],
        postgresql_where=sa.text("debtor_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop the index and both columns."""
    op.drop_index("ix_cases_debtor_id", table_name="cases")
    op.drop_column("cases", "call_ids")
    op.drop_column("cases", "debtor_id")
