"""Tests for app.memory.case_store — SQLAlchemy models.

TDD RED: Written before implementation to verify the model contract.
"""

from __future__ import annotations


def test_case_store_importable() -> None:
    """Case and Attachment must be importable from app.memory."""
    from app.memory import case_store  # noqa: F401
    from app.memory.case_store import Attachment, Case  # noqa: F401


def test_models_registered_on_base_metadata() -> None:
    """Both tables must be registered on app.config.db.Base.metadata."""
    from app.config.db import Base

    # Import case_store to trigger model registration
    from app.memory import case_store  # noqa: F401

    assert "cases" in Base.metadata.tables, "cases table not in Base.metadata"
    assert "attachments" in Base.metadata.tables, "attachments table not in Base.metadata"


def test_cases_table_has_expected_columns() -> None:
    """cases table must have all schema columns."""
    from app.config.db import Base
    from app.memory import case_store  # noqa: F401

    cases_table = Base.metadata.tables["cases"]
    column_names = {c.name for c in cases_table.columns}

    required = {
        "case_id",
        "phone",
        "poliza_id",
        "cliente_doc",
        "cliente_nombre",
        "status",
        "attachment_count",
        "reminder_sent_at",
        "escalated_at",
        "work_hours_due_at",
        "cartera_message_wamid",
        "created_at",
        "updated_at",
    }
    missing = required - column_names
    assert not missing, f"Missing columns in cases: {missing}"


def test_attachments_table_has_expected_columns() -> None:
    """attachments table must have all schema columns including FK."""
    from app.config.db import Base
    from app.memory import case_store  # noqa: F401

    attachments_table = Base.metadata.tables["attachments"]
    column_names = {c.name for c in attachments_table.columns}

    required = {
        "id",
        "case_id",
        "path",
        "mime_type",
        "sha256",
        "size_bytes",
        "meta_media_id",
        "received_at",
    }
    missing = required - column_names
    assert not missing, f"Missing columns in attachments: {missing}"


def test_attachments_has_fk_to_cases() -> None:
    """attachments.case_id must have a FK referencing cases.case_id."""
    from app.config.db import Base
    from app.memory import case_store  # noqa: F401

    attachments_table = Base.metadata.tables["attachments"]
    fk_targets = {
        fk.column.table.name for col in attachments_table.columns for fk in col.foreign_keys
    }
    assert "cases" in fk_targets, "attachments.case_id has no FK to cases"


def test_case_all_exports() -> None:
    """case_store.__all__ must export Case and Attachment."""
    from app.memory import case_store

    assert hasattr(case_store, "__all__")
    assert "Case" in case_store.__all__
    assert "Attachment" in case_store.__all__
