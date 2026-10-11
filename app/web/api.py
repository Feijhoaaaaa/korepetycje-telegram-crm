from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Annotated

from aiogram import Bot
from aiogram.types import BufferedInputFile, MenuButtonDefault
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from app.config import Settings
from app.database.models import (
    AuditLog,
    AvailabilityException,
    AvailabilityRule,
    Lesson,
    LessonSettlement,
    PaymentReceipt,
    ServiceRequest,
    Student,
    StudentLinkToken,
    StudyChat,
    Teacher,
)
from app.database.session import create_engine, create_session_factory
from app.services.accounts import AccountService, AvailabilityService, LinkService, quiet_now
from app.services.chats import ChatService
from app.services.export import build_account_workbook, build_full_workbook
from app.services.lessons import DomainError, LessonService
from app.services.notifications import notify_chat, notify_private
from app.utils.dates import local_to_utc, utc_to_local
from app.utils.resources import resource_path
from app.web.auth import Actor, require_actor
from app.web.schemas import (
    AssignStudentRequest,
    AvailabilityExceptionRequest,
    AvailabilityRequest,
    CancellationCreateRequest,
    CorrectPaymentRequest,
    CounterProposalRequest,
    CreateLessonRequest,
    DecisionRequest,
    LessonTimeRequest,
    PaymentRequest,
    QuietHoursRequest,
    RejectPaymentRequest,
    ReminderSnoozeRequest,
    RescheduleCreateRequest,
    ReviewPaymentRequest,
    TeacherRequest,
    TimeProposalRequest,
    TransferStudentRequest,
    UpdateLessonRequest,
)
from app.web.security import install_security_middleware

logger = logging.getLogger(__name__)


async def clear_mini_app_access(bot: Bot | None, telegram_ids: list[int | None]) -> None:
    if bot is None:
        return
    for telegram_id in {value for value in telegram_ids if value is not None}:
        try:
            await bot.set_chat_menu_button(chat_id=telegram_id, menu_button=MenuButtonDefault())
        except Exception as exc:
            logger.warning("Nie udało się wyczyścić menu Mini App dla %s: %s", telegram_id, exc)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_actor)])


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(database_session)]
ActorDep = Annotated[Actor, Depends(require_actor)]


def record_audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: int,
    *,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_telegram_id=actor.user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
        )
    )


def decimal_json(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def lesson_json(lesson: Lesson, settings: Settings) -> dict:
    local = utc_to_local(lesson.lesson_datetime, settings.tz)
    student_name = (
        lesson.student.name
        if lesson.student
        else lesson.student_name_snapshot or (lesson.study_chat.student_name if lesson.study_chat else None)
    )
    teacher_name = lesson.teacher.name if lesson.teacher else lesson.teacher_name_snapshot
    return {
        "id": lesson.id,
        "study_chat_id": lesson.study_chat_id,
        "student_id": lesson.student_id,
        "teacher_id": lesson.teacher_id,
        "student_name": student_name,
        "teacher_name": teacher_name,
        "telegram_chat_title": lesson.study_chat.telegram_chat_title if lesson.study_chat else None,
        "datetime": local.isoformat(),
        "date": local.date().isoformat(),
        "time": local.strftime("%H:%M"),
        "duration_minutes": lesson.duration_minutes,
        "status": lesson.status,
        "price": decimal_json(lesson.price),
        "price_missing": lesson.price is None,
        "payment_status": "paid" if lesson.settlement else lesson.payment_status,
    }


def chat_json(chat: StudyChat) -> dict:
    student = chat.student
    return {
        "id": chat.id,
        "student_id": chat.student_id,
        "teacher_id": chat.teacher_id,
        "teacher_name": chat.teacher.name if chat.teacher else None,
        "telegram_chat_id": chat.telegram_chat_id,
        "telegram_chat_title": chat.telegram_chat_title,
        "student_name": chat.student_name or (student.name if student else None),
        "student_telegram_id": student.telegram_user_id if student else chat.student_telegram_id,
        "assigned": bool(chat.student_id is not None and chat.student_name),
        "active": chat.active and bool(student.active if student else True),
    }


def teacher_json(row: Teacher) -> dict:
    return {
        "id": row.id,
        "telegram_user_id": row.telegram_user_id,
        "name": row.name,
        "active": row.active,
        "is_admin": row.is_admin,
        "default_price": decimal_json(row.default_price),
        "default_duration_minutes": row.default_duration_minutes,
        "quiet_hours_start": row.quiet_hours_start.isoformat(timespec="minutes") if row.quiet_hours_start else None,
        "quiet_hours_end": row.quiet_hours_end.isoformat(timespec="minutes") if row.quiet_hours_end else None,
    }


def receipt_json(row: PaymentReceipt) -> dict:
    return {
        "id": row.id,
        "student_id": row.student_id,
        "teacher_id": row.teacher_id,
        "student_name": row.student.name,
        "telegram_file_id": row.telegram_file_id,
        "file_type": row.telegram_file_type,
        "status": row.status,
        "submitted_amount": decimal_json(row.submitted_amount),
        "amount": decimal_json(row.amount),
        "currency": row.currency,
        "rejection_reason": row.rejection_reason,
        "created_at": row.created_at.isoformat(),
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }


def proposed_datetime(row: ServiceRequest) -> datetime:
    value = row.details.get("proposed_datetime")
    if not isinstance(value, str):
        raise HTTPException(status_code=500, detail="invalid_request_details")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="invalid_request_details") from exc


def service_request_json(row: ServiceRequest, settings: Settings) -> dict:
    proposed = proposed_datetime(row) if row.type in {"lesson_reschedule", "lesson_time_proposal"} else None
    lesson_time = utc_to_local(row.lesson.lesson_datetime, settings.tz) if row.lesson else None
    payment = row.payment
    return {
        "id": row.id,
        "type": row.type,
        "status": row.status,
        "student_id": row.student_id,
        "teacher_id": row.teacher_id,
        "student_name": row.student.name,
        "lesson_id": row.lesson_id,
        "payment_id": row.payment_id,
        "comment": row.comment,
        "decision_reason": row.details.get("decision_reason"),
        "phase": row.details.get("phase", "teacher_review"),
        "duration_minutes": row.details.get("duration_minutes"),
        "study_chat_id": row.details.get("study_chat_id"),
        "current_datetime": lesson_time.isoformat() if lesson_time else None,
        "proposed_datetime": utc_to_local(proposed, settings.tz).isoformat() if proposed else None,
        "submitted_amount": decimal_json(payment.submitted_amount) if payment else None,
        "amount": decimal_json(payment.amount) if payment else None,
        "created_at": row.created_at.isoformat(),
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "next_reminder_at": row.next_reminder_at.isoformat() if row.next_reminder_at else None,
        "last_reminder_at": row.last_reminder_at.isoformat() if row.last_reminder_at else None,
        "reminder_count": row.reminder_count,
    }


def local_range(start: date, end: date, settings: Settings) -> tuple[datetime, datetime]:
    if end <= start or end - start > timedelta(days=366):
        raise HTTPException(status_code=422, detail="invalid_date_range")
    return datetime.combine(start, time.min, settings.tz).astimezone(UTC), datetime.combine(
        end, time.min, settings.tz
    ).astimezone(UTC)


def requested_datetime(payload: LessonTimeRequest, settings: Settings) -> datetime:
    try:
        return local_to_utc(payload.date, payload.time.replace(tzinfo=None), settings.tz)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def domain_http_error(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def request_dedupe_key(
    request_type: str,
    student_id: int,
    proposed: datetime | None = None,
    *,
    lesson_id: int | None = None,
    duration_minutes: int | None = None,
    study_chat_id: int | None = None,
) -> str:
    values = [
        request_type,
        str(student_id),
        str(lesson_id or "new"),
        proposed.isoformat() if proposed else "",
        str(duration_minutes or ""),
        str(study_chat_id or ""),
    ]
    return ":".join(values)


async def notify_lesson_people(bot: Bot | None, lesson: Lesson, text: str, settings: Settings) -> list[str]:
    warnings: list[str] = []
    now = datetime.now(settings.tz).time()
    if lesson.teacher:
        warnings += await notify_private(
            bot,
            lesson.teacher.telegram_user_id,
            text,
            silent=quiet_now(lesson.teacher.quiet_hours_start, lesson.teacher.quiet_hours_end, now),
        )
    if lesson.student:
        warnings += await notify_private(
            bot,
            lesson.student.telegram_user_id,
            text,
            silent=quiet_now(lesson.student.quiet_hours_start, lesson.student.quiet_hours_end, now),
        )
    return warnings


async def actor_teacher(session: AsyncSession, actor: Actor, settings: Settings) -> Teacher | None:
    teacher_id = actor.teacher_id
    if teacher_id is not None:
        return await session.get(Teacher, teacher_id)
    if actor.is_admin:
        return await session.scalar(select(Teacher).where(Teacher.telegram_user_id == settings.owner_telegram_id))
    return None


async def require_teacher(session: AsyncSession, actor: Actor, settings: Settings) -> Teacher:
    teacher = await actor_teacher(session, actor, settings)
    if teacher is None or not teacher.active:
        raise HTTPException(status_code=403, detail="teacher_only")
    return teacher


def require_admin(actor: Actor) -> None:
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="admin_only")


