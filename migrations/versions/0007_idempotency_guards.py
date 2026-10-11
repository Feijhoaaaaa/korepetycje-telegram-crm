"""Add idempotency guards for critical user actions.

Revision ID: 0007_idempotency_guards
Revises: 0006_hard_delete_people
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_idempotency_guards"
down_revision = "0006_hard_delete_people"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def upgrade() -> None:
    for table in (
        "payment_receipts",
        "lesson_settlements",
        "availability_rules",
        "availability_exceptions",
        "reschedule_requests",
        "student_link_tokens",
        "audit_log",
    ):
        op.execute(f'ALTER TABLE "{SCHEMA}"."{table}" ENABLE ROW LEVEL SECURITY')

    op.create_index(
        "uq_payment_receipts_student_file",
        "payment_receipts",
        ["student_id", "telegram_file_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_reschedule_requests_pending_lesson",
        "reschedule_requests",
        ["lesson_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_reschedule_requests_pending_lesson", table_name="reschedule_requests", schema=SCHEMA)
    op.drop_index("uq_payment_receipts_student_file", table_name="payment_receipts", schema=SCHEMA)

    for table in (
        "audit_log",
        "student_link_tokens",
        "reschedule_requests",
        "availability_exceptions",
        "availability_rules",
        "lesson_settlements",
        "payment_receipts",
    ):
        op.execute(f'ALTER TABLE "{SCHEMA}"."{table}" DISABLE ROW LEVEL SECURITY')
