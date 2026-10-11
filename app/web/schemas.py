from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AssignStudentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name_required")
        return value


class LessonTimeRequest(BaseModel):
    date: dt.date
    time: dt.time
    duration_minutes: int = Field(default=60, ge=15, le=480)
    force: bool = False


class CreateLessonRequest(LessonTimeRequest):
    study_chat_id: int
    price: Decimal | None = Field(default=None, ge=0, decimal_places=2)


class UpdateLessonRequest(BaseModel):
    date: dt.date | None = None
    time: dt.time | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=480)
    price: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    price_set: bool = False
    teacher_id: int | None = None
    student_id: int | None = None
    study_chat_id: int | None = None
    force: bool = False

    @model_validator(mode="after")
    def date_and_time_together(self):
        if (self.date is None) != (self.time is None):
            raise ValueError("date_and_time_required")
        return self


class PaymentRequest(BaseModel):
    payment_status: str

    @field_validator("payment_status")
    @classmethod
    def valid_payment(cls, value: str) -> str:
        if value not in {"paid", "unpaid"}:
            raise ValueError("invalid_payment_status")
        return value


class TeacherRequest(BaseModel):
    telegram_user_id: int
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    is_admin: bool = False
    default_price: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    default_duration_minutes: int = Field(default=60, ge=15, le=480)
    quiet_hours_start: dt.time | None = None
    quiet_hours_end: dt.time | None = None


class TransferStudentRequest(BaseModel):
    teacher_id: int


class ReviewPaymentRequest(BaseModel):
    amount: Decimal = Field(gt=0, decimal_places=2)
    lesson_ids: list[int] = Field(default_factory=list)
    currency: Literal["PLN", "UAH", "EUR"] = "PLN"


class RejectPaymentRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CorrectPaymentRequest(BaseModel):
    status: str
    amount: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    currency: Literal["PLN", "UAH", "EUR"] = "PLN"
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in {"approved", "rejected"}:
            raise ValueError("invalid_payment_status")
        return value


class AvailabilityRuleRequest(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: dt.time
    end_time: dt.time

    @model_validator(mode="after")
    def valid_interval(self):
        if self.start_time >= self.end_time:
            raise ValueError("invalid_interval")
        return self


class AvailabilityRequest(BaseModel):
    rules: list[AvailabilityRuleRequest] = Field(max_length=100)


class AvailabilityExceptionRequest(BaseModel):
    date: dt.date
    available: bool
    start_time: dt.time | None = None
    end_time: dt.time | None = None

    @model_validator(mode="after")
    def valid_interval(self):
        if self.available and (self.start_time is None or self.end_time is None or self.start_time >= self.end_time):
            raise ValueError("available_interval_required")
        return self


class RescheduleCreateRequest(LessonTimeRequest):
    reason: str | None = Field(default=None, max_length=500)


class CancellationCreateRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class TimeProposalRequest(LessonTimeRequest):
    study_chat_id: int | None = None
    reason: str | None = Field(default=None, max_length=500)


class CounterProposalRequest(BaseModel):
    date: dt.date
    time: dt.time
    duration_minutes: int | None = Field(default=None, ge=15, le=480)
    force: bool = False
    reason: str | None = Field(default=None, max_length=500)


class DecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    force: bool = False


class ReminderSnoozeRequest(BaseModel):
    minutes: Literal[60, 180, 1440]


class QuietHoursRequest(BaseModel):
    start: dt.time | None = None
    end: dt.time | None = None

    @model_validator(mode="after")
    def paired(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("quiet_hours_pair_required")
        return self