async def scoped_chat(
    session: AsyncSession, actor: Actor, settings: Settings, chat_id: int, *, for_update: bool = False
) -> StudyChat:
    if actor.role == "student":
        query = (
            select(StudyChat)
            .options(joinedload(StudyChat.student), joinedload(StudyChat.teacher))
            .where(StudyChat.id == chat_id, StudyChat.student_id == actor.student_id)
        )
        chat = await session.scalar(query)
    else:
        teacher = await require_teacher(session, actor, settings)
        chat = await ChatService(session).by_id(
            chat_id, owner_id=teacher.telegram_user_id, admin=actor.is_admin, for_update=for_update
        )
    if chat is None or not chat.active:
        raise HTTPException(status_code=404, detail="student_not_found")
    return chat


async def scoped_lesson(
    session: AsyncSession, actor: Actor, settings: Settings, lesson_id: int, *, include_deleted: bool = False
) -> Lesson:
    teacher = await actor_teacher(session, actor, settings)
    lesson = await LessonService(session).get(
        lesson_id,
        include_deleted=include_deleted,
        teacher_id=teacher.id if teacher else None,
        student_id=actor.student_id,
        admin=actor.is_admin,
    )
    if lesson is None:
        raise HTTPException(status_code=404, detail="lesson_not_found")
    return lesson


def require_live_lesson(lesson: Lesson) -> None:
    if lesson.teacher_id is None or lesson.student_id is None:
        raise HTTPException(status_code=409, detail="historical_lesson_read_only")


async def conflict_or_availability(
    session: AsyncSession,
    lesson_service: LessonService,
    when: datetime,
    duration: int,
    teacher_id: int,
    student_id: int,
    settings: Settings,
    *,
    force: bool,
    exclude_lesson_id: int | None = None,
) -> None:
    conflicts = await lesson_service.conflicts(
        when,
        duration_minutes=duration,
        teacher_id=teacher_id,
        student_id=student_id,
        exclude_lesson_id=exclude_lesson_id,
    )
    local = utc_to_local(when, settings.tz)
    availability = AvailabilityService(session)
    configured = await session.scalar(
        select(func.count())
        .select_from(AvailabilityRule)
        .where((AvailabilityRule.teacher_id == teacher_id) | (AvailabilityRule.student_id == student_id))
    )
    outside = bool(configured) and not await availability.contains(local, duration, teacher_id, student_id)
    if not force and (conflicts or outside):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "lesson_conflict" if conflicts else "outside_availability",
                "conflicts": [lesson_json(item, settings) for item in conflicts],
            },
        )


@router.get("/bootstrap")
async def bootstrap(request: Request, actor: ActorDep) -> dict:
    settings: Settings = request.app.state.settings
    return {
        "user": {
            "id": actor.user.id,
            "name": " ".join(filter(None, (actor.user.first_name, actor.user.last_name))),
            "username": actor.user.username,
        },
        "role": actor.role,
        "capabilities": actor.capabilities,
        "timezone": settings.timezone,
        "status": "online",
        "version": "2.0.0",
    }


@router.get("/settings")
async def get_settings(request: Request, actor: ActorDep, session: SessionDep) -> dict:
    settings: Settings = request.app.state.settings
    owner = (
        await session.get(Student, actor.student_id)
        if actor.role == "student"
        else await actor_teacher(session, actor, settings)
    )
    return {
        "quiet_hours_start": owner.quiet_hours_start.isoformat(timespec="minutes")
        if owner and owner.quiet_hours_start
        else None,
        "quiet_hours_end": owner.quiet_hours_end.isoformat(timespec="minutes")
        if owner and owner.quiet_hours_end
        else None,
    }


@router.put("/settings/quiet-hours")
async def set_quiet_hours(payload: QuietHoursRequest, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    settings: Settings = request.app.state.settings
    owner = (
        await session.get(Student, actor.student_id)
        if actor.role == "student"
        else await actor_teacher(session, actor, settings)
    )
    if owner is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    owner.quiet_hours_start, owner.quiet_hours_end = payload.start, payload.end
    await session.commit()
    return await get_settings(request, actor, session)


@router.get("/admin/teachers")
async def teachers(actor: ActorDep, session: SessionDep) -> dict:
    require_admin(actor)
    return {"teachers": [teacher_json(row) for row in await session.scalars(select(Teacher).order_by(Teacher.name))]}


@router.post("/admin/teachers", status_code=201)
async def create_teacher(payload: TeacherRequest, actor: ActorDep, session: SessionDep) -> dict:
    require_admin(actor)
    if await session.scalar(select(Teacher.id).where(Teacher.telegram_user_id == payload.telegram_user_id)):
        raise HTTPException(status_code=409, detail="teacher_exists")
    row = Teacher(**payload.model_dump())
    session.add(row)
    await session.flush()
    record_audit(session, actor, "create", "teacher", row.id, after=teacher_json(row))
    await session.commit()
    return teacher_json(row)


@router.patch("/admin/teachers/{teacher_id}")
async def edit_teacher(teacher_id: int, payload: TeacherRequest, actor: ActorDep, session: SessionDep) -> dict:
    require_admin(actor)
    row = await session.get(Teacher, teacher_id)
    if row is None:
        raise HTTPException(status_code=404, detail="teacher_not_found")
    before = teacher_json(row)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    record_audit(session, actor, "update", "teacher", row.id, before=before, after=teacher_json(row))
    await session.commit()
    return teacher_json(row)


@router.post("/admin/teachers/{teacher_id}/archive")
async def archive_teacher(teacher_id: int, actor: ActorDep, session: SessionDep) -> dict:
    require_admin(actor)
    row = await session.get(Teacher, teacher_id)
    if row is None:
        raise HTTPException(status_code=404, detail="teacher_not_found")
    if row.telegram_user_id == actor.user.id:
        raise HTTPException(status_code=409, detail="cannot_archive_current_admin")
    before = teacher_json(row)
    row.active = False
    await session.execute(
        StudyChat.__table__.update().where(StudyChat.teacher_id == row.id).values(active=False)
    )
    await session.execute(Student.__table__.update().where(Student.teacher_id == row.id).values(active=False))
    record_audit(session, actor, "archive", "teacher", row.id, before=before, after=teacher_json(row))
    await session.commit()
    return teacher_json(row)


@router.post("/admin/teachers/{teacher_id}/restore")
async def restore_teacher(teacher_id: int, actor: ActorDep, session: SessionDep) -> dict:
    require_admin(actor)
    row = await session.get(Teacher, teacher_id)
    if row is None:
        raise HTTPException(status_code=404, detail="teacher_not_found")
    before = teacher_json(row)
    row.active = True
    record_audit(session, actor, "restore", "teacher", row.id, before=before, after=teacher_json(row))
    await session.commit()
    return teacher_json(row)


@router.patch("/admin/students/{student_id}/teacher")
async def transfer_student(
    student_id: int, payload: TransferStudentRequest, actor: ActorDep, session: SessionDep
) -> dict:
    require_admin(actor)
    student = await session.get(Student, student_id)
    teacher = await session.get(Teacher, payload.teacher_id)
    if student is None or teacher is None or not teacher.active:
        raise HTTPException(status_code=404, detail="student_or_teacher_not_found")
    previous_teacher_id = student.teacher_id
    student.teacher_id = teacher.id
    for chat in await session.scalars(select(StudyChat).where(StudyChat.student_id == student.id)):
        chat.teacher_id = teacher.id
    record_audit(
        session,
        actor,
        "transfer",
        "student",
        student.id,
        before={"teacher_id": previous_teacher_id},
        after={"teacher_id": teacher.id},
    )
    await session.commit()
    return {"student_id": student.id, "teacher_id": teacher.id}


@router.get("/calendar")
async def calendar(request: Request, actor: ActorDep, start: date, end: date, session: SessionDep) -> dict:
    settings: Settings = request.app.state.settings
    since, until = local_range(start, end, settings)
    teacher = await actor_teacher(session, actor, settings)
    lessons = await LessonService(session).list_scope(
        teacher_id=teacher.id if teacher else None,
        student_id=actor.student_id,
        admin=actor.is_admin,
        start=since,
        end=until,
        limit=500,
    )
    return {"start": start, "end": end, "lessons": [lesson_json(item, settings) for item in reversed(lessons)]}


@router.get("/students")
async def students(
    request: Request, actor: ActorDep, session: SessionDep, include_archived: bool = False
) -> dict:
    settings: Settings = request.app.state.settings
    if actor.role == "student":
        chats = list(
            await session.scalars(
                select(StudyChat)
                .options(joinedload(StudyChat.student), joinedload(StudyChat.teacher))
                .where(StudyChat.student_id == actor.student_id, StudyChat.active.is_(True))
            )
        )
    else:
        teacher = await require_teacher(session, actor, settings)
        chats = await ChatService(session).list_groups(
            teacher.telegram_user_id, admin=actor.is_admin, include_inactive=actor.is_admin and include_archived
        )
        # Hard-deleted accounts keep an inactive chat row for lesson history.
        # It is not an archived student and must not be offered for restore.
        chats = [chat for chat in chats if chat.student_id is not None or not chat.student_name or chat.active]
    pending = [row for row in chats if row.student_id is None or not row.student_name]
    active = [row for row in chats if row.student_id is not None and row.student_name]
    result = {"pending": [chat_json(row) for row in pending], "active": [chat_json(row) for row in active]}
    for item in result["active"]:
        item["balance"] = decimal_json(await AccountService(session).balance(item["student_id"]))
    return result


@router.post("/admin/students/{student_id}/archive")
async def archive_student(student_id: int, actor: ActorDep, session: SessionDep) -> dict:
    require_admin(actor)
    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="student_not_found")
    before = {"active": student.active, "teacher_id": student.teacher_id, "name": student.name}
    student.active = False
    await session.execute(
        StudyChat.__table__.update().where(StudyChat.student_id == student.id).values(active=False)
    )
    record_audit(session, actor, "archive", "student", student.id, before=before, after={**before, "active": False})
    await session.commit()
    return {"student_id": student.id, "active": False}


