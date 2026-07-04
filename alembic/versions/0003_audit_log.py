"""audit log: append-only table + immutable trigger + hash chain indexes

Revision ID: 0003_audit_log
Revises: 0002_payment_tables
Create Date: 2026-07-04 00:00:00.000000

Creates the ``audit_log`` table with:
  - ``trg_audit_log_immutable`` — BEFORE DELETE OR UPDATE trigger that raises
    a Postgres EXCEPTION, enforcing append-only semantics at the DB engine
    level regardless of which role connects (single-role design; REVOKE is
    insufficient — see 05-RESEARCH.md Pattern 1).
  - ``ix_audit_log_created_at`` — range scans for time-based queries.
  - ``ix_audit_log_conversation_id`` — per-conversation chain verification.

Downgrade drops trigger, function, indexes, and table in that order.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_audit_log"
down_revision: str | Sequence[str] | None = "0002_payment_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create audit_log table, immutability trigger + function, and indexes."""
    # ------------------------------------------------------------------
    # audit_log table
    # ------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("poliza_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column(
            "prev_hash",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("entry_hash", sa.Text(), nullable=False),
        sa.Column("metadata", sa.Text(), nullable=True),
    )

    # ------------------------------------------------------------------
    # Immutability trigger: BEFORE DELETE OR UPDATE raises EXCEPTION
    # ------------------------------------------------------------------
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION audit_log_immutable()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION 'audit_log is append-only: % on row % is forbidden',
                TG_OP, OLD.id;
              RETURN NULL;
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_audit_log_immutable
              BEFORE DELETE OR UPDATE ON audit_log
              FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
            """
        )
    )

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index(
        "ix_audit_log_conversation_id", "audit_log", ["conversation_id"]
    )


def downgrade() -> None:
    """Drop trigger, function, indexes, and table in safe order."""
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS audit_log_immutable()"))
    op.drop_index("ix_audit_log_conversation_id", table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_table("audit_log")
