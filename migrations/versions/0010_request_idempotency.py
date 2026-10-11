"""Add idempotency keys for pending service requests.

Revision ID: 0010_request_idempotency
Revises: 0009_unified_service_requests
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_request_idempotency"
down_revision = "0009_unified_service_requests"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def upgrade() -> None:
    op.add_column("service_requests", sa.Column("dedupe_key", sa.String(255)), schema=SCHEMA)
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.service_requests
            SET dedupe_key = type || ':' || COALESCE(payment_id::text, lesson_id::text, id::text)
            WHERE dedupe_key IS NULL
            """
        )
    )
    op.create_index(
        "uq_service_requests_pending_dedupe",
        "service_requests",
        ["student_id", "type", "dedupe_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending' AND dedupe_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_service_requests_pending_dedupe", table_name="service_requests", schema=SCHEMA)
    op.drop_column("service_requests", "dedupe_key", schema=SCHEMA)
