from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database.models import (
    AuditLog,
    AvailabilityException,
    AvailabilityRule,
    FinancialEntry,
    Lesson,
    LessonSettlement,
    PaymentReceipt,
    ServiceRequest,
    Student,
    StudentLinkToken,
    StudyChat,
)
from app.services.lessons import DomainError, LessonService


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def parse_payment_amount(value: str) -> Decimal:
    value = value.strip()
    if not re.fullmatch(r"\d+(?:[.,]\d{1,2})?", value):
        raise ValueError("invalid_amount")
    try:
        amount = money(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("invalid_amount") from exc
    if amount <= 0 or amount > Decimal("9999999999.99"):
        raise ValueError("invalid_amount")
    return amount


def quiet_now(start: time | None, end: time | None, now: time) -> bool:
    if start is None or end is None or start == end:
        return False
    return start <= now < end if start < end else now >= start or now < end


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def balances(self, student_id: int) -> dict[str, Decimal]:
        rows = await self.session.execute(
            select(FinancialEntry.currency, func.sum(FinancialEntry.amount))
            .where(FinancialEntry.student_id == student_id)
            .group_by(FinancialEntry.currency)
        )
        return {currency: money(total) for currency, total in rows}

    async def balance(self, student_id: int, currency: str = "PLN") -> Decimal:
        return (await self.balances(student_id)).get(currency, Decimal("0.00"))

    async def available_pool(self, student_id: int, currency: str = "PLN") -> Decimal:
        total = await self.session.scalar(
            select(func.coalesce(func.sum(FinancialEntry.amount), 0)).where(
                FinancialEntry.student_id == student_id,
                FinancialEntry.currency == currency,
            )
        )
        return money(total)

    async def history(self, student_id: int) -> list[dict]:
        entries = list(
            await self.session.scalars(
                select(FinancialEntry).where(FinancialEntry.student_id == student_id)
            )
        )
        rows = [
            {
                "datetime": row.created_at,
                "kind": row.kind,
                "description": row.description or row.kind,
                "amount": money(row.amount),
                "currency": row.currency,
                "id": row.id,
            }
            for row in entries
        ]
        for currency in {row["currency"] for row in rows}:
            running = Decimal("0.00")
            for row in sorted(
                (item for item in rows if item["currency"] == currency),
                key=lambda item: (item["datetime"], item["kind"], item["id"]),
            ):
                running += row["amount"]
                row["balance"] = money(running)
        return sorted(rows, key=lambda item: item["datetime"], reverse=True)

    async def submit_receipt(
        self, student: Student, file_id: str, file_type: str, submitted_amount: Decimal, currency: str = "PLN"
    ) -> PaymentReceipt:
        existing = await self.session.scalar(
            select(PaymentReceipt)
            .options(joinedload(PaymentReceipt.request))
            .where(PaymentReceipt.student_id == student.id, PaymentReceipt.telegram_file_id == file_id)
        )
        if existing is not None:
            return existing
        receipt = PaymentReceipt(
            student_id=student.id,
            teacher_id=student.teacher_id,
            telegram_file_id=file_id,
            telegram_file_type=file_type,
            submitted_amount=money(submitted_amount),
            currency=currency,
        )
        self.session.add(receipt)
        await self.session.flush()
        receipt.request = ServiceRequest(
            type="payment_confirmation",
            student_id=student.id,
            teacher_id=student.teacher_id,
            payment_id=receipt.id,
        )
        self.session.add(receipt.request)
        await self.session.flush()
        return receipt

    async def mark_receipt_delivered(self, receipt: PaymentReceipt, chat_id: int, message_id: int) -> None:
        receipt = await self._locked_receipt(receipt)
        if receipt.teacher_message_id is not None:
            return
        receipt.teacher_chat_id = chat_id
        receipt.teacher_message_id = message_id
        await self.session.flush()

    async def receipt(
        self,
        receipt_id: int,
        *,
        teacher_id: int | None = None,
        student_id: int | None = None,
        admin: bool = False,
        lock: bool = False,
    ) -> PaymentReceipt | None:
        query = (
            select(PaymentReceipt)
            .options(joinedload(PaymentReceipt.student), joinedload(PaymentReceipt.request))
            .where(PaymentReceipt.id == receipt_id)
        )
        if not admin:
            query = (
                query.where(PaymentReceipt.teacher_id == teacher_id)
                if teacher_id
                else query.where(PaymentReceipt.student_id == student_id)
            )
        if lock:
            query = query.with_for_update(of=PaymentReceipt)
        return await self.session.scalar(query)

    async def list_receipts(
        self,
        *,
        teacher_id: int | None = None,
        student_id: int | None = None,
        admin: bool = False,
        status: str | None = None,
    ) -> list[PaymentReceipt]:
        query = select(PaymentReceipt).options(joinedload(PaymentReceipt.student), joinedload(PaymentReceipt.request))
        if not admin:
            query = (
                query.where(PaymentReceipt.teacher_id == teacher_id)
                if teacher_id
                else query.where(PaymentReceipt.student_id == student_id)
            )
        if status:
            query = query.join(ServiceRequest, ServiceRequest.payment_id == PaymentReceipt.id).where(
                ServiceRequest.status == status
            )
        return list(await self.session.scalars(query.order_by(PaymentReceipt.created_at.desc())))

    async def approve(
        self,
        receipt: PaymentReceipt,
        amount: Decimal,
        lesson_ids: list[int],
        reviewer_id: int,
        actor_telegram_id: int,
        currency: str = "PLN",
    ) -> bool:
        receipt = await self._locked_receipt(receipt)
        amount = money(amount)
        if receipt.status == "approved" and receipt.amount == amount:
            return False
        if receipt.status != "pending":
            raise DomainError("receipt_already_reviewed")
        if amount <= 0:
            raise DomainError("invalid_amount")
        if currency not in {"PLN", "UAH", "EUR"}:
            raise DomainError("invalid_currency")
        await self._lock_student(receipt.student_id)
        receipt.amount = amount
        receipt.currency = currency
        receipt.request.status = "approved"
        receipt.request.comment = None
        receipt.request.resolved_by_teacher_id = reviewer_id
        receipt.request.resolved_at = datetime.now(UTC)
        self.session.add(
            FinancialEntry(
                student_id=receipt.student_id,
                teacher_id=receipt.teacher_id,
                kind="payment",
                amount=amount,
                currency=currency,
                payment_id=receipt.id,
                actor_telegram_id=actor_telegram_id,
                description="Wpłata",
            )
        )
        await self.session.flush()
        pool = await self.available_pool(receipt.student_id, currency)
        lessons = (
            list(
                await self.session.scalars(
                    select(Lesson)
                    .options(joinedload(Lesson.settlement))
                    .where(Lesson.id.in_(lesson_ids))
                    .with_for_update(of=Lesson)
                )
            )
            if lesson_ids
            else []
        )
        if len(lessons) != len(set(lesson_ids)):
            raise DomainError("lesson_not_found")
        for lesson in lessons:
            if (
                lesson.student_id != receipt.student_id
                or lesson.status != "completed"
                or lesson.price is None
                or lesson.settlement
                or currency != "PLN"
            ):
                raise DomainError("lesson_not_settleable")
            price = money(lesson.price)
            if pool < price:
                raise DomainError("insufficient_unallocated_funds")
            self.session.add(
                LessonSettlement(
                    lesson_id=lesson.id,
                    student_id=lesson.student_id,
                    amount=price,
                    confirmed_by_teacher_id=reviewer_id,
                )
            )
            self.session.add(
                FinancialEntry(
                    student_id=lesson.student_id,
                    teacher_id=lesson.teacher_id,
                    kind="lesson_charge",
                    amount=-price,
                    currency="PLN",
                    lesson_id=lesson.id,
                    actor_telegram_id=actor_telegram_id,
                    description="Lekcja",
                )
            )
            lesson.payment_status = "paid"
            pool -= price
        self.session.add(
            AuditLog(
                actor_telegram_id=actor_telegram_id,
                action="approve",
                entity_type="payment_receipt",
                entity_id=receipt.id,
                before={"status": "pending"},
                after={"status": "approved", "amount": str(amount), "currency": currency, "lesson_ids": lesson_ids},
            )
        )
        await self.session.flush()
        return True

    async def reject(self, receipt: PaymentReceipt, reason: str, reviewer_id: int, actor_telegram_id: int) -> bool:
        receipt = await self._locked_receipt(receipt)
        reason = reason.strip()
        if receipt.status == "rejected" and receipt.rejection_reason == reason:
            return False
        if receipt.status != "pending":
            raise DomainError("receipt_already_reviewed")
        receipt.request.status = "rejected"
        receipt.request.comment = reason
        receipt.request.resolved_by_teacher_id = reviewer_id
        receipt.request.resolved_at = datetime.now(UTC)
        self.session.add(
            AuditLog(
                actor_telegram_id=actor_telegram_id,
                action="reject",
                entity_type="payment_receipt",
                entity_id=receipt.id,
                before={"status": "pending"},
                after={"status": "rejected", "reason": receipt.rejection_reason},
            )
        )
        await self.session.flush()
        return True

    async def correct_receipt(
        self,
        receipt: PaymentReceipt,
        *,
        status: str,
        amount: Decimal | None,
        reason: str | None,
        reviewer_id: int,
        actor_telegram_id: int,
        currency: str = "PLN",
    ) -> None:
        if status not in {"approved", "rejected"} or (status == "approved" and amount is None):
            raise DomainError("invalid_payment_correction")
        receipt = await self._locked_receipt(receipt)
        before = {
            "status": receipt.status,
            "amount": str(receipt.amount) if receipt.amount else None,
            "currency": receipt.currency,
        }
        new_amount = money(amount or 0) if status == "approved" else Decimal("0.00")
        if currency not in {"PLN", "UAH", "EUR"}:
            raise DomainError("invalid_currency")
        old_amount = money(receipt.amount or 0)
        if receipt.status in {"approved", "rejected"}:
            old_pool = await self.available_pool(receipt.student_id, receipt.currency)
            projected_old_pool = old_pool - old_amount
            if receipt.status == "approved" and status == "approved" and receipt.currency == currency:
                projected_old_pool += new_amount
            if projected_old_pool < 0:
                raise DomainError("payment_funds_allocated")
        if receipt.status == "pending" and status == "approved":
            self.session.add(
                FinancialEntry(
                    student_id=receipt.student_id,
                    teacher_id=receipt.teacher_id,
                    kind="payment",
                    amount=new_amount,
                    currency=currency,
                    payment_id=receipt.id,
                    actor_telegram_id=actor_telegram_id,
                    description="Wpłata",
                )
            )
        if receipt.status == "approved" and (old_amount != new_amount or receipt.currency != currency):
            self.session.add(
                FinancialEntry(
                    student_id=receipt.student_id,
                    teacher_id=receipt.teacher_id,
                    kind="manual_adjustment",
                    amount=-old_amount,
                    currency=receipt.currency,
                    actor_telegram_id=actor_telegram_id,
                    description="Korekta płatności",
                )
            )
            if status == "approved":
                self.session.add(
                    FinancialEntry(
                        student_id=receipt.student_id,
                        teacher_id=receipt.teacher_id,
                        kind="manual_adjustment",
                        amount=new_amount,
                        currency=currency,
                        actor_telegram_id=actor_telegram_id,
                        description="Korekta płatności",
                    )
                )
        receipt.amount = new_amount if status == "approved" else None
        receipt.currency = currency
        receipt.request.status = status
        receipt.request.comment = reason.strip() if status == "rejected" and reason else None
        receipt.request.resolved_by_teacher_id = reviewer_id
        receipt.request.resolved_at = datetime.now(UTC)
        self.session.add(
            AuditLog(
                actor_telegram_id=actor_telegram_id,
                action="correct",
                entity_type="payment_receipt",
                entity_id=receipt.id,
                before=before,
                after={
                    "status": status,
                    "amount": str(receipt.amount) if receipt.amount else None,
                    "currency": currency,
                },
            )
        )
        await self.session.flush()

    async def settle_lesson(self, lesson: Lesson, reviewer_id: int, actor_telegram_id: int) -> None:
        lesson = await self._locked_lesson(lesson)
        await self._lock_student(lesson.student_id)
        if lesson.settlement:
            lesson.payment_status = "paid"
            await self.session.flush()
            return
        if lesson.status != "completed" or lesson.price is None:
            raise DomainError("lesson_not_settleable")
        price = money(lesson.price)
        if await self.available_pool(lesson.student_id) < price:
            raise DomainError("insufficient_unallocated_funds")
        self.session.add(
            LessonSettlement(
                lesson_id=lesson.id, student_id=lesson.student_id, amount=price, confirmed_by_teacher_id=reviewer_id
            )
        )
        self.session.add(
            FinancialEntry(
                student_id=lesson.student_id,
                teacher_id=lesson.teacher_id,
                kind="lesson_charge",
                amount=-price,
                currency="PLN",
                lesson_id=lesson.id,
                actor_telegram_id=actor_telegram_id,
                description="Lekcja",
            )
        )
        lesson.payment_status = "paid"
        self.session.add(
            AuditLog(
                actor_telegram_id=actor_telegram_id,
                action="settle",
                entity_type="lesson",
                entity_id=lesson.id,
                before={"payment_status": "unpaid"},
                after={"payment_status": "paid", "amount": str(price)},
            )
        )
        await self.session.flush()

    async def un_settle_lesson(self, lesson: Lesson, actor_telegram_id: int) -> None:
        lesson = await self._locked_lesson(lesson)
        await self._lock_student(lesson.student_id)
        if lesson.settlement is None:
            lesson.payment_status = "unpaid"
            await self.session.flush()
            return
        amount = str(lesson.settlement.amount)
        await self.session.delete(lesson.settlement)
        self.session.add(
            FinancialEntry(
                student_id=lesson.student_id,
                teacher_id=lesson.teacher_id,
                kind="refund",
                amount=money(lesson.settlement.amount),
                currency="PLN",
                lesson_id=lesson.id,
                actor_telegram_id=actor_telegram_id,
                description="Zwrot za lekcję",
            )
        )
        lesson.payment_status = "unpaid"
        self.session.add(
            AuditLog(
                actor_telegram_id=actor_telegram_id,
                action="unsettle",
                entity_type="lesson",
                entity_id=lesson.id,
                before={"payment_status": "paid", "amount": amount},
                after={"payment_status": "unpaid"},
            )
        )
        await self.session.flush()

    async def _lock_student(self, student_id: int | None) -> None:
        if student_id is not None:
            await self.session.scalar(select(Student.id).where(Student.id == student_id).with_for_update())

    async def _locked_receipt(self, receipt: PaymentReceipt) -> PaymentReceipt:
        locked = await self.session.scalar(
            select(PaymentReceipt)
            .options(joinedload(PaymentReceipt.student), joinedload(PaymentReceipt.request))
            .where(PaymentReceipt.id == receipt.id)
            .execution_options(populate_existing=True)
            .with_for_update(of=PaymentReceipt)
        )
        if locked is None:
            raise DomainError("payment_not_found")
        request = await self.session.scalar(
            select(ServiceRequest)
            .where(ServiceRequest.payment_id == locked.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if request is None:
            raise DomainError("payment_request_not_found")
        locked.request = request
        return locked

    async def _locked_lesson(self, lesson: Lesson) -> Lesson:
        locked = await self.session.scalar(
            select(Lesson)
            .options(*LessonService.loaded())
            .where(Lesson.id == lesson.id)
            .execution_options(populate_existing=True)
            .with_for_update(of=Lesson)
        )
        if locked is None:
            raise DomainError("lesson_not_found")
        return locked


class AvailabilityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_rules(
        self, *, teacher_id: int | None, student_id: int | None, rules: list[dict]
    ) -> list[AvailabilityRule]:
        owner = AvailabilityRule.teacher_id == teacher_id if teacher_id else AvailabilityRule.student_id == student_id
        await self.session.execute(delete(AvailabilityRule).where(owner))
        result = [AvailabilityRule(teacher_id=teacher_id, student_id=student_id, **rule) for rule in rules]
        self.session.add_all(result)
        await self.session.flush()
        return result

    async def set_exception(
        self,
        *,
        teacher_id: int | None,
        student_id: int | None,
        exception_date: date,
        available: bool,
        start_time: time | None,
        end_time: time | None,
    ) -> AvailabilityException:
        owner = (
            AvailabilityException.teacher_id == teacher_id
            if teacher_id
            else AvailabilityException.student_id == student_id
        )
        await self.session.execute(
            delete(AvailabilityException).where(owner, AvailabilityException.exception_date == exception_date)
        )
        row = AvailabilityException(
            teacher_id=teacher_id,
            student_id=student_id,
            exception_date=exception_date,
            available=available,
            start_time=start_time,
            end_time=end_time,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def intervals(
        self, day: date, *, teacher_id: int | None = None, student_id: int | None = None
    ) -> list[tuple[time, time]]:
        owner_exc = (
            AvailabilityException.teacher_id == teacher_id
            if teacher_id
            else AvailabilityException.student_id == student_id
        )
        exceptions = list(
            await self.session.scalars(
                select(AvailabilityException).where(owner_exc, AvailabilityException.exception_date == day)
            )
        )
        if exceptions:
            return [
                (row.start_time, row.end_time)
                for row in exceptions
                if row.available and row.start_time and row.end_time
            ]
        owner_rule = (
            AvailabilityRule.teacher_id == teacher_id if teacher_id else AvailabilityRule.student_id == student_id
        )
        rules = list(
            await self.session.scalars(
                select(AvailabilityRule)
                .where(owner_rule, AvailabilityRule.weekday == day.weekday())
                .order_by(AvailabilityRule.start_time)
            )
        )
        return [(row.start_time, row.end_time) for row in rules]

    async def common(self, day: date, teacher_id: int, student_id: int) -> list[tuple[time, time]]:
        teacher = await self.intervals(day, teacher_id=teacher_id)
        student = await self.intervals(day, student_id=student_id)
        return [(max(a, c), min(b, d)) for a, b in teacher for c, d in student if max(a, c) < min(b, d)]

    async def contains(self, start: datetime, duration_minutes: int, teacher_id: int, student_id: int) -> bool:
        intervals = await self.common(start.date(), teacher_id, student_id)
        finish = (start + timedelta(minutes=duration_minutes)).time()
        return any(begin <= start.time() and finish <= end for begin, end in intervals)


class LinkService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, student_id: int, ttl_minutes: int = 30) -> str:
        token = secrets.token_urlsafe(24)
        self.session.add(
            StudentLinkToken(
                student_id=student_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
            )
        )
        await self.session.flush()
        return token

    async def claim(self, token: str, telegram_user_id: int) -> StudentLinkToken:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = await self.session.scalar(
            select(StudentLinkToken)
            .where(StudentLinkToken.token_hash == token_hash)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if row is None or row.expires_at <= datetime.now(UTC):
            raise DomainError("link_expired")
        if row.claimed_at is not None:
            raise DomainError("link_already_used")
        row.claimed_at, row.claimed_telegram_user_id = datetime.now(UTC), telegram_user_id
        await self.session.flush()
        return row

    async def approve(self, link_id: int, reviewer_id: int) -> Student:
        row = await self.session.scalar(
            select(StudentLinkToken)
            .where(StudentLinkToken.id == link_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if row is None or row.claimed_at is None or row.expires_at <= datetime.now(UTC):
            raise DomainError("invalid_link_request")
        student = await self.session.scalar(
            select(Student).where(Student.id == row.student_id).with_for_update()
        )
        if student is None:
            raise DomainError("student_not_found")
        if row.approved_at is not None:
            if student.telegram_user_id == row.claimed_telegram_user_id:
                if not student.active:
                    student.active = True
                    await self.session.execute(
                        StudyChat.__table__.update().where(StudyChat.student_id == student.id).values(active=True)
                    )
                return student
            raise DomainError("invalid_link_request")
        duplicate = await self.session.scalar(
            select(Student).where(
                Student.telegram_user_id == row.claimed_telegram_user_id,
                Student.id != student.id,
            ).with_for_update()
        )
        if duplicate is not None:
            if not duplicate.active and duplicate.teacher_id == student.teacher_id:
                raise DomainError("telegram_user_linked_to_archived_student")
            raise DomainError("telegram_user_already_linked")
        student.telegram_user_id = row.claimed_telegram_user_id
        if not student.active:
            student.active = True
            await self.session.execute(
                StudyChat.__table__.update().where(StudyChat.student_id == student.id).values(active=True)
            )
        row.approved_at, row.approved_by_teacher_id = datetime.now(UTC), reviewer_id
        await self.session.flush()
        return student
