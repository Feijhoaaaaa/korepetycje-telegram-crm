"""Store submitted payment amount and teacher delivery details.

Revision ID: 0008_payment_submission_details
Revises: 0007_idempotency_guards
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_payment_submission_details"
down_revision = "0007_idempotency_guards"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"
TABLE = "payment_receipts"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("submitted_amount", sa.Numeric(12, 2)), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("teacher_chat_id", sa.BigInteger()), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("teacher_message_id", sa.BigInteger()), schema=SCHEMA)
    op.create_check_constraint(
        "payment_receipt_submitted_amount_check",
        TABLE,
        "submitted_amount IS NULL OR submitted_amount > 0",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("payment_receipt_submitted_amount_check", TABLE, schema=SCHEMA, type_="check")
    op.drop_column(TABLE, "teacher_message_id", schema=SCHEMA)
    op.drop_column(TABLE, "teacher_chat_id", schema=SCHEMA)
    op.drop_column(TABLE, "submitted_amount", schema=SCHEMA)
