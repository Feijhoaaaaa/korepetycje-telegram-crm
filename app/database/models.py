from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "tutor_bot"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class QuietHoursMixin:
    quiet_hours_start: Mapped[time | None] = mapped_column(Time())
    quiet_hours_end: Mapped[time | None] = mapped_column(Time())


class Teacher(QuietHoursMixin, TimestampMixin, Base):
    __tablename__ = "teachers"
    __table_args__: ClassVar[dict[str, str]] = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    default_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    default_duration_minutes: Mapped[int] = mapped_column(Integer, default=60, server_default="60", nullable=False)

    study_chats: Mapped[list[StudyChat]] = relationship(back_populates="teacher")
    students: Mapped[list[Student]] = relationship(back_populates="teacher")


class Student(QuietHoursMixin, TimestampMixin, Base):
    __tablename__ = "students"
    __table_args__ = (Index("ix_students_teacher_active", "teacher_id", "active"), {"schema": SCHEMA})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id", ondelete="RESTRICT"), nullable=False)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    teacher: Mapped[Teacher] = relationship(back_populates="students")
    study_chats: Mapped[list[StudyChat]] = relationship(back_populates="student")


class StudyChat(TimestampMixin, Base):
    __tablename__ = "study_chats"
    __table_args__ = (Index("ix_study_chats_teacher_active", "teacher_id", "active"), {"schema": SCHEMA})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    telegram_chat_title: Mapped[str | None] = mapped_column(Text)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id", ondelete="SET NULL"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.students.id", ondelete="SET NULL"))
    # Legacy mirrors retained so existing group integrations keep working during migration.
    student_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    student_name: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    teacher: Mapped[Teacher | None] = relationship(back_populates="study_chats")
    student: Mapped[Student | None] = relationship(back_populates="study_chats")
    lessons: Mapped[list[Lesson]] = relationship(back_populates="study_chat")


class Lesson(TimestampMixin, Base):
    __tablename__ = "lessons"
    __table_args__ = (
        CheckConstraint("status IN ('planned', 'completed', 'cancelled')", name="lesson_status_check"),
        CheckConstraint("payment_status IN ('unknown', 'paid', 'unpaid')", name="payment_status_check"),
        CheckConstraint("duration_minutes > 0", name="lesson_duration_check"),
        CheckConstraint("price IS NULL OR price >= 0", name="lesson_price_check"),
        Index("ix_lessons_chat_datetime", "study_chat_id", "lesson_datetime"),
        Index("ix_lessons_teacher_datetime", "teacher_id", "lesson_datetime"),
        Index("ix_lessons_student_datetime", "student_id", "lesson_datetime"),
        Index("ix_lessons_status_datetime", "status", "lesson_datetime"),
        Index("ix_lessons_payment", "payment_status", "deleted_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    study_chat_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.study_chats.id", ondelete="SET NULL"))
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id", ondelete="SET NULL"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.students.id", ondelete="SET NULL"))
    student_name_snapshot: Mapped[str | None] = mapped_column(Text)
    teacher_name_snapshot: Mapped[str | None] = mapped_column(Text)
    lesson_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60, server_default="60", nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(16), default="planned", nullable=False)
    payment_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)

    study_chat: Mapped[StudyChat | None] = relationship(back_populates="lessons")
    teacher: Mapped[Teacher | None] = relationship()
    student: Mapped[Student | None] = relationship()
    settlement: Mapped[LessonSettlement | None] = relationship(back_populates="lesson", uselist=False)


