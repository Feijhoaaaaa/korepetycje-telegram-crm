"""Allow deleting student accounts without deleting lesson history.

Revision ID: 0005_delete_student_preserve_lessons
Revises: 0004_multi_role_finance
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_delete_student_lessons"
down_revision = "0004_multi_role_finance"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def upgrade() -> None:
    op.drop_constraint("fk_lessons_student", "lessons", schema=SCHEMA, type_="foreignkey")
    op.alter_column("lessons", "student_id", nullable=True, schema=SCHEMA)
    op.create_foreign_key(
        "fk_lessons_student",
        "lessons",
        "students",
        ["student_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f'DELETE FROM "{SCHEMA}"."lessons" WHERE student_id IS NULL'
        )
    )
    op.drop_constraint("fk_lessons_student", "lessons", schema=SCHEMA, type_="foreignkey")
    op.alter_column("lessons", "student_id", nullable=False, schema=SCHEMA)
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
