"""Store the Telegram title of study chats.

Revision ID: 0003_chat_title
Revises: 0002_enable_rls
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_chat_title"
down_revision = "0002_enable_rls"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def upgrade() -> None:
    op.add_column("study_chats", sa.Column("telegram_chat_title", sa.Text()), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("study_chats", "telegram_chat_title", schema=SCHEMA)
