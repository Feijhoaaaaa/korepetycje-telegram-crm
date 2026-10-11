"""Unify payment and reschedule workflows in one request queue.

Revision ID: 0009_unified_service_requests
Revises: 0008_payment_submission_details
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_unified_service_requests"
down_revision = "0008_payment_submission_details"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def upgrade() -> None:
    op.create_table(
        "service_requests",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("student_id", sa.BigInteger(), nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("lesson_id", sa.BigInteger()),
        sa.Column("payment_id", sa.BigInteger(), unique=True),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_teacher_id", sa.BigInteger()),
        sa.Column("next_reminder_at", sa.DateTime(timezone=True)),
        sa.Column("last_reminder_at", sa.DateTime(timezone=True)),
        sa.Column("reminder_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "type IN ('payment_confirmation', 'lesson_reschedule', 'lesson_cancellation', "
            "'lesson_time_proposal', 'other')",
            name="service_request_type_check",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')",
            name="service_request_status_check",
        ),
        sa.CheckConstraint("reminder_count >= 0", name="service_request_reminder_count_check"),
        sa.CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL) OR (status <> 'pending' AND resolved_at IS NOT NULL)",
            name="service_request_resolution_check",
        ),
        sa.CheckConstraint(
            "(type = 'payment_confirmation' AND payment_id IS NOT NULL AND lesson_id IS NULL) OR "
            "(type IN ('lesson_reschedule', 'lesson_cancellation') AND lesson_id IS NOT NULL "
            "AND payment_id IS NULL) OR "
            "(type IN ('lesson_time_proposal', 'other') AND payment_id IS NULL)",
            name="service_request_reference_check",
        ),
        sa.ForeignKeyConstraint(["student_id"], [f"{SCHEMA}.students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], [f"{SCHEMA}.teachers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lesson_id"], [f"{SCHEMA}.lessons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], [f"{SCHEMA}.payment_receipts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["resolved_by_teacher_id"], [f"{SCHEMA}.teachers.id"], ondelete="SET NULL"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_service_requests_teacher_status_created",
        "service_requests",
        ["teacher_id", "status", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_service_requests_student_status_created",
        "service_requests",
        ["student_id", "status", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_service_requests_pending_lesson_type",
        "service_requests",
        ["lesson_id", "type"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending' AND lesson_id IS NOT NULL"),
    )
    op.execute(f'ALTER TABLE "{SCHEMA}"."service_requests" ENABLE ROW LEVEL SECURITY')

    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA}.service_requests (
                type, student_id, teacher_id, payment_id, status, comment,
                resolved_at, resolved_by_teacher_id, created_at, updated_at
            )
            SELECT
                'payment_confirmation', student_id, teacher_id, id, status, rejection_reason,
                CASE WHEN status = 'pending' THEN NULL ELSE COALESCE(reviewed_at, updated_at) END,
                reviewed_by_teacher_id, created_at, updated_at
            FROM {SCHEMA}.payment_receipts
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA}.service_requests (
                type, student_id, teacher_id, lesson_id, details, comment, status,
                resolved_at, resolved_by_teacher_id, created_at, updated_at
            )
            SELECT
                'lesson_reschedule', request.student_id, lesson.teacher_id, request.lesson_id,
                jsonb_build_object('proposed_datetime', request.proposed_datetime), request.reason,
                CASE WHEN request.status = 'accepted' THEN 'approved' ELSE request.status END,
                CASE
                    WHEN request.status = 'pending' THEN NULL
                    ELSE COALESCE(request.reviewed_at, request.updated_at)
                END,
                request.reviewed_by_teacher_id, request.created_at, request.updated_at
            FROM {SCHEMA}.reschedule_requests request
            JOIN {SCHEMA}.lessons lesson ON lesson.id = request.lesson_id
            """
        )
    )

    op.drop_table("reschedule_requests", schema=SCHEMA)
    op.drop_index("ix_payment_receipts_student_status", table_name="payment_receipts", schema=SCHEMA)
    op.drop_constraint("payment_receipt_status_check", "payment_receipts", schema=SCHEMA, type_="check")
    op.drop_constraint(
        "payment_receipts_reviewed_by_teacher_id_fkey", "payment_receipts", schema=SCHEMA, type_="foreignkey"
    )
    for column in ("reviewed_at", "reviewed_by_teacher_id", "rejection_reason", "status"):
        op.drop_column("payment_receipts", column, schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "payment_receipts",
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        schema=SCHEMA,
    )
    op.add_column("payment_receipts", sa.Column("rejection_reason", sa.Text()), schema=SCHEMA)
    op.add_column("payment_receipts", sa.Column("reviewed_by_teacher_id", sa.BigInteger()), schema=SCHEMA)
    op.add_column("payment_receipts", sa.Column("reviewed_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.create_foreign_key(
        "payment_receipts_reviewed_by_teacher_id_fkey",
        "payment_receipts",
        "teachers",
        ["reviewed_by_teacher_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {SCHEMA}.payment_receipts receipt
            SET status = request.status,
                rejection_reason = CASE WHEN request.status = 'rejected' THEN request.comment END,
                reviewed_by_teacher_id = request.resolved_by_teacher_id,
                reviewed_at = request.resolved_at
            FROM {SCHEMA}.service_requests request
            WHERE request.payment_id = receipt.id
            """
        )
    )
    op.create_check_constraint(
        "payment_receipt_status_check",
        "payment_receipts",
        "status IN ('pending', 'approved', 'rejected')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_payment_receipts_student_status",
        "payment_receipts",
        ["student_id", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "reschedule_requests",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("lesson_id", sa.BigInteger(), nullable=False),
        sa.Column("student_id", sa.BigInteger(), nullable=False),
        sa.Column("proposed_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("reviewed_by_teacher_id", sa.BigInteger()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'cancelled')", name="reschedule_status_check"
        ),
        sa.ForeignKeyConstraint(["lesson_id"], [f"{SCHEMA}.lessons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], [f"{SCHEMA}.students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_teacher_id"], [f"{SCHEMA}.teachers.id"]),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_reschedule_student_status", "reschedule_requests", ["student_id", "status"], schema=SCHEMA
    )
    op.create_index(
        "uq_reschedule_requests_pending_lesson",
        "reschedule_requests",
        ["lesson_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.execute(f'ALTER TABLE "{SCHEMA}"."reschedule_requests" ENABLE ROW LEVEL SECURITY')
    op.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA}.reschedule_requests (
                lesson_id, student_id, proposed_datetime, status, reason,
                reviewed_by_teacher_id, reviewed_at, created_at, updated_at
            )
            SELECT
                lesson_id, student_id, (details->>'proposed_datetime')::timestamptz,
                CASE WHEN status = 'approved' THEN 'accepted' ELSE status END,
                comment, resolved_by_teacher_id, resolved_at, created_at, updated_at
            FROM {SCHEMA}.service_requests
            WHERE type = 'lesson_reschedule'
            """
        )
    )

    op.drop_table("service_requests", schema=SCHEMA)
