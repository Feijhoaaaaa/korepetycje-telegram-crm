"""Index pending service requests for the reminder worker.

Revision ID: 0011_request_reminder_index
Revises: 0010_request_idempotency
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_request_reminder_index"
down_revision = "0010_request_idempotency"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def upgrade() -> None:
    op.create_index(
        "ix_service_requests_pending_reminder",
        "service_requests",
        ["next_reminder_at", "created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_service_requests_pending_reminder", table_name="service_requests", schema=SCHEMA)
