from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database.models import Student, StudyChat, Teacher


class ChatService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure_teacher(self, telegram_user_id: int, name: str | None, *, is_admin: bool = False) -> Teacher:
        teacher = await self.session.scalar(select(Teacher).where(Teacher.telegram_user_id == telegram_user_id))
        if teacher is None:
            teacher = Teacher(telegram_user_id=telegram_user_id, name=name, is_admin=is_admin)
            self.session.add(teacher)
            await self.session.flush()
        else:
            if name and teacher.name != name:
                teacher.name = name
            if is_admin:
                teacher.is_admin = True
        return teacher

    async def ensure_owner_teacher(self, telegram_user_id: int) -> Teacher:
        return await self.ensure_teacher(telegram_user_id, "Administrator", is_admin=True)

    async def setup_chat(
        self,
        telegram_chat_id: int,
        owner_id: int,
        owner_name: str | None,
        telegram_chat_title: str | None = None,
        *,
        is_admin: bool = False,
    ) -> StudyChat:
        teacher = await self.ensure_teacher(owner_id, owner_name, is_admin=is_admin)
        study_chat = await self.by_telegram_id(telegram_chat_id, include_inactive=True)
        if study_chat is None:
            student = Student(teacher_id=teacher.id, name=telegram_chat_title or f"Ученик {abs(telegram_chat_id)}")
            self.session.add(student)
            await self.session.flush()
            study_chat = StudyChat(
                telegram_chat_id=telegram_chat_id,
                telegram_chat_title=telegram_chat_title,
                teacher_id=teacher.id,
                student_id=student.id,
                student=student,
                teacher=teacher,
            )
            self.session.add(study_chat)
            await self.session.flush()
        else:
            study_chat.teacher_id = teacher.id
            if study_chat.student and study_chat.student_id is not None:
                study_chat.student.teacher_id = teacher.id
            else:
                student = Student(
                    teacher_id=teacher.id,
                    name=telegram_chat_title or study_chat.telegram_chat_title or f"Ученик {abs(telegram_chat_id)}",
                )
                self.session.add(student)
                await self.session.flush()
                study_chat.student_id = student.id
                study_chat.student = student
                study_chat.student_name = None
                study_chat.student_telegram_id = None
            study_chat.active = True
            if telegram_chat_title:
                study_chat.telegram_chat_title = telegram_chat_title
        return study_chat

    async def assign_student(self, study_chat: StudyChat, student_id: int | None, student_name: str) -> bool:
        student_name = student_name.strip()
        if not student_name:
            raise ValueError("student_name_required")
        changed = study_chat.student_name != student_name
        if study_chat.student is None:
            student = Student(teacher_id=study_chat.teacher_id, name=student_name, telegram_user_id=student_id)
            self.session.add(student)
            await self.session.flush()
            study_chat.student_id = student.id
            study_chat.student = student
            changed = True
        else:
            changed = changed or study_chat.student.name != student_name
            study_chat.student.name = student_name
            if student_id is not None:
                changed = changed or study_chat.student.telegram_user_id != student_id
                study_chat.student.telegram_user_id = student_id
        study_chat.student_telegram_id = student_id
        study_chat.student_name = student_name
        await self.session.flush()
        return changed

    async def by_telegram_id(self, telegram_chat_id: int, *, include_inactive: bool = False) -> StudyChat | None:
        query = (
            select(StudyChat)
            .options(joinedload(StudyChat.student))
            .where(StudyChat.telegram_chat_id == telegram_chat_id)
        )
        if not include_inactive:
            query = query.where(StudyChat.active.is_(True))
        return await self.session.scalar(query)

    async def by_id(
        self, study_chat_id: int, *, owner_id: int | None = None, admin: bool = False, for_update: bool = False
    ) -> StudyChat | None:
        query = (
            select(StudyChat)
            .options(joinedload(StudyChat.student), joinedload(StudyChat.teacher))
            .where(StudyChat.id == study_chat_id)
        )
        if owner_id is not None and not admin:
            query = query.join(Teacher).where(Teacher.telegram_user_id == owner_id)
        if for_update:
            query = query.with_for_update(of=StudyChat).execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def list_students(self, owner_id: int, *, admin: bool = False) -> list[StudyChat]:
        query = (
            select(StudyChat)
            .options(joinedload(StudyChat.student), joinedload(StudyChat.teacher))
            .where(StudyChat.active.is_(True), StudyChat.student_name.is_not(None))
        )
        if not admin:
            query = query.join(Teacher).where(Teacher.telegram_user_id == owner_id)
        return list(await self.session.scalars(query.order_by(StudyChat.student_name)))

    async def list_groups(
        self, owner_id: int, *, admin: bool = False, include_inactive: bool = False
    ) -> list[StudyChat]:
        query = (
            select(StudyChat)
            .options(joinedload(StudyChat.student), joinedload(StudyChat.teacher))
            .where(StudyChat.active.is_(True) if not include_inactive else True)
        )
        if not admin:
            query = query.join(Teacher).where(Teacher.telegram_user_id == owner_id)
        return list(
            await self.session.scalars(
                query.order_by(StudyChat.student_name.asc().nulls_first(), StudyChat.telegram_chat_title)
            )
        )