@router.post("/admin/students/{student_id}/restore")
async def restore_student(student_id: int, actor: ActorDep, session: SessionDep) -> dict:
    require_admin(actor)
    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="student_not_found")
    teacher = await session.get(Teacher, student.teacher_id)
    if not teacher or not teacher.active:
        raise HTTPException(status_code=409, detail="teacher_archived")
    before = {"active": student.active, "teacher_id": student.teacher_id, "name": student.name}
    student.active = True
    await session.execute(
        StudyChat.__table__.update().where(StudyChat.student_id == student.id).values(active=True)
    )
    record_audit(session, actor, "restore", "student", student.id, before=before, after={**before, "active": True})
    await session.commit()
    return {"student_id": student.id, "active": True}


@router.delete("/admin/students/{student_id}")
async def delete_student(student_id: int, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    require_admin(actor)
    student = await session.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="student_not_found")

    telegram_user_id = student.telegram_user_id
    deleted_snapshot = {"teacher_id": student.teacher_id, "name": student.name, "active": student.active}
    # Keep lessons and their Telegram group context, but remove account-owned data.
    lessons = list(
        await session.scalars(
            select(Lesson).options(joinedload(Lesson.study_chat)).where(Lesson.student_id == student.id)
        )
    )
    for lesson in lessons:
        lesson.student_name_snapshot = student.name
        if lesson.study_chat and not lesson.study_chat.student_name:
            lesson.study_chat.student_name = student.name
    await session.execute(delete(PaymentReceipt).where(PaymentReceipt.student_id == student.id))
    await session.execute(delete(LessonSettlement).where(LessonSettlement.student_id == student.id))
    await session.execute(
        StudyChat.__table__.update()
        .where(StudyChat.student_id == student.id)
        .values(student_telegram_id=None, active=False)
    )
    await session.delete(student)
    record_audit(session, actor, "delete", "student", student_id, before=deleted_snapshot)
    await session.commit()
    await clear_mini_app_access(request.app.state.bot, [telegram_user_id])
    return {"student_id": student_id, "deleted": True, "lessons_preserved": True}


