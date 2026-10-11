from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import PaymentFlow
from app.config import Settings
from app.database.models import Student, StudentLinkToken, Teacher
from app.services.accounts import AccountService, LinkService, parse_payment_amount, quiet_now
from app.services.chats import ChatService
from app.services.lessons import DomainError

logger = logging.getLogger(__name__)


def full_name(user) -> str:
    return " ".join(part for part in (user.first_name, user.last_name) if part) or str(user.id)


def mini_app_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=url))]]
    )


def currency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="PLN", callback_data="pay_currency:PLN"),
                InlineKeyboardButton(text="UAH", callback_data="pay_currency:UAH"),
                InlineKeyboardButton(text="EUR", callback_data="pay_currency:EUR"),
            ]
        ]
    )


async def teacher_for(session: AsyncSession, user_id: int, settings: Settings) -> Teacher | None:
    teacher = await session.scalar(select(Teacher).where(Teacher.telegram_user_id == user_id, Teacher.active.is_(True)))
    if teacher is None and user_id == settings.owner_telegram_id:
        teacher = await ChatService(session).ensure_teacher(user_id, None, is_admin=True)
    return teacher


async def student_for(session: AsyncSession, user_id: int) -> Student | None:
    return await session.scalar(select(Student).where(Student.telegram_user_id == user_id, Student.active.is_(True)))


async def pending_student_link(session: AsyncSession, user_id: int) -> StudentLinkToken | None:
    return await session.scalar(
        select(StudentLinkToken).where(
            StudentLinkToken.claimed_telegram_user_id == user_id,
            StudentLinkToken.claimed_at.is_not(None),
            StudentLinkToken.approved_at.is_(None),
            StudentLinkToken.expires_at > datetime.now(UTC),
        )
    )


