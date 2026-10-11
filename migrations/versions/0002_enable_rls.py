"""Enable RLS for the private application schema.

Revision ID: 0002_enable_rls
Revises: 0001_initial
"""

from alembic import op

revision = "0002_enable_rls"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"
TABLES = ("alembic_version", "teachers", "study_chats", "lessons")


def upgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE "{SCHEMA}"."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f'ALTER TABLE "{SCHEMA}"."{table}" DISABLE ROW LEVEL SECURITY')
