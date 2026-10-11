"""Add teachers, students, finance, availability and audit domain.

Revision ID: 0004_multi_role_finance
Revises: 0003_chat_title
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_multi_role_finance"
down_revision = "0003_chat_title"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column(
        "teachers", sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False), schema=SCHEMA
    )
    op.add_column(
        "teachers", sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False), schema=SCHEMA
    )
    op.add_column("teachers", sa.Column("default_price", sa.Numeric(12, 2)), schema=SCHEMA)
    op.add_column(
        "teachers",
        sa.Column("default_duration_minutes", sa.Integer(), server_default="60", nullable=False),
        schema=SCHEMA,
    )
    op.add_column("teachers", sa.Column("quiet_hours_start", sa.Time()), schema=SCHEMA)
    op.add_column("teachers", sa.Column("quiet_hours_end", sa.Time()), schema=SCHEMA)
    # Before this migration the installation had exactly one privileged teacher.
    op.execute(f'UPDATE "{SCHEMA}"."teachers" SET is_admin = true')

    op.create_table(
        "students",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("quiet_hours_start", sa.Time()),
        sa.Column("quiet_hours_end", sa.Time()),
        sa.Column("legacy_chat_id", sa.BigInteger(), unique=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["teacher_id"], [f"{SCHEMA}.teachers.id"], ondelete="RESTRICT"),
        schema=SCHEMA,
    )
    op.create_index("ix_students_teacher_active", "students", ["teacher_id", "active"], schema=SCHEMA)
    op.execute(
        f'''INSERT INTO "{SCHEMA}"."students"
            (teacher_id, telegram_user_id, name, active, legacy_chat_id)
            SELECT teacher_id,
                   CASE WHEN row_number() OVER (PARTITION BY student_telegram_id ORDER BY id) = 1
                        THEN student_telegram_id END,
                   COALESCE(NULLIF(student_name, ''), NULLIF(telegram_chat_title, ''), 'Ученик ' || id),
                   active, id
              FROM "{SCHEMA}"."study_chats"'''
    )
    op.add_column("study_chats", sa.Column("student_id", sa.BigInteger()), schema=SCHEMA)
    op.create_foreign_key(
        "fk_study_chats_student",
        "study_chats",
        "students",
        ["student_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.execute(
        f'''UPDATE "{SCHEMA}"."study_chats" c SET student_id = s.id
              FROM "{SCHEMA}"."students" s WHERE s.legacy_chat_id = c.id'''
    )
    op.drop_column("students", "legacy_chat_id", schema=SCHEMA)

    op.add_column("lessons", sa.Column("teacher_id", sa.BigInteger()), schema=SCHEMA)
    op.add_column("lessons", sa.Column("student_id", sa.BigInteger()), schema=SCHEMA)
    op.add_column(
        "lessons", sa.Column("duration_minutes", sa.Integer(), server_default="60", nullable=False), schema=SCHEMA
    )
    op.add_column("lessons", sa.Column("price", sa.Numeric(12, 2)), schema=SCHEMA)
    op.execute(
        f'''UPDATE "{SCHEMA}"."lessons" l
               SET teacher_id = c.teacher_id, student_id = c.student_id
              FROM "{SCHEMA}"."study_chats" c WHERE c.id = l.study_chat_id'''
    )
    op.alter_column("lessons", "teacher_id", nullable=False, schema=SCHEMA)
    op.alter_column("lessons", "student_id", nullable=False, schema=SCHEMA)
    op.create_foreign_key(
        "fk_lessons_teacher",
        "lessons",
        "teachers",
        ["teacher_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_lessons_student",
        "lessons",
        "students",
        ["student_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_check_constraint("lesson_duration_check", "lessons", "duration_minutes > 0", schema=SCHEMA)
    op.create_check_constraint("lesson_price_check", "lessons", "price IS NULL OR price >= 0", schema=SCHEMA)
    op.create_index("ix_lessons_teacher_datetime", "lessons", ["teacher_id", "lesson_datetime"], schema=SCHEMA)
    op.create_index("ix_lessons_student_datetime", "lessons", ["student_id", "lesson_datetime"], schema=SCHEMA)

    op.create_table(
        "payment_receipts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("student_id", sa.BigInteger(), nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_file_id", sa.Text(), nullable=False),
        sa.Column("telegram_file_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("rejection_reason", sa.Text()),
        sa.Column("reviewed_by_teacher_id", sa.BigInteger()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="payment_receipt_status_check"),
        sa.CheckConstraint("amount IS NULL OR amount > 0", name="payment_receipt_amount_check"),
        sa.ForeignKeyConstraint(["student_id"], [f"{SCHEMA}.students.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["teacher_id"], [f"{SCHEMA}.teachers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by_teacher_id"], [f"{SCHEMA}.teachers.id"]),
        schema=SCHEMA,
    )
    op.create_index("ix_payment_receipts_student_status", "payment_receipts", ["student_id", "status"], schema=SCHEMA)
    op.create_table(
        "lesson_settlements",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("lesson_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("student_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("confirmed_by_teacher_id", sa.BigInteger(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("amount > 0", name="lesson_settlement_amount_check"),
        sa.ForeignKeyConstraint(["lesson_id"], [f"{SCHEMA}.lessons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], [f"{SCHEMA}.students.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by_teacher_id"], [f"{SCHEMA}.teachers.id"]),
        schema=SCHEMA,
    )
    op.create_table(
        "availability_rules",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("teacher_id", sa.BigInteger()),
        sa.Column("student_id", sa.BigInteger()),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="availability_weekday_check"),
        sa.CheckConstraint("start_time < end_time", name="availability_time_check"),
        sa.CheckConstraint("num_nonnulls(teacher_id, student_id) = 1", name="availability_owner_check"),
        sa.ForeignKeyConstraint(["teacher_id"], [f"{SCHEMA}.teachers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], [f"{SCHEMA}.students.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_availability_owner_weekday", "availability_rules", ["teacher_id", "student_id", "weekday"], schema=SCHEMA
    )
    op.create_table(
        "availability_exceptions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("teacher_id", sa.BigInteger()),
        sa.Column("student_id", sa.BigInteger()),
        sa.Column("exception_date", sa.Date(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("start_time", sa.Time()),
        sa.Column("end_time", sa.Time()),
        *timestamps(),
        sa.CheckConstraint("start_time IS NULL OR start_time < end_time", name="availability_exception_time_check"),
        sa.CheckConstraint("num_nonnulls(teacher_id, student_id) = 1", name="availability_exception_owner_check"),
        sa.ForeignKeyConstraint(["teacher_id"], [f"{SCHEMA}.teachers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], [f"{SCHEMA}.students.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_availability_exception_owner_date",
        "availability_exceptions",
        ["teacher_id", "student_id", "exception_date"],
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
        *timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'cancelled')", name="reschedule_status_check"
        ),
        sa.ForeignKeyConstraint(["lesson_id"], [f"{SCHEMA}.lessons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], [f"{SCHEMA}.students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_teacher_id"], [f"{SCHEMA}.teachers.id"]),
        schema=SCHEMA,
    )
    op.create_index("ix_reschedule_student_status", "reschedule_requests", ["student_id", "status"], schema=SCHEMA)
    op.create_table(
        "student_link_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("student_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_telegram_user_id", sa.BigInteger()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by_teacher_id", sa.BigInteger()),
        *timestamps(),
        sa.ForeignKeyConstraint(["student_id"], [f"{SCHEMA}.students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by_teacher_id"], [f"{SCHEMA}.teachers.id"]),
        schema=SCHEMA,
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("before", postgresql.JSONB()),
        sa.Column("after", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_audit_entity", "audit_log", ["entity_type", "entity_id"], schema=SCHEMA)
    for table in (
        "students",
        "payment_receipts",
        "lesson_settlements",
        "availability_rules",
        "availability_exceptions",
        "reschedule_requests",
        "student_link_tokens",
        "audit_log",
    ):
        op.execute(f'ALTER TABLE "{SCHEMA}"."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in (
        "audit_log",
        "student_link_tokens",
        "reschedule_requests",
        "availability_exceptions",
        "availability_rules",
        "lesson_settlements",
        "payment_receipts",
    ):
        op.drop_table(table, schema=SCHEMA)
    op.drop_index("ix_lessons_student_datetime", table_name="lessons", schema=SCHEMA)
    op.drop_index("ix_lessons_teacher_datetime", table_name="lessons", schema=SCHEMA)
    for column in ("price", "duration_minutes", "student_id", "teacher_id"):
        op.drop_column("lessons", column, schema=SCHEMA)
    op.drop_column("study_chats", "student_id", schema=SCHEMA)
    op.drop_table("students", schema=SCHEMA)
    for column in (
        "quiet_hours_end",
        "quiet_hours_start",
        "default_duration_minutes",
        "default_price",
        "is_admin",
        "active",
    ):
        op.drop_column("teachers", column, schema=SCHEMA)
