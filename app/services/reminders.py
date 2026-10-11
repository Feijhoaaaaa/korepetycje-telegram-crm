from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import Bot
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import Settings
from app.database.models import ServiceRequest
from app.services.accounts import quiet_now
from app.services.notifications import notify_private


def reminder_delay(reminder_count: int, settings: Settings) -> timedelta:
    minutes = (
        settings.reminder_first_minutes
        if reminder_count == 0
        else settings.reminder_second_minutes
        if reminder_count == 1
        else settings.reminder_later_minutes
    )
    return timedelta(minutes=minutes)


def reminder_text(row: ServiceRequest) -> str:
    labels = {
        "payment_confirmation": "potwierdzenie płatności",
        "lesson_reschedule": "przeniesienie zajęcia",
        "lesson_cancellation": "anulowanie zajęcia",
        "lesson_time_proposal": "propozycję terminu zajęć",
        "other": "zgłoszenie",
    }
    return f"⏰ Przypomnienie: czeka na Ciebie {labels.get(row.type, 'zgłoszenie')} ucznia."


async def run_reminder_once(
    session: AsyncSession,
    bot: Bot | None,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(UTC)
    first_delay = timedelta(minutes=settings.reminder_first_minutes)
    row = await session.scalar(
        select(ServiceRequest)
        .options(joinedload(ServiceRequest.student), joinedload(ServiceRequest.teacher))
        .where(
            ServiceRequest.status == "pending",
            or_(
                ServiceRequest.next_reminder_at <= now,
                and_(
                    ServiceRequest.next_reminder_at.is_(None),
                    ServiceRequest.created_at <= now - first_delay,
                ),
            ),
        )
        .order_by(ServiceRequest.next_reminder_at.asc().nulls_last(), ServiceRequest.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True, of=ServiceRequest)
    )
    if row is None:
        return False

    row.last_reminder_at = now
    row.reminder_count += 1
    row.next_reminder_at = now + reminder_delay(row.reminder_count - 1, settings)
    await session.commit()

    teacher = row.teacher
    if teacher is None:
        return True
    await notify_private(
        bot,
        teacher.telegram_user_id,
        reminder_text(row),
        silent=quiet_now(
            teacher.quiet_hours_start,
            teacher.quiet_hours_end,
            now.astimezone(settings.tz).time(),
        ),
    )
    return True
