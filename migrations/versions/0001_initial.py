"""Initial tutor bot schema.

Revision ID: 0001_initial
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.create_table(
        "teachers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("name", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "study_chats",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("student_telegram_id", sa.BigInteger()),
        sa.Column("student_name", sa.Text()),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["teacher_id"], [f"{SCHEMA}.teachers.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_study_chats_teacher_active", "study_chats", ["teacher_id", "active"], schema=SCHEMA)
    op.create_table(
        "lessons",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("study_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("lesson_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="planned", nullable=False),
        sa.Column("payment_status", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_by_telegram_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('planned', 'completed', 'cancelled')", name="lesson_status_check"),
        sa.CheckConstraint("payment_status IN ('unknown', 'paid', 'unpaid')", name="payment_status_check"),
        sa.ForeignKeyConstraint(["study_chat_id"], [f"{SCHEMA}.study_chats.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_lessons_chat_datetime", "lessons", ["study_chat_id", "lesson_datetime"], schema=SCHEMA)
    op.create_index("ix_lessons_status_datetime", "lessons", ["status", "lesson_datetime"], schema=SCHEMA)
    op.create_index("ix_lessons_payment", "lessons", ["payment_status", "deleted_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("lessons", schema=SCHEMA)
    op.drop_table("study_chats", schema=SCHEMA)
    op.drop_table("teachers", schema=SCHEMA)
