"""Static schema contract for the capture dispatch outbox."""
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from personal_os.db.models import CaptureDispatchOutbox


def test_outbox_has_unique_capture_reference_and_pending_scan_index():
    table = CaptureDispatchOutbox.__table__
    expected_columns = {
        "id",
        "capture_id",
        "state",
        "attempts",
        "created_at",
        "next_attempt_at",
        "dispatched_at",
        "last_error",
    }
    assert expected_columns == {
        column.name for column in table.columns
    }
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_capture_dispatch_outbox_capture_id"
        for constraint in table.constraints
    )
    assert any(
        isinstance(constraint, ForeignKeyConstraint)
        and next(iter(constraint.elements)).ondelete == "CASCADE"
        for constraint in table.constraints
    )
    assert any(
        index.name == "ix_capture_dispatch_outbox_state_next_attempt_created"
        and [column.name for column in index.columns]
        == ["state", "next_attempt_at", "created_at"]
        for index in table.indexes
    )
