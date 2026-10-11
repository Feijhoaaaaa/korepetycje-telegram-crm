"""Keep lesson history when a student or teacher is hard-deleted.

Revision ID: 0006_hard_delete_people
Revises: 0005_delete_student_lessons
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_hard_delete_people"
down_revision = "0005_delete_student_lessons"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def upgrade() -> None:
    op.add_column("lessons", sa.Column("student_name_snapshot", sa.Text()), schema=SCHEMA)
    op.add_column("lessons", sa.Column("teacher_name_snapshot", sa.Text()), schema=SCHEMA)

    op.drop_constraint("study_chats_teacher_id_fkey", "study_chats", schema=SCHEMA, type_="foreignkey")
    op.alter_column("study_chats", "teacher_id", nullable=True, schema=SCHEMA)
    op.create_foreign_key(
        "fk_study_chats_teacher",
        "study_chats",
        "teachers",
        ["teacher_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )

    op.drop_constraint("lessons_study_chat_id_fkey", "lessons", schema=SCHEMA, type_="foreignkey")
    op.alter_column("lessons", "study_chat_id", nullable=True, schema=SCHEMA)
    op.create_foreign_key(
        "fk_lessons_study_chat",
        "lessons",
        "study_chats",
        ["study_chat_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )

    op.drop_constraint("fk_lessons_teacher", "lessons", schema=SCHEMA, type_="foreignkey")
    op.alter_column("lessons", "teacher_id", nullable=True, schema=SCHEMA)
    op.create_foreign_key(
        "fk_lessons_teacher",
        "lessons",
        "teachers",
        ["teacher_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_lessons_teacher", "lessons", schema=SCHEMA, type_="foreignkey")
    op.alter_column("lessons", "teacher_id", nullable=False, schema=SCHEMA)
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

    op.drop_constraint("fk_lessons_study_chat", "lessons", schema=SCHEMA, type_="foreignkey")
    op.alter_column("lessons", "study_chat_id", nullable=False, schema=SCHEMA)
    op.create_foreign_key(
        "lessons_study_chat_id_fkey",
        "lessons",
        "study_chats",
        ["study_chat_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )

    op.drop_constraint("fk_study_chats_teacher", "study_chats", schema=SCHEMA, type_="foreignkey")
    op.alter_column("study_chats", "teacher_id", nullable=False, schema=SCHEMA)
    op.create_foreign_key(
        "study_chats_teacher_id_fkey",
        "study_chats",
        "teachers",
        ["teacher_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.drop_column("lessons", "teacher_name_snapshot", schema=SCHEMA)
    op.drop_column("lessons", "student_name_snapshot", schema=SCHEMA)