def create_router(settings: Settings, web_app_url: Callable[[], str | None] | None = None) -> Router:
    router = Router(name="tutor")

    async def show_app(message: Message, label: str = "Приложение готово.") -> None:
        url = web_app_url() if web_app_url else None
        if not url:
            await message.answer("Приложение ещё запускается. Попробуйте снова через несколько секунд.")
            return
        await message.answer(label, reply_markup=mini_app_keyboard(url))

    @router.message(Command("start"))
    async def start(message: Message, session: AsyncSession, command: CommandObject) -> None:
        if not message.from_user:
            return
        if command.args and command.args.startswith("link_"):
            try:
                link = await LinkService(session).claim(command.args.removeprefix("link_"), message.from_user.id)
                student = await session.get(Student, link.student_id)
                teacher = await session.get(Teacher, student.teacher_id) if student else None
                await session.commit()
                await message.answer(
                    "✅ Запрос на подключение отправлен преподавателю. После подтверждения откройте /menu."
                )
                if teacher:
                    url = web_app_url() if web_app_url else None
                    await message.bot.send_message(
                        teacher.telegram_user_id,
                        f"🔔 Ученик запросил подключение: {student.name if student else 'ученик'}.",
                        reply_markup=mini_app_keyboard(url) if url else None,
                    )
            except DomainError as exc:
                await message.answer(
                    "Ссылка недействительна или уже использована."
                    if str(exc) != "link_expired"
                    else "Срок действия ссылки истёк."
                )
            return
        teacher = await teacher_for(session, message.from_user.id, settings)
        student = await student_for(session, message.from_user.id)
        if not teacher and not student:
            if await pending_student_link(session, message.from_user.id):
                await message.answer(
                    "⏳ Запрос ожидает подтверждения преподавателя. После подтверждения снова откройте /menu."
                )
                return
            await message.answer("⛔ Аккаунт ещё не подключён. Используйте ссылку из учебной группы.")
            return
        if message.chat.type != "private":
            await message.answer("Для подключения учебной группы используйте /setup.")
            return
        await show_app(
            message, "Панель администратора готова." if teacher and teacher.is_admin else "Личный кабинет готов."
        )

    @router.message(Command("menu"))
    async def menu(message: Message, session: AsyncSession) -> None:
        if not message.from_user or (
            not await teacher_for(session, message.from_user.id, settings)
            and not await student_for(session, message.from_user.id)
        ):
            if message.from_user and await pending_student_link(session, message.from_user.id):
                await message.answer(
                    "⏳ Zapyt czeka na zatwierdzenie wykładowcy. Po zatwierdzeniu ponownie użyj /menu."
                )
                return
            await message.answer("⛔ Аккаунт не подключён.")
            return
        if message.chat.type == "private":
            await show_app(message)
        else:
            await message.answer("Откройте приложение в личном чате с ботом.", disable_notification=True)

    @router.message(Command("setup"))
    async def setup(message: Message, session: AsyncSession) -> None:
        if not message.from_user:
            return
        teacher = await teacher_for(session, message.from_user.id, settings)
        if teacher is None:
            await message.answer("⛔ Эта команда доступна только активному преподавателю.")
            return
        if message.chat.type not in {"group", "supergroup"}:
            await message.answer("Команда /setup работает только в группе с учеником.")
            return
        chat = await ChatService(session).setup_chat(
            message.chat.id,
            teacher.telegram_user_id,
            teacher.name or full_name(message.from_user),
            message.chat.title,
            is_admin=teacher.is_admin,
        )
        token = await LinkService(session).create(chat.student_id)
        bot_user = await message.bot.get_me()
        await session.commit()
        link = f"https://t.me/{bot_user.username}?start=link_{token}"
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Подключить аккаунт ученика", url=link)]]
        )
        if chat.student_name:
            text = f"✅ Группа подключена. Ученик: {chat.student_name}"
        else:
            text = "✅ Группа подключена и ожидает назначения. Укажите имя ученика в приложении."
        await message.answer(text, reply_markup=markup, disable_notification=True)

    @router.message(Command("cancel"))
    async def cancel_state(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Отменено.")

    @router.message(Command("pay", "pay_pln", "pay_uah", "pay_eur"))
    async def payment(message: Message, session: AsyncSession, state: FSMContext) -> None:
        if (
            not message.from_user
            or message.chat.type != "private"
            or not await student_for(session, message.from_user.id)
        ):
            await message.answer("⛔ Команда доступна подключённому ученику в личном чате.")
            return
        command = message.text.split()[0].lower() if message.text else "/pay"
        currency = {"/pay_pln": "PLN", "/pay_uah": "UAH", "/pay_eur": "EUR"}.get(command)
        if currency:
            await state.update_data(currency=currency)
            await state.set_state(PaymentFlow.waiting_for_amount)
            await message.answer(f"Введите сумму платежа в {currency}, например 150 или 150,50.")
        else:
            await state.set_state(PaymentFlow.choosing_currency)
            await message.answer("Выберите валюту платежа:", reply_markup=currency_keyboard())

    @router.callback_query(PaymentFlow.choosing_currency, F.data.startswith("pay_currency:"))
    async def payment_currency(callback: CallbackQuery, state: FSMContext) -> None:
        currency = callback.data.removeprefix("pay_currency:")
        if currency not in {"PLN", "UAH", "EUR"}:
            await callback.answer("Недоступная валюта", show_alert=True)
            return
        await state.update_data(currency=currency)
        await state.set_state(PaymentFlow.waiting_for_amount)
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(f"Введите сумму платежа в {currency}, например 150 или 150,50.")

    @router.message(PaymentFlow.waiting_for_amount, F.text)
    async def payment_amount(message: Message, state: FSMContext) -> None:
        try:
            amount = parse_payment_amount(message.text or "")
        except ValueError:
            currency = (await state.get_data()).get("currency", "PLN")
            await message.answer(f"Введите положительную сумму в {currency}, например 150 или 150,50.")
            return
        await state.update_data(submitted_amount=str(amount))
        await state.set_state(PaymentFlow.waiting_for_file)
        await message.answer("Отправьте фотографию или PDF с подтверждением оплаты.")

    @router.message(PaymentFlow.waiting_for_amount)
    async def wrong_payment_amount(message: Message) -> None:
            await message.answer("Введите сумму цифрами, например 150 или 150,50, либо /cancel.")

    @router.message(PaymentFlow.waiting_for_file, F.photo | F.document)
    async def payment_file(message: Message, session: AsyncSession, state: FSMContext) -> None:
        student = await student_for(session, message.from_user.id)
        if student is None:
            await state.clear()
            return
        if message.photo:
            file_id, file_type = message.photo[-1].file_id, "photo"
        elif message.document and message.document.mime_type == "application/pdf":
            file_id, file_type = message.document.file_id, "document"
        else:
            await message.answer("Нужна фотография или PDF.")
            return
        data = await state.get_data()
        currency = data.get("currency", "PLN")
        try:
            submitted_amount = parse_payment_amount(str(data["submitted_amount"]))
        except (KeyError, ValueError):
            await state.set_state(PaymentFlow.waiting_for_amount)
            await message.answer(f"Сначала введите сумму платежа в {currency}.")
            return
        account = AccountService(session)
        receipt = await account.submit_receipt(student, file_id, file_type, submitted_amount, currency)
        teacher = await session.get(Teacher, student.teacher_id)
        await session.commit()
        await state.clear()
        url = web_app_url() if web_app_url else None
        markup = mini_app_keyboard(url) if url else None
        delivered = receipt.teacher_message_id is not None
        if teacher and not delivered:
            caption = (
                f"💳 Новое подтверждение оплаты\n\n"
                f"Ученик: {student.name}\n"
                f"Сумма: {receipt.submitted_amount:.2f} {receipt.currency}\n"
                f"Платёж №{receipt.id}\n"
                "Статус: ожидает проверки"
            )
            silent = quiet_now(
                teacher.quiet_hours_start,
                teacher.quiet_hours_end,
                datetime.now(settings.tz).time(),
            )
            try:
                if file_type == "photo":
                    sent = await message.bot.send_photo(
                        teacher.telegram_user_id,
                        file_id,
                        caption=caption,
                        reply_markup=markup,
                        disable_notification=silent,
                    )
                else:
                    sent = await message.bot.send_document(
                        teacher.telegram_user_id,
                        file_id,
                        caption=caption,
                        reply_markup=markup,
                        disable_notification=silent,
                    )
                await account.mark_receipt_delivered(receipt, teacher.telegram_user_id, sent.message_id)
                await session.commit()
                delivered = True
            except Exception as exc:
                await session.rollback()
                logger.warning("Nie udało się przekazać potwierdzenia płatności %s: %s", receipt.id, exc)
        await message.answer(
            "✅ Подтверждение отправлено преподавателю."
            if delivered
            else "✅ Подтверждение сохранено, но не удалось уведомить преподавателя. Оно доступно в приложении."
        )

    @router.message(PaymentFlow.waiting_for_file)
    async def wrong_payment_file(message: Message) -> None:
        await message.answer("Отправьте фотографию или PDF, либо /cancel.")

    @router.message(Command("student"))
    async def legacy_student(message: Message) -> None:
        await message.answer("Назначение ученика выполняется в приложении.")

    @router.callback_query()
    async def legacy_callback(callback: CallbackQuery, session: AsyncSession) -> None:
        if not await teacher_for(session, callback.from_user.id, settings) and not await student_for(
            session, callback.from_user.id
        ):
            await callback.answer("Нет доступа", show_alert=True)
            return
        if callback.message:
            url = web_app_url() if web_app_url else None
            await callback.message.answer(
                "Это меню больше не используется. Откройте приложение.",
                reply_markup=mini_app_keyboard(url) if url and callback.message.chat.type == "private" else None,
            )
        await callback.answer()

    return router