class PaymentReceipt(TimestampMixin, Base):
    __tablename__ = "payment_receipts"
    __table_args__ = (
        CheckConstraint("amount IS NULL OR amount > 0", name="payment_receipt_amount_check"),
        CheckConstraint(
            "submitted_amount IS NULL OR submitted_amount > 0", name="payment_receipt_submitted_amount_check"
        ),
        CheckConstraint("currency IN ('PLN', 'UAH', 'EUR')", name="payment_receipt_currency_check"),
        Index("uq_payment_receipts_student_file", "student_id", "telegram_file_id", unique=True),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.students.id", ondelete="RESTRICT"), nullable=False)
    teacher_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id", ondelete="RESTRICT"), nullable=False)
    telegram_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    submitted_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="PLN", server_default="PLN", nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    teacher_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    teacher_message_id: Mapped[int | None] = mapped_column(BigInteger)

    student: Mapped[Student] = relationship(foreign_keys=[student_id])
    request: Mapped[ServiceRequest] = relationship(back_populates="payment", uselist=False, lazy="selectin")

    @property
    def status(self) -> str:
        return self.request.status

    @property
    def rejection_reason(self) -> str | None:
        return self.request.comment if self.request.status == "rejected" else None

    @property
    def reviewed_at(self) -> datetime | None:
        return self.request.resolved_at


class FinancialEntry(TimestampMixin, Base):
    __tablename__ = "financial_entries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('payment', 'lesson_charge', 'refund', 'manual_adjustment')",
            name="financial_entry_kind_check",
        ),
        CheckConstraint("currency IN ('PLN', 'UAH', 'EUR')", name="financial_entry_currency_check"),
        CheckConstraint("amount <> 0", name="financial_entry_amount_check"),
        UniqueConstraint("kind", "payment_id", name="uq_financial_entry_payment_kind"),
        UniqueConstraint("kind", "lesson_id", name="uq_financial_entry_lesson_kind"),
        Index("ix_financial_entries_student_currency_created", "student_id", "currency", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.students.id", ondelete="RESTRICT"), nullable=False)
    teacher_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.payment_receipts.id", ondelete="RESTRICT"))
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.lessons.id", ondelete="RESTRICT"))
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(Text)

class ServiceRequest(TimestampMixin, Base):
    __tablename__ = "service_requests"
    __table_args__ = (
        CheckConstraint(
            "type IN ('payment_confirmation', 'lesson_reschedule', 'lesson_cancellation', "
            "'lesson_time_proposal', 'other')",
            name="service_request_type_check",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')",
            name="service_request_status_check",
        ),
        CheckConstraint("reminder_count >= 0", name="service_request_reminder_count_check"),
        CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL) OR (status <> 'pending' AND resolved_at IS NOT NULL)",
            name="service_request_resolution_check",
        ),
        CheckConstraint(
            "(type = 'payment_confirmation' AND payment_id IS NOT NULL AND lesson_id IS NULL) OR "
            "(type IN ('lesson_reschedule', 'lesson_cancellation') AND lesson_id IS NOT NULL "
            "AND payment_id IS NULL) OR "
            "(type IN ('lesson_time_proposal', 'other') AND payment_id IS NULL)",
            name="service_request_reference_check",
        ),
        Index("ix_service_requests_teacher_status_created", "teacher_id", "status", "created_at"),
        Index("ix_service_requests_student_status_created", "student_id", "status", "created_at"),
        Index(
            "ix_service_requests_pending_reminder",
            "next_reminder_at",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "uq_service_requests_pending_lesson_type",
            "lesson_id",
            "type",
            unique=True,
            postgresql_where=text("status = 'pending' AND lesson_id IS NOT NULL"),
        ),
        Index(
            "uq_service_requests_pending_dedupe",
            "student_id",
            "type",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'pending' AND dedupe_key IS NOT NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.students.id", ondelete="CASCADE"), nullable=False)
    teacher_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id", ondelete="CASCADE"), nullable=False)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.lessons.id", ondelete="CASCADE"))
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.payment_receipts.id", ondelete="CASCADE"), unique=True
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.teachers.id", ondelete="SET NULL")
    )
    next_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    student: Mapped[Student] = relationship(foreign_keys=[student_id])
    teacher: Mapped[Teacher] = relationship(foreign_keys=[teacher_id])
    lesson: Mapped[Lesson | None] = relationship()
    payment: Mapped[PaymentReceipt | None] = relationship(back_populates="request", foreign_keys=[payment_id])


class LessonSettlement(TimestampMixin, Base):
    __tablename__ = "lesson_settlements"
    __table_args__ = (
        UniqueConstraint("lesson_id", name="uq_lesson_settlements_lesson"),
        CheckConstraint("amount > 0", name="lesson_settlement_amount_check"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.lessons.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.students.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    confirmed_by_teacher_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id"), nullable=False)

    lesson: Mapped[Lesson] = relationship(back_populates="settlement")


class AvailabilityRule(TimestampMixin, Base):
    __tablename__ = "availability_rules"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="availability_weekday_check"),
        CheckConstraint("start_time < end_time", name="availability_time_check"),
        CheckConstraint("num_nonnulls(teacher_id, student_id) = 1", name="availability_owner_check"),
        Index("ix_availability_owner_weekday", "teacher_id", "student_id", "weekday"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id", ondelete="CASCADE"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.students.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time(), nullable=False)
    end_time: Mapped[time] = mapped_column(Time(), nullable=False)


class AvailabilityException(TimestampMixin, Base):
    __tablename__ = "availability_exceptions"
    __table_args__ = (
        CheckConstraint("start_time IS NULL OR start_time < end_time", name="availability_exception_time_check"),
        CheckConstraint("num_nonnulls(teacher_id, student_id) = 1", name="availability_exception_owner_check"),
        Index("ix_availability_exception_owner_date", "teacher_id", "student_id", "exception_date"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id", ondelete="CASCADE"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.students.id", ondelete="CASCADE"))
    exception_date: Mapped[date] = mapped_column(Date, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time())
    end_time: Mapped[time | None] = mapped_column(Time())


class StudentLinkToken(TimestampMixin, Base):
    __tablename__ = "student_link_tokens"
    __table_args__: ClassVar[dict[str, str]] = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.students.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_teacher_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.teachers.id"))


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"), {"schema": SCHEMA})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