@router.delete("/admin/teachers/{teacher_id}")
async def delete_teacher(teacher_id: int, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    require_admin(actor)
    teacher = await session.get(Teacher, teacher_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="teacher_not_found")
    if teacher.telegram_user_id == actor.user.id:
        raise HTTPException(status_code=409, detail="cannot_delete_current_admin")
    deleted_snapshot = teacher_json(teacher)

    students = list(await session.scalars(select(Student).where(Student.teacher_id == teacher.id)))
    telegram_ids = [teacher.telegram_user_id, *(student.telegram_user_id for student in students)]
    student_ids = [student.id for student in students]
    lessons = list(
        await session.scalars(
            select(Lesson)
            .options(joinedload(Lesson.student), joinedload(Lesson.study_chat))
            .where(Lesson.teacher_id == teacher.id)
        )
    )
    for lesson in lessons:
        lesson.teacher_name_snapshot = teacher.name or str(teacher.telegram_user_id)
        if lesson.student:
            lesson.student_name_snapshot = lesson.student.name
    if student_ids:
        await session.execute(delete(PaymentReceipt).where(PaymentReceipt.student_id.in_(student_ids)))
        await session.execute(delete(LessonSettlement).where(LessonSettlement.student_id.in_(student_ids)))
    await session.execute(delete(PaymentReceipt).where(PaymentReceipt.teacher_id == teacher.id))
    await session.execute(
        delete(LessonSettlement).where(LessonSettlement.confirmed_by_teacher_id == teacher.id)
    )
    await session.execute(delete(StudyChat).where(StudyChat.teacher_id == teacher.id))
    if student_ids:
        await session.execute(delete(Student).where(Student.id.in_(student_ids)))
    await session.delete(teacher)
    record_audit(session, actor, "delete", "teacher", teacher_id, before=deleted_snapshot)
    await session.commit()
    await clear_mini_app_access(request.app.state.bot, telegram_ids)
    return {"teacher_id": teacher_id, "deleted": True, "lessons_preserved": True}


@router.get("/students/{chat_id}")
async def student_detail(chat_id: int, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    settings: Settings = request.app.state.settings
    chat = await scoped_chat(session, actor, settings, chat_id)
    teacher = await actor_teacher(session, actor, settings)
    service = LessonService(session)
    now = datetime.now(UTC)
    kwargs = {
        "teacher_id": teacher.id if teacher else None,
        "student_id": actor.student_id,
        "admin": actor.is_admin,
        "study_chat_id": chat.id,
        "limit": 100,
    }
    upcoming = await service.list_scope(**kwargs, status="planned", start=now)
    history = await service.list_scope(**kwargs, end=now)
    account = AccountService(session)
    balances = await account.balances(chat.student_id)
    return {
        **chat_json(chat),
        "balance": decimal_json(balances.get("PLN", Decimal("0.00"))),
        "balances": {currency: decimal_json(amount) for currency, amount in balances.items()},
        "available_pool": decimal_json(await account.available_pool(chat.student_id)),
        "account_history": [
            {
                **row,
                "amount": decimal_json(row["amount"]),
                "balance": decimal_json(row["balance"]),
                "datetime": row["datetime"].isoformat(),
            }
            for row in await account.history(chat.student_id)
        ],
        "upcoming": [lesson_json(row, settings) for row in reversed(upcoming)],
        "history": [lesson_json(row, settings) for row in history],
    }


@router.patch("/students/{chat_id}")
async def assign_student(
    chat_id: int, payload: AssignStudentRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    chat = await scoped_chat(session, actor, settings, chat_id, for_update=True)
    changed = await ChatService(session).assign_student(chat, chat.student_telegram_id, payload.name)
    await session.commit()
    warnings = (
        await notify_chat(
            request.app.state.bot, chat.telegram_chat_id, f"✅ Группа назначена ученику: {chat.student_name}"
        )
        if changed
        else []
    )
    return {"student": chat_json(chat), "warnings": warnings}


@router.post("/students/{chat_id}/link")
async def create_student_link(chat_id: int, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    chat = await scoped_chat(session, actor, request.app.state.settings, chat_id)
    token = await LinkService(session).create(chat.student_id)
    await session.commit()
    bot: Bot | None = request.app.state.bot
    if bot is None:
        raise HTTPException(status_code=503, detail="telegram_not_configured")
    bot_user = await bot.get_me()
    return {"token": token, "url": f"https://t.me/{bot_user.username}?start=link_{token}", "expires_in_seconds": 1800}


@router.get("/student-links/pending")
async def pending_student_links(request: Request, actor: ActorDep, session: SessionDep) -> dict:
    teacher = await require_teacher(session, actor, request.app.state.settings)
    query = (
        select(StudentLinkToken, Student)
        .join(Student, StudentLinkToken.student_id == Student.id)
        .where(
            StudentLinkToken.claimed_at.is_not(None),
            StudentLinkToken.approved_at.is_(None),
            StudentLinkToken.expires_at > datetime.now(UTC),
        )
    )
    if not actor.is_admin:
        query = query.where(Student.teacher_id == teacher.id)
    rows = (await session.execute(query)).all()
    return {
        "links": [
            {
                "id": link.id,
                "student_id": student.id,
                "teacher_id": student.teacher_id,
                "student_name": student.name,
                "telegram_user_id": link.claimed_telegram_user_id,
                "expires_at": link.expires_at.isoformat(),
            }
            for link, student in rows
        ]
    }


@router.post("/student-links/{link_id}/approve")
async def approve_student_link(link_id: int, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    link = await session.get(StudentLinkToken, link_id)
    student = await session.get(Student, link.student_id) if link else None
    if link is None or student is None or (not actor.is_admin and student.teacher_id != teacher.id):
        raise HTTPException(status_code=404, detail="link_not_found")
    try:
        student = await LinkService(session).approve(link_id, teacher.id)
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    await session.commit()
    await notify_private(
        request.app.state.bot,
        student.telegram_user_id,
        "✅ Аккаунт ученика подключён.",
        silent=quiet_now(
            student.quiet_hours_start,
            student.quiet_hours_end,
            datetime.now(settings.tz).time(),
        ),
    )
    return {"student_id": student.id, "telegram_user_id": student.telegram_user_id}


@router.get("/lessons")
async def lessons(
    request: Request,
    actor: ActorDep,
    session: SessionDep,
    date_from: date | None = None,
    date_to: date | None = None,
    study_chat_id: int | None = None,
    lesson_status: str | None = Query(default=None, alias="status"),
    payment_status: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    settings: Settings = request.app.state.settings
    if lesson_status not in {None, "planned", "completed", "cancelled"} or payment_status not in {
        None,
        "unknown",
        "paid",
        "unpaid",
    }:
        raise HTTPException(status_code=422, detail="invalid_status")
    start = datetime.combine(date_from, time.min, settings.tz).astimezone(UTC) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, settings.tz).astimezone(UTC) if date_to else None
    teacher = await actor_teacher(session, actor, settings)
    rows = await LessonService(session).list_scope(
        teacher_id=teacher.id if teacher else None,
        student_id=actor.student_id,
        admin=actor.is_admin,
        start=start,
        end=end,
        study_chat_id=study_chat_id,
        status=lesson_status,
        payment_status=payment_status,
        offset=offset,
        limit=limit,
    )
    return {"lessons": [lesson_json(row, settings) for row in rows]}


@router.get("/lessons/{lesson_id}")
async def lesson_detail(lesson_id: int, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    return lesson_json(
        await scoped_lesson(session, actor, request.app.state.settings, lesson_id), request.app.state.settings
    )


@router.post("/lessons", status_code=201)
async def create_lesson(payload: CreateLessonRequest, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    chat = await scoped_chat(session, actor, settings, payload.study_chat_id)
    if not chat.student_name or chat.student_id is None or chat.student is None or not chat.student.active:
        raise HTTPException(status_code=409, detail="student_not_assigned")
    when = requested_datetime(payload, settings)
    chat_id, teacher_id, student_id, telegram_chat_id = (
        chat.id,
        chat.teacher_id,
        chat.student_id,
        chat.telegram_chat_id,
    )
    service = LessonService(session)
    await conflict_or_availability(
        session,
        service,
        when,
        payload.duration_minutes,
        teacher_id,
        student_id,
        settings,
        force=payload.force,
    )
    try:
        lesson = await service.create(chat_id, when, duration_minutes=payload.duration_minutes, price=payload.price)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing_id = await session.scalar(
            select(Lesson.id).where(
                Lesson.teacher_id == teacher_id,
                Lesson.student_id == student_id,
                Lesson.lesson_datetime == when,
                Lesson.status == "planned",
                Lesson.deleted_at.is_(None),
            )
        )
        existing = await service.get(
            existing_id,
            admin=True,
        )
        if existing is None:
            raise domain_http_error(DomainError("duplicate_lesson")) from exc
        lesson = existing
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    lesson = await service.get(lesson.id, admin=True)
    local = utc_to_local(when, settings.tz)
    warnings = await notify_chat(
        request.app.state.bot, telegram_chat_id, f"📅 Новое занятие\n\n{local:%d.%m.%Y, %H:%M}"
    )
    warnings += await notify_lesson_people(
        request.app.state.bot, lesson, f"📅 Новое занятие: {local:%d.%m.%Y, %H:%M}", settings
    )
    return {"lesson": lesson_json(lesson, settings), "warnings": warnings}


@router.patch("/lessons/{lesson_id}/reschedule")
async def reschedule_lesson(
    lesson_id: int, payload: LessonTimeRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    lesson = await scoped_lesson(session, actor, settings, lesson_id)
    require_live_lesson(lesson)
    when = requested_datetime(payload, settings)
    service = LessonService(session)
    await conflict_or_availability(
        session,
        service,
        when,
        payload.duration_minutes or lesson.duration_minutes,
        lesson.teacher_id,
        lesson.student_id,
        settings,
        force=payload.force,
        exclude_lesson_id=lesson.id,
    )
    old = utc_to_local(lesson.lesson_datetime, settings.tz)
    await service.reschedule(lesson, when)
    lesson.duration_minutes = payload.duration_minutes
    await session.commit()
    current = utc_to_local(when, settings.tz)
    warnings = []
    if lesson.study_chat:
        warnings = await notify_chat(
            request.app.state.bot,
            lesson.study_chat.telegram_chat_id,
            f"🔄 Занятие перенесено\n\nБыло: {old:%d.%m.%Y, %H:%M}\nСтало: {current:%d.%m.%Y, %H:%M}",
        )
    warnings += await notify_lesson_people(
        request.app.state.bot,
        lesson,
        f"🔄 Занятие перенесено: {old:%d.%m.%Y, %H:%M} → {current:%d.%m.%Y, %H:%M}",
        settings,
    )
    return {"lesson": lesson_json(lesson, settings), "warnings": warnings}


@router.patch("/lessons/{lesson_id}")
async def edit_lesson(
    lesson_id: int, payload: UpdateLessonRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    lesson = await scoped_lesson(session, actor, settings, lesson_id)
    require_live_lesson(lesson)
    ownership_change = any(
        value is not None for value in (payload.teacher_id, payload.student_id, payload.study_chat_id)
    )
    if ownership_change and not actor.is_admin:
        raise HTTPException(status_code=403, detail="admin_only")

    target_teacher, target_student = lesson.teacher_id, lesson.student_id
    if ownership_change:
        target_chat_id = payload.study_chat_id or lesson.study_chat_id
        target_chat = await session.scalar(
            select(StudyChat)
            .options(joinedload(StudyChat.teacher), joinedload(StudyChat.student))
            .where(StudyChat.id == target_chat_id, StudyChat.active.is_(True))
        )
        target_teacher = payload.teacher_id or lesson.teacher_id
        target_student = payload.student_id or lesson.student_id
        if (
            target_chat is None
            or target_chat.teacher is None
            or not target_chat.teacher.active
            or target_chat.student is None
            or not target_chat.student.active
            or target_chat.teacher_id != target_teacher
            or target_chat.student_id != target_student
        ):
            raise HTTPException(status_code=409, detail="invalid_lesson_ownership")
    service = LessonService(session)
    when = (
        local_to_utc(payload.date, payload.time.replace(tzinfo=None), settings.tz)
        if payload.date and payload.time
        else None
    )
    if when or payload.duration_minutes or payload.teacher_id or payload.student_id:
        await conflict_or_availability(
            session,
            service,
            when or lesson.lesson_datetime,
            payload.duration_minutes or lesson.duration_minutes,
            target_teacher,
            target_student,
            settings,
            force=payload.force,
            exclude_lesson_id=lesson.id,
        )
    try:
        await service.update(
            lesson,
            actor_telegram_id=actor.user.id,
            lesson_datetime=when,
            duration_minutes=payload.duration_minutes,
            price=payload.price if payload.price_set else ...,
            teacher_id=payload.teacher_id,
            student_id=payload.student_id,
            study_chat_id=payload.study_chat_id,
        )
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    await session.commit()
    lesson = await service.get(lesson.id, admin=True)
    return {"lesson": lesson_json(lesson, settings), "warnings": []}


async def mutate_status(action: str, lesson_id: int, request: Request, actor: Actor, session: AsyncSession) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    lesson = await scoped_lesson(session, actor, settings, lesson_id, include_deleted=action == "delete")
    require_live_lesson(lesson)
    service = LessonService(session)
    try:
        if action == "complete":
            await service.complete(lesson)
        elif action == "cancel":
            await service.cancel(lesson)
        else:
            await service.soft_delete(lesson, actor.user.id)
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    await session.commit()
    warnings: list[str] = []
    if action in {"cancel", "delete"}:
        local = utc_to_local(lesson.lesson_datetime, settings.tz)
        icon, word = ("❌", "отменено") if action == "cancel" else ("🗑", "удалено")
        if lesson.study_chat:
            warnings = await notify_chat(
                request.app.state.bot,
                lesson.study_chat.telegram_chat_id,
                f"{icon} Занятие {word}\n\n{local:%d.%m.%Y, %H:%M}",
            )
    warnings += await notify_lesson_people(
        request.app.state.bot,
        lesson,
        f"Занятие {utc_to_local(lesson.lesson_datetime, settings.tz):%d.%m.%Y, %H:%M}: {lesson.status}",
        settings,
    )
    return {"lesson": lesson_json(lesson, settings), "warnings": warnings}


@router.post("/lessons/{lesson_id}/complete")
async def complete_lesson(lesson_id: int, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    return await mutate_status("complete", lesson_id, request, actor, session)


@router.post("/lessons/{lesson_id}/cancel")
async def cancel_lesson(lesson_id: int, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    return await mutate_status("cancel", lesson_id, request, actor, session)


@router.delete("/lessons/{lesson_id}")
async def delete_lesson(lesson_id: int, request: Request, actor: ActorDep, session: SessionDep) -> dict:
    return await mutate_status("delete", lesson_id, request, actor, session)


@router.patch("/lessons/{lesson_id}/payment")
async def set_payment(
    lesson_id: int, payload: PaymentRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    lesson = await scoped_lesson(session, actor, settings, lesson_id)
    teacher = await require_teacher(session, actor, settings)
    try:
        if payload.payment_status == "paid":
            await AccountService(session).settle_lesson(lesson, teacher.id, actor.user.id)
        elif lesson.settlement:
            await AccountService(session).un_settle_lesson(lesson, actor.user.id)
        else:
            await LessonService(session).set_payment(lesson, "unpaid")
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    await session.commit()
    await notify_lesson_people(
        request.app.state.bot, lesson, f"Статус оплаты занятия: {lesson.payment_status}", settings
    )
    return {"lesson": lesson_json(lesson, settings), "warnings": []}


@router.get("/payments/unpaid")
async def unpaid(request: Request, actor: ActorDep, session: SessionDep) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await actor_teacher(session, actor, settings)
    rows = await LessonService(session).list_scope(
        teacher_id=teacher.id if teacher else None,
        student_id=actor.student_id,
        admin=actor.is_admin,
        status="completed",
        payment_status="unpaid",
        limit=1000,
    )
    grouped: dict[int, dict] = {}
    for lesson in rows:
        group = grouped.setdefault(
            lesson.student_id,
            {
                "student_id": lesson.student_id,
                "study_chat_id": lesson.study_chat_id,
                "student_name": lesson.student.name,
                "lessons": [],
            },
        )
        group["lessons"].append(lesson_json(lesson, settings))
    return {"students": list(grouped.values()), "total": len(rows)}


@router.get("/payments/receipts")
async def payment_receipts(
    request: Request,
    actor: ActorDep,
    session: SessionDep,
    receipt_status: str | None = Query(default=None, alias="status"),
) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await actor_teacher(session, actor, settings)
    rows = await AccountService(session).list_receipts(
        teacher_id=teacher.id if teacher else None,
        student_id=actor.student_id,
        admin=actor.is_admin,
        status=receipt_status,
    )
    return {"receipts": [receipt_json(row) for row in rows]}


@router.get("/payments/receipts/{receipt_id}/file")
async def payment_file(receipt_id: int, request: Request, actor: ActorDep, session: SessionDep) -> Response:
    settings: Settings = request.app.state.settings
    teacher = await actor_teacher(session, actor, settings)
    row = await AccountService(session).receipt(
        receipt_id, teacher_id=teacher.id if teacher else None, student_id=actor.student_id, admin=actor.is_admin
    )
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    bot: Bot | None = request.app.state.bot
    if bot is None:
        raise HTTPException(status_code=503, detail="telegram_not_configured")
    telegram_file = await bot.get_file(row.telegram_file_id)
    max_file_bytes = 20 * 1024 * 1024
    if telegram_file.file_size and telegram_file.file_size > max_file_bytes:
        raise HTTPException(status_code=413, detail="payment_file_too_large")
    stream = await bot.download_file(telegram_file.file_path)
    if stream is None:
        raise HTTPException(status_code=502, detail="telegram_download_failed")
    content = stream.read(max_file_bytes + 1)
    if len(content) > max_file_bytes:
        raise HTTPException(status_code=413, detail="payment_file_too_large")
    extension = "pdf" if row.telegram_file_type == "document" else "jpg"
    return Response(
        content,
        media_type="application/pdf" if row.telegram_file_type == "document" else "image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="payment-{row.id}.{extension}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/payments/receipts/{receipt_id}/approve")
async def approve_payment(
    receipt_id: int, payload: ReviewPaymentRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    row = await AccountService(session).receipt(receipt_id, teacher_id=teacher.id, admin=actor.is_admin)
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    try:
        changed = await AccountService(session).approve(
            row, payload.amount, payload.lesson_ids, teacher.id, actor.user.id, payload.currency
        )
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    await session.commit()
    if changed:
        await notify_private(
            request.app.state.bot,
            row.student.telegram_user_id,
            f"✅ Платёж на {row.amount:.2f} {row.currency} подтверждён.",
            silent=quiet_now(
                row.student.quiet_hours_start,
                row.student.quiet_hours_end,
                datetime.now(settings.tz).time(),
            ),
        )
    return receipt_json(row)


@router.post("/payments/receipts/{receipt_id}/reject")
async def reject_payment(
    receipt_id: int, payload: RejectPaymentRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    row = await AccountService(session).receipt(receipt_id, teacher_id=teacher.id, admin=actor.is_admin)
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    try:
        changed = await AccountService(session).reject(row, payload.reason, teacher.id, actor.user.id)
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    await session.commit()
    if changed:
        await notify_private(
            request.app.state.bot,
            row.student.telegram_user_id,
            f"❌ Подтверждение платежа отклонено: {row.rejection_reason}",
            silent=quiet_now(
                row.student.quiet_hours_start,
                row.student.quiet_hours_end,
                datetime.now(settings.tz).time(),
            ),
        )
    return receipt_json(row)


@router.patch("/admin/payments/{receipt_id}")
async def correct_payment(
    receipt_id: int,
    payload: CorrectPaymentRequest,
    request: Request,
    actor: ActorDep,
    session: SessionDep,
) -> dict:
    require_admin(actor)
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    row = await AccountService(session).receipt(receipt_id, admin=True)
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    currency = payload.currency if payload.currency != "PLN" or row.currency == "PLN" else row.currency
    try:
        await AccountService(session).correct_receipt(
            row,
            status=payload.status,
            amount=payload.amount,
            currency=currency,
            reason=payload.reason,
            reviewer_id=teacher.id,
            actor_telegram_id=actor.user.id,
        )
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    await session.commit()
    return receipt_json(row)


def owner_ids(actor: Actor, teacher: Teacher | None) -> tuple[int | None, int | None]:
    if actor.role == "student":
        if actor.student_id is None:
            raise HTTPException(status_code=404, detail="account_not_found")
        return None, actor.student_id
    if teacher is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    return teacher.id, None


@router.get("/availability")
async def availability(request: Request, actor: ActorDep, session: SessionDep) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await actor_teacher(session, actor, settings)
    teacher_id, student_id = owner_ids(actor, teacher)
    rule_owner = AvailabilityRule.student_id == student_id if student_id else AvailabilityRule.teacher_id == teacher_id
    exception_owner = (
        AvailabilityException.student_id == student_id if student_id else AvailabilityException.teacher_id == teacher_id
    )
    rules = list(
        await session.scalars(
            select(AvailabilityRule).where(rule_owner).order_by(AvailabilityRule.weekday, AvailabilityRule.start_time)
        )
    )
    exceptions = list(
        await session.scalars(
            select(AvailabilityException).where(exception_owner).order_by(AvailabilityException.exception_date)
        )
    )
    return {
        "rules": [
            {
                "id": row.id,
                "weekday": row.weekday,
                "start_time": row.start_time.isoformat(timespec="minutes"),
                "end_time": row.end_time.isoformat(timespec="minutes"),
            }
            for row in rules
        ],
        "exceptions": [
            {
                "id": row.id,
                "date": row.exception_date.isoformat(),
                "available": row.available,
                "start_time": row.start_time.isoformat(timespec="minutes") if row.start_time else None,
                "end_time": row.end_time.isoformat(timespec="minutes") if row.end_time else None,
            }
            for row in exceptions
        ],
    }


@router.put("/availability")
async def replace_availability(
    payload: AvailabilityRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await actor_teacher(session, actor, settings)
    teacher_id, student_id = owner_ids(actor, teacher)
    await AvailabilityService(session).replace_rules(
        teacher_id=teacher_id, student_id=student_id, rules=[row.model_dump() for row in payload.rules]
    )
    await session.commit()
    return await availability(request, actor, session)


@router.post("/availability/exceptions")
async def set_availability_exception(
    payload: AvailabilityExceptionRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await actor_teacher(session, actor, settings)
    teacher_id, student_id = owner_ids(actor, teacher)
    row = await AvailabilityService(session).set_exception(
        teacher_id=teacher_id,
        student_id=student_id,
        exception_date=payload.date,
        available=payload.available,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
    await session.commit()
    return {"id": row.id}


@router.delete("/availability/exceptions/{exception_date}")
async def delete_availability_exception(
    exception_date: date, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await actor_teacher(session, actor, settings)
    teacher_id, student_id = owner_ids(actor, teacher)
    owner = (
        AvailabilityException.student_id == student_id
        if student_id
        else AvailabilityException.teacher_id == teacher_id
    )
    await session.execute(
        AvailabilityException.__table__.delete().where(
            owner, AvailabilityException.exception_date == exception_date
        )
    )
    await session.commit()
    return {"date": exception_date}


@router.get("/availability/common")
async def common_availability(
    day: date, student_id: int, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    settings: Settings = request.app.state.settings
    student = await session.get(Student, student_id)
    if (
        student is None
        or (
            not actor.is_admin
            and actor.role == "teacher"
            and student.teacher_id != (await require_teacher(session, actor, settings)).id
        )
        or (actor.role == "student" and student.id != actor.student_id)
    ):
        raise HTTPException(status_code=404, detail="student_not_found")
    intervals = await AvailabilityService(session).common(day, student.teacher_id, student.id)
    return {
        "date": day,
        "intervals": [
            {"start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes")}
            for start, end in intervals
        ],
    }


@router.get("/request-slots")
async def request_slots(
    day: date,
    request: Request,
    actor: ActorDep,
    session: SessionDep,
    lesson_id: int | None = None,
    study_chat_id: int | None = None,
    duration_minutes: int = Query(default=60, ge=15, le=480),
) -> dict:
    if actor.role != "student" or actor.student_id is None:
        raise HTTPException(status_code=403, detail="student_only")
    settings: Settings = request.app.state.settings
    lesson = await scoped_lesson(session, actor, settings, lesson_id) if lesson_id else None
    if lesson is not None:
        require_live_lesson(lesson)
        teacher_id, student_id = lesson.teacher_id, lesson.student_id
        exclude_lesson_id = lesson.id
    else:
        chats = list(
            await session.scalars(
                select(StudyChat).where(
                    StudyChat.student_id == actor.student_id,
                    StudyChat.active.is_(True),
                    StudyChat.teacher_id.is_not(None),
                )
            )
        )
        if study_chat_id is not None:
            chat = await scoped_chat(session, actor, settings, study_chat_id)
        elif len(chats) == 1:
            chat = chats[0]
        elif not chats:
            raise HTTPException(status_code=404, detail="student_chat_not_found")
        else:
            raise HTTPException(status_code=422, detail="study_chat_required")
        teacher_id, student_id, exclude_lesson_id = chat.teacher_id, actor.student_id, None
    if teacher_id is None:
        raise HTTPException(status_code=409, detail="teacher_not_assigned")
    intervals = await AvailabilityService(session).common(day, teacher_id, student_id)
    service = LessonService(session)
    slots: list[dict] = []
    for start, end in intervals:
        cursor = datetime.combine(day, start)
        boundary = datetime.combine(day, end)
        while cursor + timedelta(minutes=duration_minutes) <= boundary:
            try:
                when = local_to_utc(day, cursor.time(), settings.tz)
            except ValueError:
                cursor += timedelta(minutes=15)
                continue
            if not await service.conflicts(
                when,
                duration_minutes=duration_minutes,
                teacher_id=teacher_id,
                student_id=student_id,
                exclude_lesson_id=exclude_lesson_id,
            ):
                local = utc_to_local(when, settings.tz)
                slots.append(
                    {
                        "datetime": local.isoformat(),
                        "date": day.isoformat(),
                        "time": local.time().isoformat(timespec="minutes"),
                    }
                )
            cursor += timedelta(minutes=15)
    return {"slots": slots}


@router.get("/requests")
async def service_requests(
    request: Request,
    actor: ActorDep,
    session: SessionDep,
    request_type: str | None = Query(default=None, alias="type"),
    request_status: str | None = Query(default=None, alias="status"),
) -> dict:
    allowed_types = {
        "payment_confirmation",
        "lesson_reschedule",
        "lesson_cancellation",
        "lesson_time_proposal",
        "other",
    }
    allowed_statuses = {"pending", "approved", "rejected", "cancelled", "expired"}
    if request_type is not None and request_type not in allowed_types:
        raise HTTPException(status_code=422, detail="invalid_request_type")
    if request_status is not None and request_status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="invalid_request_status")

    settings: Settings = request.app.state.settings
    teacher = await actor_teacher(session, actor, settings)
    query = select(ServiceRequest).options(
        joinedload(ServiceRequest.student),
        joinedload(ServiceRequest.lesson),
        joinedload(ServiceRequest.payment),
    )
    if actor.role == "student":
        query = query.where(ServiceRequest.student_id == actor.student_id)
    elif not actor.is_admin:
        if teacher is None:
            raise HTTPException(status_code=404, detail="account_not_found")
        query = query.where(ServiceRequest.teacher_id == teacher.id)
    if request_type:
        query = query.where(ServiceRequest.type == request_type)
    if request_status:
        query = query.where(ServiceRequest.status == request_status)
    rows = list(await session.scalars(query.order_by(ServiceRequest.created_at.desc())))
    return {"requests": [service_request_json(row, settings) for row in rows]}


@router.post("/lessons/{lesson_id}/reschedule-requests", status_code=201)
async def request_reschedule(
    lesson_id: int, payload: RescheduleCreateRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role != "student":
        raise HTTPException(status_code=403, detail="student_only")
    settings: Settings = request.app.state.settings
    lesson = await scoped_lesson(session, actor, settings, lesson_id)
    existing_id = await session.scalar(
        select(ServiceRequest.id).where(
            ServiceRequest.lesson_id == lesson.id,
            ServiceRequest.type == "lesson_reschedule",
            ServiceRequest.status == "pending",
        )
    )
    if existing_id:
        return {"id": existing_id, "status": "pending"}
    row = ServiceRequest(
        type="lesson_reschedule",
        lesson_id=lesson.id,
        student_id=actor.student_id,
        teacher_id=lesson.teacher_id,
        dedupe_key=request_dedupe_key(
            "lesson_reschedule", actor.student_id, requested_datetime(payload, settings), lesson_id=lesson.id
        ),
        details={
            "phase": "teacher_review",
            "proposed_datetime": requested_datetime(payload, settings).isoformat(),
            "duration_minutes": payload.duration_minutes,
        },
        comment=payload.reason,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing_id = await session.scalar(
            select(ServiceRequest.id).where(
                ServiceRequest.lesson_id == lesson.id,
                ServiceRequest.type == "lesson_reschedule",
                ServiceRequest.status == "pending",
            )
        )
        if existing_id:
            return {"id": existing_id, "status": "pending"}
        raise domain_http_error(DomainError("request_already_pending")) from exc
    return {"id": row.id, "status": row.status}


@router.post("/lessons/{lesson_id}/cancellation-requests", status_code=201)
async def request_cancellation(
    lesson_id: int,
    payload: CancellationCreateRequest,
    request: Request,
    actor: ActorDep,
    session: SessionDep,
) -> dict:
    if actor.role != "student":
        raise HTTPException(status_code=403, detail="student_only")
    lesson = await scoped_lesson(session, actor, request.app.state.settings, lesson_id)
    require_live_lesson(lesson)
    if lesson.status != "planned":
        raise HTTPException(status_code=409, detail="lesson_not_cancellable")
    existing_id = await session.scalar(
        select(ServiceRequest.id).where(
            ServiceRequest.type == "lesson_cancellation",
            ServiceRequest.lesson_id == lesson.id,
            ServiceRequest.status == "pending",
        )
    )
    if existing_id:
        return {"id": existing_id, "status": "pending"}
    row = ServiceRequest(
        type="lesson_cancellation",
        lesson_id=lesson.id,
        student_id=actor.student_id,
        teacher_id=lesson.teacher_id,
        dedupe_key=request_dedupe_key("lesson_cancellation", actor.student_id, lesson_id=lesson.id),
        details={"phase": "teacher_review"},
        comment=payload.reason,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing_id = await session.scalar(
            select(ServiceRequest.id).where(
                ServiceRequest.type == "lesson_cancellation",
                ServiceRequest.lesson_id == lesson.id,
                ServiceRequest.status == "pending",
            )
        )
        if existing_id:
            return {"id": existing_id, "status": "pending"}
        raise domain_http_error(DomainError("request_already_pending")) from exc
    return {"id": row.id, "status": row.status}


@router.post("/lessons/{lesson_id}/time-proposals", status_code=201)
async def request_lesson_time(
    lesson_id: int,
    payload: TimeProposalRequest,
    request: Request,
    actor: ActorDep,
    session: SessionDep,
) -> dict:
    if actor.role != "student":
        raise HTTPException(status_code=403, detail="student_only")
    settings: Settings = request.app.state.settings
    lesson = await scoped_lesson(session, actor, settings, lesson_id)
    require_live_lesson(lesson)
    proposed = requested_datetime(payload, settings)
    row = ServiceRequest(
        type="lesson_time_proposal",
        lesson_id=lesson.id,
        student_id=actor.student_id,
        teacher_id=lesson.teacher_id,
        dedupe_key=request_dedupe_key(
            "lesson_time_proposal",
            actor.student_id,
            proposed,
            lesson_id=lesson.id,
            duration_minutes=payload.duration_minutes,
        ),
        details={
            "phase": "teacher_review",
            "proposed_datetime": proposed.isoformat(),
            "duration_minutes": payload.duration_minutes,
        },
        comment=payload.reason,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing_id = await session.scalar(
            select(ServiceRequest.id).where(ServiceRequest.dedupe_key == row.dedupe_key)
        )
        if existing_id:
            return {"id": existing_id, "status": "pending"}
        raise domain_http_error(DomainError("request_already_pending")) from exc
    return {"id": row.id, "status": row.status}


@router.post("/time-proposals", status_code=201)
async def request_new_lesson_time(
    payload: TimeProposalRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role != "student":
        raise HTTPException(status_code=403, detail="student_only")
    settings: Settings = request.app.state.settings
    chats = list(
        await session.scalars(
            select(StudyChat).where(
                StudyChat.student_id == actor.student_id,
                StudyChat.active.is_(True),
                StudyChat.teacher_id.is_not(None),
            )
        )
    )
    if payload.study_chat_id is not None:
        chat = await scoped_chat(session, actor, settings, payload.study_chat_id)
    elif len(chats) == 1:
        chat = chats[0]
    elif not chats:
        raise HTTPException(status_code=404, detail="student_chat_not_found")
    else:
        raise HTTPException(status_code=422, detail="study_chat_required")
    proposed = requested_datetime(payload, settings)
    dedupe_key = request_dedupe_key(
        "lesson_time_proposal",
        actor.student_id,
        proposed,
        duration_minutes=payload.duration_minutes,
        study_chat_id=chat.id,
    )
    existing_id = await session.scalar(
        select(ServiceRequest.id).where(ServiceRequest.dedupe_key == dedupe_key, ServiceRequest.status == "pending")
    )
    if existing_id:
        return {"id": existing_id, "status": "pending"}
    row = ServiceRequest(
        type="lesson_time_proposal",
        student_id=actor.student_id,
        teacher_id=chat.teacher_id,
        dedupe_key=dedupe_key,
        details={
            "phase": "teacher_review",
            "proposed_datetime": proposed.isoformat(),
            "duration_minutes": payload.duration_minutes,
            "study_chat_id": chat.id,
        },
        comment=payload.reason,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing_id = await session.scalar(
            select(ServiceRequest.id).where(ServiceRequest.dedupe_key == dedupe_key)
        )
        if existing_id:
            return {"id": existing_id, "status": "pending"}
        raise domain_http_error(DomainError("request_already_pending")) from exc
    return {"id": row.id, "status": row.status}


async def lock_service_request(session: AsyncSession, request_id: int) -> ServiceRequest | None:
    return await session.scalar(
        select(ServiceRequest)
        .options(
            joinedload(ServiceRequest.student),
            joinedload(ServiceRequest.lesson)
            .joinedload(Lesson.study_chat)
            .joinedload(StudyChat.teacher),
            joinedload(ServiceRequest.lesson).joinedload(Lesson.teacher),
            joinedload(ServiceRequest.lesson).joinedload(Lesson.student),
        )
        .where(ServiceRequest.id == request_id)
        .execution_options(populate_existing=True)
        .with_for_update(of=ServiceRequest)
    )


async def complete_service_request(
    row: ServiceRequest,
    *,
    reviewer_id: int,
    session: AsyncSession,
    settings: Settings,
    force: bool,
) -> Lesson:
    if row.type == "lesson_cancellation":
        if row.lesson is None:
            raise HTTPException(status_code=409, detail="lesson_not_found")
        require_live_lesson(row.lesson)
        await LessonService(session).cancel(row.lesson)
        row.status = "approved"
        row.resolved_by_teacher_id = reviewer_id
        row.resolved_at = datetime.now(UTC)
        row.details = {**row.details, "phase": "resolved"}
        return row.lesson

    if row.type not in {"lesson_reschedule", "lesson_time_proposal"}:
        raise HTTPException(status_code=409, detail="request_not_approvable")
    when = proposed_datetime(row)
    duration = int(row.details.get("duration_minutes") or (row.lesson.duration_minutes if row.lesson else 60))
    service = LessonService(session)
    if row.lesson is not None:
        require_live_lesson(row.lesson)
        await conflict_or_availability(
            session,
            service,
            when,
            duration,
            row.lesson.teacher_id,
            row.lesson.student_id,
            settings,
            force=force,
            exclude_lesson_id=row.lesson.id,
        )
        await service.reschedule(row.lesson, when)
        row.lesson.duration_minutes = duration
        lesson = row.lesson
    else:
        study_chat_id = row.details.get("study_chat_id")
        chat = await session.scalar(
            select(StudyChat)
            .options(joinedload(StudyChat.teacher), joinedload(StudyChat.student))
            .where(
                StudyChat.id == study_chat_id,
                StudyChat.student_id == row.student_id,
                StudyChat.teacher_id == row.teacher_id,
                StudyChat.active.is_(True),
            )
            .with_for_update(of=StudyChat)
        )
        if chat is None:
            raise HTTPException(status_code=404, detail="student_chat_not_found")
        await conflict_or_availability(
            session,
            service,
            when,
            duration,
            row.teacher_id,
            row.student_id,
            settings,
            force=force,
        )
        lesson = await service.create(chat.id, when, duration_minutes=duration)
        row.lesson_id = lesson.id
    row.status = "approved"
    row.resolved_by_teacher_id = reviewer_id
    row.resolved_at = datetime.now(UTC)
    row.details = {**row.details, "phase": "resolved"}
    await session.flush()
    return lesson


@router.post("/requests/{request_id}/approve")
async def approve_service_request(
    request_id: int, payload: DecisionRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    row = await lock_service_request(session, request_id)
    if row is None or (not actor.is_admin and row.teacher_id != teacher.id):
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.status == "approved":
        return service_request_json(row, settings)
    if row.status != "pending" or row.details.get("phase", "teacher_review") != "teacher_review":
        raise HTTPException(status_code=409, detail="request_waiting_for_student")
    try:
        lesson = await complete_service_request(
            row, reviewer_id=teacher.id, session=session, settings=settings, force=payload.force
        )
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    await session.commit()
    lesson = await LessonService(session).get(lesson.id, admin=True)
    if lesson is not None:
        await notify_lesson_people(request.app.state.bot, lesson, "✅ Заявка подтверждена.", settings)
    return service_request_json(row, settings)


@router.post("/requests/{request_id}/counter")
async def counter_service_request(
    request_id: int,
    payload: CounterProposalRequest,
    request: Request,
    actor: ActorDep,
    session: SessionDep,
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    row = await lock_service_request(session, request_id)
    if row is None or (not actor.is_admin and row.teacher_id != teacher.id):
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.type not in {"lesson_reschedule", "lesson_time_proposal"} or row.status != "pending":
        raise HTTPException(status_code=409, detail="request_not_counterable")
    if row.details.get("phase", "teacher_review") != "teacher_review":
        raise HTTPException(status_code=409, detail="request_waiting_for_student")
    proposed = requested_datetime(payload, settings)
    duration = payload.duration_minutes or int(
        row.details.get("duration_minutes") or (row.lesson.duration_minutes if row.lesson else 60)
    )
    if row.lesson is not None:
        require_live_lesson(row.lesson)
        await conflict_or_availability(
            session,
            LessonService(session),
            proposed,
            duration,
            row.lesson.teacher_id,
            row.lesson.student_id,
            settings,
            force=payload.force,
            exclude_lesson_id=row.lesson.id,
        )
    else:
        study_chat_id = row.details.get("study_chat_id")
        if await session.scalar(
            select(StudyChat.id).where(
                StudyChat.id == study_chat_id,
                StudyChat.student_id == row.student_id,
                StudyChat.teacher_id == row.teacher_id,
                StudyChat.active.is_(True),
            )
        ) is None:
            raise HTTPException(status_code=404, detail="student_chat_not_found")
        await conflict_or_availability(
            session,
            LessonService(session),
            proposed,
            duration,
            row.teacher_id,
            row.student_id,
            settings,
            force=payload.force,
        )
    row.details = {
        **row.details,
        "phase": "student_confirmation",
        "proposed_datetime": proposed.isoformat(),
        "duration_minutes": duration,
        "counter_comment": payload.reason,
    }
    await session.commit()
    if row.student.telegram_user_id:
        await notify_private(
            request.app.state.bot,
            row.student.telegram_user_id,
            "🔄 Преподаватель предложил другое время. Подтвердите его в приложении.",
            silent=quiet_now(
                row.student.quiet_hours_start,
                row.student.quiet_hours_end,
                datetime.now(settings.tz).time(),
            ),
        )
    return service_request_json(row, settings)


@router.post("/requests/{request_id}/confirm")
async def confirm_service_request(
    request_id: int, payload: DecisionRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role != "student":
        raise HTTPException(status_code=403, detail="student_only")
    settings: Settings = request.app.state.settings
    row = await lock_service_request(session, request_id)
    if row is None or row.student_id != actor.student_id:
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.status == "approved":
        return service_request_json(row, settings)
    if row.status != "pending" or row.details.get("phase") != "student_confirmation":
        raise HTTPException(status_code=409, detail="request_not_ready_for_student")
    teacher = await session.get(Teacher, row.teacher_id)
    if teacher is None:
        raise HTTPException(status_code=404, detail="teacher_not_found")
    try:
        lesson = await complete_service_request(
            row, reviewer_id=teacher.id, session=session, settings=settings, force=payload.force
        )
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    await session.commit()
    lesson = await LessonService(session).get(lesson.id, admin=True)
    if lesson is not None:
        await notify_lesson_people(request.app.state.bot, lesson, "✅ Время занятия подтверждено.", settings)
    return service_request_json(row, settings)


@router.post("/requests/{request_id}/cancel")
async def cancel_service_request(request_id: int, actor: ActorDep, session: SessionDep) -> dict:
    if actor.role != "student":
        raise HTTPException(status_code=403, detail="student_only")
    row = await session.scalar(
        select(ServiceRequest)
        .where(ServiceRequest.id == request_id, ServiceRequest.student_id == actor.student_id)
        .with_for_update(of=ServiceRequest)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.status == "cancelled":
        return {"id": row.id, "status": row.status}
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="request_already_resolved")
    row.status = "cancelled"
    row.resolved_at = datetime.now(UTC)
    row.details = {**row.details, "phase": "resolved"}
    await session.commit()
    return {"id": row.id, "status": row.status}


@router.post("/requests/{request_id}/remind-later")
async def remind_service_request_later(
    request_id: int,
    payload: ReminderSnoozeRequest,
    request: Request,
    actor: ActorDep,
    session: SessionDep,
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    row = await lock_service_request(session, request_id)
    if row is None or (not actor.is_admin and row.teacher_id != teacher.id):
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="request_already_resolved")
    row.next_reminder_at = datetime.now(UTC) + timedelta(minutes=payload.minutes)
    await session.commit()
    return service_request_json(row, settings)


@router.post("/requests/{request_id}/reject")
async def reject_service_request(
    request_id: int, payload: DecisionRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    row = await lock_service_request(session, request_id)
    if row is None or (not actor.is_admin and row.teacher_id != teacher.id):
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.status == "rejected":
        return service_request_json(row, settings)
    if row.status != "pending" or row.details.get("phase", "teacher_review") != "teacher_review":
        raise HTTPException(status_code=409, detail="request_waiting_for_student")
    row.status = "rejected"
    row.resolved_by_teacher_id = teacher.id
    row.resolved_at = datetime.now(UTC)
    row.details = {
        **row.details,
        "decision_reason": payload.reason.strip() if payload.reason else None,
        "phase": "resolved",
    }
    await session.commit()
    if row.lesson is not None:
        await notify_lesson_people(request.app.state.bot, row.lesson, "❌ Zgłoszenie odrzucone.", settings)
    return service_request_json(row, settings)


@router.get("/reschedule-requests")
async def reschedule_requests(request: Request, actor: ActorDep, session: SessionDep) -> dict:
    settings: Settings = request.app.state.settings
    teacher = await actor_teacher(session, actor, settings)
    query = select(ServiceRequest).options(joinedload(ServiceRequest.student)).where(
        ServiceRequest.type == "lesson_reschedule"
    )
    if actor.role == "student":
        query = query.where(ServiceRequest.student_id == actor.student_id)
    elif not actor.is_admin:
        if teacher is None:
            raise HTTPException(status_code=404, detail="account_not_found")
        query = query.where(ServiceRequest.teacher_id == teacher.id)
    rows = list(await session.scalars(query.order_by(ServiceRequest.created_at.desc())))
    return {
        "requests": [
            {
                "id": row.id,
                "lesson_id": row.lesson_id,
                "student_name": row.student.name,
                "proposed_datetime": utc_to_local(proposed_datetime(row), settings.tz).isoformat(),
                "status": row.status,
                "reason": row.comment,
            }
            for row in rows
        ]
    }


@router.post("/reschedule-requests/{request_id}/accept")
async def accept_reschedule(
    request_id: int, payload: DecisionRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    row = await session.scalar(
        select(ServiceRequest)
        .options(
            joinedload(ServiceRequest.lesson).joinedload(Lesson.teacher),
            joinedload(ServiceRequest.lesson).joinedload(Lesson.student),
        )
        .where(ServiceRequest.id == request_id, ServiceRequest.type == "lesson_reschedule")
        .execution_options(populate_existing=True)
        .with_for_update(of=ServiceRequest)
    )
    if row is None or (not actor.is_admin and row.teacher_id != teacher.id):
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.status == "approved":
        return {"id": row.id, "status": row.status}
    if row.status != "pending" or row.lesson is None:
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.details.get("phase", "teacher_review") != "teacher_review":
        raise HTTPException(status_code=409, detail="request_waiting_for_student")
    when = proposed_datetime(row)
    service = LessonService(session)
    await conflict_or_availability(
        session,
        service,
        when,
        row.lesson.duration_minutes,
        row.lesson.teacher_id,
        row.lesson.student_id,
        settings,
        force=payload.force,
        exclude_lesson_id=row.lesson_id,
    )
    await service.reschedule(row.lesson, when)
    row.status = "approved"
    row.resolved_by_teacher_id = teacher.id
    row.resolved_at = datetime.now(UTC)
    row.details = {**row.details, "phase": "resolved"}
    await session.commit()
    await notify_lesson_people(request.app.state.bot, row.lesson, "✅ Запрос на перенос принят.", settings)
    return {"id": row.id, "status": row.status}


@router.post("/reschedule-requests/{request_id}/reject")
async def reject_reschedule(
    request_id: int, payload: DecisionRequest, request: Request, actor: ActorDep, session: SessionDep
) -> dict:
    if actor.role == "student":
        raise HTTPException(status_code=403, detail="teacher_only")
    settings: Settings = request.app.state.settings
    teacher = await require_teacher(session, actor, settings)
    row = await session.scalar(
        select(ServiceRequest)
        .options(
            joinedload(ServiceRequest.lesson).joinedload(Lesson.teacher),
            joinedload(ServiceRequest.lesson).joinedload(Lesson.student),
        )
        .where(ServiceRequest.id == request_id, ServiceRequest.type == "lesson_reschedule")
        .execution_options(populate_existing=True)
        .with_for_update(of=ServiceRequest)
    )
    if row is None or (not actor.is_admin and row.teacher_id != teacher.id):
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.details.get("phase", "teacher_review") != "teacher_review":
        raise HTTPException(status_code=409, detail="request_waiting_for_student")
    reason = payload.reason.strip() if payload.reason else None
    if row.status == "rejected" and row.details.get("decision_reason") == reason:
        return {"id": row.id, "status": row.status}
    if row.status != "pending" or row.lesson is None:
        raise HTTPException(status_code=404, detail="request_not_found")
    row.status = "rejected"
    row.details = {**row.details, "decision_reason": reason, "phase": "resolved"}
    row.resolved_by_teacher_id = teacher.id
    row.resolved_at = datetime.now(UTC)
    await session.commit()
    await notify_lesson_people(request.app.state.bot, row.lesson, "❌ Запрос на перенос отклонён.", settings)
    return {"id": row.id, "status": row.status}


@router.post("/reschedule-requests/{request_id}/cancel")
async def cancel_reschedule(request_id: int, actor: ActorDep, session: SessionDep) -> dict:
    if actor.role != "student":
        raise HTTPException(status_code=403, detail="student_only")
    row = await session.scalar(
        select(ServiceRequest)
        .where(
            ServiceRequest.id == request_id,
            ServiceRequest.type == "lesson_reschedule",
            ServiceRequest.student_id == actor.student_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="request_not_found")
    if row.status == "cancelled":
        return {"id": row.id, "status": row.status}
    if row.status != "pending":
        raise HTTPException(status_code=404, detail="request_not_found")
    row.status = "cancelled"
    row.resolved_at = datetime.now(UTC)
    row.details = {**row.details, "phase": "resolved"}
    await session.commit()
    return {"id": row.id, "status": row.status}


async def export_data(
    session: AsyncSession, actor: Actor, settings: Settings, month: date | None, study_chat_id: int | None
) -> tuple[bytes, str]:
    teacher = await actor_teacher(session, actor, settings)
    selected_student_id = actor.student_id
    if study_chat_id is not None:
        chat = await scoped_chat(session, actor, settings, study_chat_id)
        if chat.student_id is None:
            raise HTTPException(status_code=409, detail="student_not_assigned")
        selected_student_id = chat.student_id
    start = end = None
    if month:
        start, end = local_range(
            month.replace(day=1), (month.replace(day=28) + timedelta(days=4)).replace(day=1), settings
        )
    rows = await LessonService(session).list_scope(
        teacher_id=teacher.id if teacher else None,
        student_id=actor.student_id,
        admin=actor.is_admin,
        start=start,
        end=end,
        study_chat_id=study_chat_id,
        limit=10000,
    )
    student_ids = sorted(
        {row.student_id for row in rows if row.student_id is not None}
        | ({actor.student_id} if actor.student_id else set())
    )
    account = AccountService(session)
    receipts = await account.list_receipts(
        teacher_id=teacher.id if teacher else None, student_id=actor.student_id, admin=actor.is_admin
    )
    if actor.role == "student" or study_chat_id:
        if selected_student_id is None:
            raise HTTPException(status_code=404, detail="student_not_found")
        history = await account.history(selected_student_id)
        return build_account_workbook(
            rows, [row for row in receipts if row.student_id == selected_student_id], history, settings.tz
        ), "konto-ucznia.xlsx"
    balances = [(await session.get(Student, sid), await account.balance(sid)) for sid in student_ids]
    return build_full_workbook(rows, receipts, balances, settings.tz), "rozliczenia.xlsx"


@router.post("/exports/send")
async def send_export(
    request: Request, actor: ActorDep, session: SessionDep, month: str | None = None, study_chat_id: int | None = None
) -> dict:
    selected = None
    if month:
        try:
            selected = datetime.strptime(month, "%Y-%m").date()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_month") from exc
    content, filename = await export_data(session, actor, request.app.state.settings, selected, study_chat_id)
    record_audit(
        session,
        actor,
        "send",
        "export",
        actor.user.id,
        after={"month": month, "study_chat_id": study_chat_id, "filename": filename},
    )
    await session.commit()
    bot: Bot | None = request.app.state.bot
    if bot is None:
        raise HTTPException(status_code=503, detail="telegram_not_configured")
    await bot.send_document(actor.user.id, BufferedInputFile(content, filename=filename), caption="📊 Отчёт готов")
    return {"sent": True, "filename": filename}


@router.get("/exports.xlsx")
async def export_excel(
    request: Request, actor: ActorDep, session: SessionDep, month: str | None = None, study_chat_id: int | None = None
) -> Response:
    selected = None
    if month:
        try:
            selected = datetime.strptime(month, "%Y-%m").date()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_month") from exc
    content, filename = await export_data(session, actor, request.app.state.settings, selected, study_chat_id)
    record_audit(
        session,
        actor,
        "download",
        "export",
        actor.user.id,
        after={"month": month, "study_chat_id": study_chat_id, "filename": filename},
    )
    await session.commit()
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def create_web_app(settings: Settings, *, engine: AsyncEngine | None = None, bot: Bot | None = None) -> FastAPI:
    own_engine = engine is None
    engine = engine or create_engine(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if own_engine:
            await engine.dispose()

    app = FastAPI(title="Korepetycje CRM Mini App", docs_url=None, redoc_url=None, lifespan=lifespan)
    install_security_middleware(app)
    @app.middleware("http")
    async def log_slow_requests(request: Request, call_next):
        started = perf_counter()
        response = await call_next(request)
        elapsed_ms = (perf_counter() - started) * 1000
        if request.url.path.startswith("/api/") and elapsed_ms >= settings.slow_request_ms:
            logger.warning(
                "Wolne żądanie API: %s %s -> %s (%.0f ms)",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        return response

    app.state.settings, app.state.engine, app.state.session_factory, app.state.bot = (
        settings,
        engine,
        create_session_factory(engine),
        bot,
    )
    app.include_router(router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict:
        try:
            async with engine.connect() as connection:
                await connection.execute(select(1))
        except SQLAlchemyError as exc:
            logger.warning("Aplikacja nie jest gotowa: baza danych jest niedostępna: %s", exc)
            raise HTTPException(status_code=503, detail="database_unavailable") from exc
        return {"status": "ready"}

    static_dir = resource_path(Path("frontend") / "dist")
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> FileResponse:
        candidate = static_dir / path
        if path and candidate.is_file() and static_dir in candidate.resolve().parents:
            return FileResponse(candidate)
        index = static_dir / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503, detail="frontend_not_built")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
