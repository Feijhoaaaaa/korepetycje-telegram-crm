from __future__ import annotations

from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import ConfirmCb, FlowCb, LessonCb, MenuCb
from app.database.models import Lesson, StudyChat
from app.utils.dates import utc_to_local


def private_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Сегодня", callback_data=MenuCb(action="today"))
    builder.button(text="📆 Неделя", callback_data=MenuCb(action="week"))
    builder.button(text="👨‍🎓 Ученики", callback_data=MenuCb(action="students"))
    builder.button(text="💰 Неоплаченные", callback_data=MenuCb(action="unpaid"))
    builder.button(text="📊 Отчёт", callback_data=MenuCb(action="report"))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def group_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить занятие", callback_data=MenuCb(action="add"))
    builder.button(text="📅 Ближайшие", callback_data=MenuCb(action="upcoming"))
    builder.button(text="📚 История", callback_data=MenuCb(action="history"))
    builder.button(text="💰 Оплаты", callback_data=MenuCb(action="payments"))
    builder.button(text="📊 Excel", callback_data=MenuCb(action="report"))
    builder.adjust(1, 2, 2)
    return builder.as_markup()


def date_keyboard(today: date) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for shift in range(7):
        day = today + timedelta(days=shift)
        label = "Сегодня" if shift == 0 else "Завтра" if shift == 1 else day.strftime("%d.%m")
        builder.button(text=label, callback_data=FlowCb(action="date", value=day.isoformat()))
    builder.button(text="⌨️ Ввести дату", callback_data=FlowCb(action="manual_date"))
    builder.button(text="❌ Отмена", callback_data=FlowCb(action="abort"))
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def time_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for hour in range(10, 20):
        value = f"{hour:02d}:00"
        builder.button(text=value, callback_data=FlowCb(action="time", value=value))
    builder.button(text="⌨️ Ввести время", callback_data=FlowCb(action="manual_time"))
    builder.button(text="❌ Отмена", callback_data=FlowCb(action="abort"))
    builder.adjust(2, 2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def flow_confirmation() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить", callback_data=FlowCb(action="save"))
    builder.button(text="❌ Отмена", callback_data=FlowCb(action="abort"))
    builder.adjust(2)
    return builder.as_markup()


def conflict_confirmation() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚠️ Сохранить всё равно", callback_data=FlowCb(action="force_save"))
    builder.button(text="⬅️ Выбрать другое время", callback_data=FlowCb(action="back_time"))
    builder.button(text="❌ Отмена", callback_data=FlowCb(action="abort"))
    builder.adjust(1)
    return builder.as_markup()


def lessons_keyboard(lessons: list[Lesson], timezone, *, page: int, has_next: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for lesson in lessons:
        local = utc_to_local(lesson.lesson_datetime, timezone)
        builder.button(
            text=local.strftime("%d.%m — %H:%M"),
            callback_data=LessonCb(action="view", lesson_id=lesson.id),
        )
    if page > 0:
        builder.button(text="⬅️", callback_data=MenuCb(action="upcoming", value=page - 1))
    if has_next:
        builder.button(text="➡️", callback_data=MenuCb(action="upcoming", value=page + 1))
    builder.button(text="➕ Добавить", callback_data=MenuCb(action="add"))
    builder.adjust(1)
    return builder.as_markup()


def lesson_actions(lesson: Lesson) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if lesson.status == "planned":
        builder.button(text="✅ Проведено", callback_data=LessonCb(action="complete", lesson_id=lesson.id))
        builder.button(text="🔄 Перенести", callback_data=LessonCb(action="reschedule", lesson_id=lesson.id))
        builder.button(text="❌ Отменить", callback_data=ConfirmCb(action="cancel", entity_id=lesson.id))
    if lesson.status == "completed":
        builder.button(text="✅ Оплачено", callback_data=LessonCb(action="paid", lesson_id=lesson.id))
        builder.button(text="⏳ Не оплачено", callback_data=LessonCb(action="unpaid", lesson_id=lesson.id))
    builder.button(text="🗑 Удалить", callback_data=ConfirmCb(action="delete", entity_id=lesson.id))
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def destructive_confirmation(action: str, entity_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=LessonCb(action=action, lesson_id=entity_id))
    builder.button(text="⬅️ Назад", callback_data=LessonCb(action="view", lesson_id=entity_id))
    builder.adjust(2)
    return builder.as_markup()


def payment_prompt(lesson_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Оплачено", callback_data=LessonCb(action="paid", lesson_id=lesson_id))
    builder.button(text="⏳ Не оплачено", callback_data=LessonCb(action="unpaid", lesson_id=lesson_id))
    builder.adjust(2)
    return builder.as_markup()


def students_keyboard(chats: list[StudyChat]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for chat in chats:
        builder.button(
            text=chat.student_name or "Без имени",
            callback_data=MenuCb(action="student", value=chat.id),
        )
    builder.adjust(1)
    return builder.as_markup()


def report_keyboard(study_chat_id: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Excel за месяц", callback_data=MenuCb(action="export_month", value=study_chat_id))
    builder.button(text="📥 Excel за всё время", callback_data=MenuCb(action="export_all", value=study_chat_id))
    builder.adjust(1)
    return builder.as_markup()


def history_keyboard(study_chat_id: int, month_offset: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️", callback_data=MenuCb(action="history", value=month_offset - 1).pack()),
                InlineKeyboardButton(text="➡️", callback_data=MenuCb(action="history", value=month_offset + 1).pack()),
            ]
        ]
    )
