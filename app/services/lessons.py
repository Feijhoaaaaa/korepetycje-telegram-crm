from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database.models import AuditLog, Lesson, StudyChat, Teacher
from app.utils.dates import day_bounds, month_bounds, week_bounds


class DomainError(ValueError):
    pass


class LessonService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def loaded() -> tuple:
        return (
            joinedload(Lesson.study_chat).joinedload(StudyChat.student),
            joinedload(Lesson.teacher),
            joinedload(Lesson.student),
            joinedload(Lesson.settlement),
        )

    @staticmethod
    def _visible(query: Select[tuple[Lesson]]) -> Select[tuple[Lesson]]:
        return query.where(Lesson.deleted_at.is_(None))

    @staticmethod
    def _scope(query, *, teacher_id: int | None = None, student_id: int | None = None, admin: bool = False):
        if admin:
            return query
        if student_id is not None:
            return query.where(Lesson.student_id == student_id)
        if teacher_id is not None:
            return query.where(Lesson.teacher_id == teacher_id)
        return query.where(False)

    async def get(
        self,
        lesson_id: int,
        *,
        include_deleted: bool = False,
        owner_id: int | None = None,
        teacher_id: int | None = None,
        student_id: int | None = None,
        admin: bool = False,
    ) -> Lesson | None:
        query = select(Lesson).options(*self.loaded()).where(Lesson.id == lesson_id)
        if owner_id is not None:
            query = query.join(Teacher, Lesson.teacher_id == Teacher.id).where(Teacher.telegram_user_id == owner_id)
        else:
            query = self._scope(query, teacher_id=teacher_id, student_id=student_id, admin=admin)
        if not include_deleted:
            query = query.where(Lesson.deleted_at.is_(None))
        return await self.session.scalar(query)

    async def create(
        self,
        study_chat_id: int,
        when_utc: datetime,
        *,
        duration_minutes: int | None = None,
        price: Decimal | None = None,
    ) -> Lesson:
        chat = await self.session.scalar(
            select(StudyChat)
            .options(joinedload(StudyChat.teacher), joinedload(StudyChat.student))
            .where(StudyChat.id == study_chat_id)
            .execution_options(populate_existing=True)
            .with_for_update(of=StudyChat)
        )
        if chat is None or chat.student_id is None:
            raise DomainError("student_not_assigned")
        lesson = Lesson(
            study_chat_id=study_chat_id,
            teacher_id=chat.teacher_id,
            student_id=chat.student_id,
            lesson_datetime=when_utc,
            duration_minutes=duration_minutes or chat.teacher.default_duration_minutes,
            price=price if price is not None else chat.teacher.default_price,
        )
        self.session.add(lesson)
        await self.session.flush()
        return lesson

    async def conflicts(
        self,
        when_utc: datetime,
        *,
        duration_minutes: int = 60,
        teacher_id: int | None = None,
        student_id: int | None = None,
        exclude_lesson_id: int | None = None,
    ) -> list[Lesson]:
        end = when_utc + timedelta(minutes=duration_minutes)
        query = (
            select(Lesson)
            .options(*self.loaded())
            .where(
                Lesson.lesson_datetime < end,
                Lesson.lesson_datetime + func.make_interval(0, 0, 0, 0, 0, Lesson.duration_minutes) > when_utc,
                Lesson.status == "planned",
                Lesson.deleted_at.is_(None),
            )
        )
        owners = [
            condition
            for condition in (
                Lesson.teacher_id == teacher_id if teacher_id else None,
                Lesson.student_id == student_id if student_id else None,
            )
            if condition is not None
        ]
        if owners:
            query = query.where(or_(*owners))
        if exclude_lesson_id is not None:
            query = query.where(Lesson.id != exclude_lesson_id)
        return list(await self.session.scalars(query))

    async def reschedule(self, lesson: Lesson, when_utc: datetime) -> Lesson:
        lesson = await self._locked(lesson)
        self._require(lesson)
        if lesson.lesson_datetime == when_utc:
            return lesson
        lesson.lesson_datetime = when_utc
        await self.session.flush()
        return lesson

    async def update(
        self,
        lesson: Lesson,
        *,
        actor_telegram_id: int,
        lesson_datetime: datetime | None = None,
        duration_minutes: int | None = None,
        price: Decimal | None | object = ...,
        teacher_id: int | None = None,
        student_id: int | None = None,
        study_chat_id: int | None = None,
    ) -> Lesson:
        self._require(lesson)
        before = self.snapshot(lesson)
        financial_change = False
        if lesson_datetime is not None:
            lesson.lesson_datetime = lesson_datetime
        if duration_minutes is not None:
            lesson.duration_minutes = duration_minutes
        if price is not ... and lesson.price != price:
            lesson.price = price
            financial_change = True
        if teacher_id is not None and teacher_id != lesson.teacher_id:
            lesson.teacher_id = teacher_id
            financial_change = True
        if student_id is not None and student_id != lesson.student_id:
            lesson.student_id = student_id
            financial_change = True
        if study_chat_id is not None:
            lesson.study_chat_id = study_chat_id
        if financial_change and lesson.settlement is not None:
            await self.session.delete(lesson.settlement)
            lesson.payment_status = "unpaid" if lesson.status == "completed" else "unknown"
        await self.session.flush()
        self.session.add(
            AuditLog(
                actor_telegram_id=actor_telegram_id,
                action="update",
                entity_type="lesson",
                entity_id=lesson.id,
                before=before,
                after=self.snapshot(lesson),
            )
        )
        return lesson

    async def cancel(self, lesson: Lesson) -> Lesson:
        lesson = await self._locked(lesson)
        if lesson.status == "cancelled":
            return lesson
        self._require(lesson, status="planned")
        lesson.status = "cancelled"
        lesson.payment_status = "unknown"
        await self.session.flush()
        return lesson

    async def complete(self, lesson: Lesson) -> Lesson:
        lesson = await self._locked(lesson)
        if lesson.status == "completed":
            return lesson
        self._require(lesson, status="planned")
        lesson.status = "completed"
        lesson.payment_status = "unpaid" if lesson.price is not None else "unknown"
        await self.session.flush()
        return lesson

    async def set_payment(self, lesson: Lesson, payment_status: str) -> Lesson:
        lesson = await self._locked(lesson)
        if payment_status not in {"paid", "unpaid"}:
            raise DomainError("invalid_payment_status")
        if lesson.payment_status == payment_status:
            return lesson
        self._require(lesson, status="completed")
        lesson.payment_status = payment_status
        await self.session.flush()
        return lesson

    async def soft_delete(self, lesson: Lesson, actor_id: int) -> Lesson:
        lesson = await self._locked(lesson)
        if lesson.deleted_at is not None:
            return lesson
        lesson.deleted_at = datetime.now(UTC)
        lesson.deleted_by_telegram_id = actor_id
        await self.session.flush()
        return lesson

    @staticmethod
    def snapshot(lesson: Lesson) -> dict:
        return {
            "datetime": lesson.lesson_datetime.isoformat(),
            "duration_minutes": lesson.duration_minutes,
            "price": str(lesson.price) if lesson.price is not None else None,
            "teacher_id": lesson.teacher_id,
            "student_id": lesson.student_id,
            "status": lesson.status,
            "payment_status": lesson.payment_status,
        }

    @staticmethod
    def _require(lesson: Lesson, *, status: str | None = None) -> None:
        if lesson.deleted_at is not None:
            raise DomainError("lesson_deleted")
        if status and lesson.status != status:
            raise DomainError("invalid_lesson_transition")

    async def _locked(self, lesson: Lesson) -> Lesson:
        locked = await self.session.scalar(
            select(Lesson)
            .options(*self.loaded())
            .where(Lesson.id == lesson.id)
            .execution_options(populate_existing=True)
            .with_for_update(of=Lesson)
        )
        if locked is None:
            raise DomainError("lesson_not_found")
        return locked

    async def upcoming(self, study_chat_id: int, *, offset: int = 0, limit: int = 10) -> list[Lesson]:
        return list(
            await self.session.scalars(
                select(Lesson)
                .where(
                    Lesson.study_chat_id == study_chat_id,
                    Lesson.status == "planned",
                    Lesson.deleted_at.is_(None),
                    Lesson.lesson_datetime >= datetime.now(UTC),
                )
                .order_by(Lesson.lesson_datetime)
                .offset(offset)
                .limit(limit)
            )
        )

    async def history(self, study_chat_id: int, start: datetime, end: datetime) -> list[Lesson]:
        return list(
            await self.session.scalars(
                select(Lesson)
                .where(
                    Lesson.study_chat_id == study_chat_id,
                    Lesson.deleted_at.is_(None),
                    Lesson.lesson_datetime >= start,
                    Lesson.lesson_datetime < end,
                )
                .order_by(Lesson.lesson_datetime.desc())
            )
        )

    async def owner_period(self, owner_id: int, start: datetime, end: datetime) -> list[Lesson]:
        return list(
            await self.session.scalars(
                select(Lesson)
                .options(*self.loaded())
                .join(Teacher, Lesson.teacher_id == Teacher.id)
                .where(
                    Teacher.telegram_user_id == owner_id,
                    Lesson.deleted_at.is_(None),
                    Lesson.status == "planned",
                    Lesson.lesson_datetime >= start,
                    Lesson.lesson_datetime < end,
                )
                .order_by(Lesson.lesson_datetime)
            )
        )

    async def list_scope(
        self,
        *,
        teacher_id: int | None = None,
        student_id: int | None = None,
        admin: bool = False,
        start: datetime | None = None,
        end: datetime | None = None,
        study_chat_id: int | None = None,
        status: str | None = None,
        payment_status: str | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> list[Lesson]:
        query = self._scope(
            select(Lesson).options(*self.loaded()).where(Lesson.deleted_at.is_(None)),
            teacher_id=teacher_id,
            student_id=student_id,
            admin=admin,
        )
        for value, condition in (
            (start, Lesson.lesson_datetime >= start if start else None),
            (end, Lesson.lesson_datetime < end if end else None),
            (study_chat_id, Lesson.study_chat_id == study_chat_id if study_chat_id else None),
            (status, Lesson.status == status if status else None),
            (payment_status, Lesson.payment_status == payment_status if payment_status else None),
        ):
            if value is not None:
                query = query.where(condition)
        return list(
            await self.session.scalars(query.order_by(Lesson.lesson_datetime.desc()).offset(offset).limit(limit))
        )

    async def list_owner(self, owner_id: int, **kwargs) -> list[Lesson]:
        teacher_id = await self.session.scalar(select(Teacher.id).where(Teacher.telegram_user_id == owner_id))
        return await self.list_scope(teacher_id=teacher_id, **kwargs)

    async def today(self, owner_id: int, day: date, timezone) -> list[Lesson]:
        return await self.owner_period(owner_id, *day_bounds(day, timezone))

    async def week(self, owner_id: int, day: date, timezone) -> list[Lesson]:
        return await self.owner_period(owner_id, *week_bounds(day, timezone))

    async def unpaid(self, owner_id: int) -> list[Lesson]:
        teacher_id = await self.session.scalar(select(Teacher.id).where(Teacher.telegram_user_id == owner_id))
        return await self.list_scope(teacher_id=teacher_id, status="completed", payment_status="unpaid", limit=1000)

    async def export_rows(
        self, owner_id: int, *, month: date | None = None, study_chat_id: int | None = None, timezone: ZoneInfo
    ) -> list[Lesson]:
        start = end = None
        if month is not None:
            start, end = month_bounds(month, timezone)
        teacher_id = await self.session.scalar(select(Teacher.id).where(Teacher.telegram_user_id == owner_id))
        rows = await self.list_scope(
            teacher_id=teacher_id, start=start, end=end, study_chat_id=study_chat_id, limit=10000
        )
        return sorted(rows, key=lambda row: row.lesson_datetime)
