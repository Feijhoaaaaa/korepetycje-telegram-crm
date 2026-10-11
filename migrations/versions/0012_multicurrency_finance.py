"""Add payment currencies and an immutable financial journal.

Revision ID: 0012_multicurrency_finance
Revises: 0011_request_reminder_index
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_multicurrency_finance"
down_revision = "0011_request_reminder_index"
branch_labels = None
depends_on = None

SCHEMA = "tutor_bot"


def upgrade() -> None:
    op.add_column(
        "payment_receipts",
        sa.Column("currency", sa.String(3), server_default="PLN", nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "payment_receipt_currency_check",
        "payment_receipts",
        "currency IN ('PLN', 'UAH', 'EUR')",
        schema=SCHEMA,
    )
    op.create_table(
        "financial_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("student_id", sa.BigInteger(), nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("payment_id", sa.BigInteger()),
        sa.Column("lesson_id", sa.BigInteger()),
        sa.Column("actor_telegram_id", sa.BigInteger()),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('payment', 'lesson_charge', 'refund', 'manual_adjustment')",
            name="financial_entry_kind_check",
        ),
        sa.CheckConstraint("currency IN ('PLN', 'UAH', 'EUR')", name="financial_entry_currency_check"),
        sa.CheckConstraint("amount <> 0", name="financial_entry_amount_check"),
        sa.UniqueConstraint("kind", "payment_id", name="uq_financial_entry_payment_kind"),
        sa.UniqueConstraint("kind", "lesson_id", name="uq_financial_entry_lesson_kind"),
        sa.ForeignKeyConstraint(["student_id"], [f"{SCHEMA}.students.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["teacher_id"], [f"{SCHEMA}.teachers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_id"], [f"{SCHEMA}.payment_receipts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lesson_id"], [f"{SCHEMA}.lessons.id"], ondelete="RESTRICT"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_financial_entries_student_currency_created",
        "financial_entries",
        ["student_id", "currency", "created_at"],
        schema=SCHEMA,
    )
    op.execute(f'ALTER TABLE "{SCHEMA}"."financial_entries" ENABLE ROW LEVEL SECURITY')
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.financial_entries
            (student_id, teacher_id, kind, amount, currency, payment_id, description)
        SELECT p.student_id, p.teacher_id, 'payment', p.amount, p.currency, p.id, 'Wpłata'
        FROM {SCHEMA}.payment_receipts p
        JOIN {SCHEMA}.service_requests r ON r.payment_id = p.id
        WHERE r.status = 'approved' AND p.amount IS NOT NULL
        """
    )
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.financial_entries
            (student_id, teacher_id, kind, amount, currency, lesson_id, description)
        SELECT l.student_id, l.teacher_id, 'lesson_charge', -s.amount, 'PLN', l.id, 'Lekcja'
        FROM {SCHEMA}.lesson_settlements s
        JOIN {SCHEMA}.lessons l ON l.id = s.lesson_id
        """
    )


def downgrade() -> None:
    op.drop_table("financial_entries", schema=SCHEMA)
    op.drop_constraint("payment_receipt_currency_check", "payment_receipts", schema=SCHEMA, type_="check")
    op.drop_column("payment_receipts", "currency", schema=SCHEMA)
