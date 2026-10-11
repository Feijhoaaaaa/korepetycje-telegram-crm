import asyncio
import os
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import delete, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.handlers import create_router
from app.bot.states import PaymentFlow
from app.config import Settings
from app.database.models import Base, PaymentReceipt, ServiceRequest, Student, StudyChat
from app.services.accounts import AccountService, LinkService, quiet_now
from app.services.chats import ChatService
from app.services.lessons import DomainError, LessonService
from app.services.reminders import run_reminder_once

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


async def pending_receipt(session, chat, file_id: str, submitted_amount: Decimal | None = None) -> PaymentReceipt:
    receipt = PaymentReceipt(
        student_id=chat.student_id,
        teacher_id=chat.teacher_id,
        telegram_file_id=file_id,
        telegram_file_type="photo",
        submitted_amount=submitted_amount,
    )
    session.add(receipt)
    await session.flush()
    receipt.request = ServiceRequest(
        type="payment_confirmation",
        student_id=chat.student_id,
        teacher_id=chat.teacher_id,
        payment_id=receipt.id,
    )
    session.add(receipt.request)
    await session.flush()
    return receipt


@pytest_asyncio.fixture
async def session():
    assert TEST_DATABASE_URL and "test" in TEST_DATABASE_URL.lower(), "Refusing non-test database"
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS tutor_bot CASCADE"))
        await connection.execute(text("CREATE SCHEMA tutor_bot"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
        await value.rollback()
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS tutor_bot CASCADE"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_lesson_lifecycle_and_soft_delete(session) -> None:
    chat_service = ChatService(session)
    chat = await chat_service.setup_chat(-1001, 42, "Иван")
    await chat_service.assign_student(chat, 77, "Анна")
    lesson_service = LessonService(session)
    lesson = await lesson_service.create(chat.id, datetime(2026, 9, 24, 15, tzinfo=UTC))
    await lesson_service.complete(lesson)
    await lesson_service.set_payment(lesson, "unpaid")

    assert lesson.status == "completed"
    assert lesson.payment_status == "unpaid"
    assert len(await lesson_service.unpaid(42)) == 1

    await lesson_service.soft_delete(lesson, 42)
    assert await lesson_service.get(lesson.id) is None
    assert await lesson_service.export_rows(42, timezone=ZoneInfo("Europe/Warsaw")) == []


@pytest.mark.asyncio
async def test_invalid_transition_is_rejected(session) -> None:
    chat = await ChatService(session).setup_chat(-1002, 42, "Иван")
    lesson = await LessonService(session).create(chat.id, datetime(2026, 9, 24, 15, tzinfo=UTC))
    await LessonService(session).cancel(lesson)
    with pytest.raises(DomainError, match="invalid_lesson_transition"):
        await LessonService(session).complete(lesson)


@pytest.mark.asyncio
async def test_conflict_ignores_cancelled_lesson(session) -> None:
    chat = await ChatService(session).setup_chat(-1003, 42, "Иван")
    when = datetime(2026, 9, 24, 15, tzinfo=UTC)
    service = LessonService(session)
    lesson = await service.create(chat.id, when)
    assert [item.id for item in await service.conflicts(when)] == [lesson.id]
    await service.cancel(lesson)
    assert await service.conflicts(when) == []


@pytest.mark.asyncio
async def test_balance_pool_and_full_lesson_settlement(session) -> None:
    chat = await ChatService(session).setup_chat(-1004, 42, "Иван")
    await ChatService(session).assign_student(chat, 78, "Мария")
    lesson_service = LessonService(session)
    first = await lesson_service.create(chat.id, datetime(2026, 9, 24, 15, tzinfo=UTC), price=Decimal("100"))
    second = await lesson_service.create(chat.id, datetime(2026, 9, 25, 15, tzinfo=UTC), price=Decimal("120"))
    await lesson_service.complete(first)
    await lesson_service.complete(second)
    account = AccountService(session)
    receipt = await pending_receipt(session, chat, "file")
    await account.approve(receipt, Decimal("250"), [first.id, second.id], chat.teacher_id, 42)

    assert await account.balance(chat.student_id) == Decimal("30.00")
    assert await account.available_pool(chat.student_id) == Decimal("30.00")
    assert first.payment_status == second.payment_status == "paid"
    with pytest.raises(DomainError, match="payment_funds_allocated"):
        await account.correct_receipt(
            receipt,
            status="approved",
            amount=Decimal("200"),
            reason=None,
            reviewer_id=chat.teacher_id,
            actor_telegram_id=42,
        )
    await account.correct_receipt(
        receipt,
        status="approved",
        amount=Decimal("230"),
        reason=None,
        reviewer_id=chat.teacher_id,
        actor_telegram_id=42,
    )
    assert await account.available_pool(chat.student_id) == Decimal("10.00")


@pytest.mark.asyncio
async def test_payment_approval_is_idempotent(session) -> None:
    chat = await ChatService(session).setup_chat(-1014, 42, "Иван")
    await ChatService(session).assign_student(chat, 88, "Мария")
    lesson = await LessonService(session).create(
        chat.id, datetime(2026, 9, 24, 15, tzinfo=UTC), price=Decimal("100")
    )
    await LessonService(session).complete(lesson)
    receipt = await pending_receipt(session, chat, "same-file")

    account = AccountService(session)
    await account.approve(receipt, Decimal("100"), [lesson.id], chat.teacher_id, 42)
    await account.approve(receipt, Decimal("100"), [lesson.id], chat.teacher_id, 42)

    assert lesson.payment_status == "paid"
    assert await session.scalar(text("select count(*) from tutor_bot.lesson_settlements")) == 1


@pytest.mark.asyncio
async def test_concurrent_payment_approval_creates_one_settlement() -> None:
    assert TEST_DATABASE_URL and "test" in TEST_DATABASE_URL.lower(), "Refusing non-test database"
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS tutor_bot CASCADE"))
        await connection.execute(text("CREATE SCHEMA tutor_bot"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as seed:
        chat = await ChatService(seed).setup_chat(-1020, 42, "Иван")
        await ChatService(seed).assign_student(chat, 903, "Мария")
        lesson = await LessonService(seed).create(
            chat.id, datetime(2026, 9, 24, 15, tzinfo=UTC), price=Decimal("100")
        )
        await LessonService(seed).complete(lesson)
        receipt = await pending_receipt(seed, chat, "concurrent-file", Decimal("100"))
        await seed.commit()
        receipt_id, lesson_id, teacher_id = receipt.id, lesson.id, chat.teacher_id

    async def approve() -> None:
        async with factory() as worker:
            receipt = await worker.get(PaymentReceipt, receipt_id)
            await AccountService(worker).approve(receipt, Decimal("100"), [lesson_id], teacher_id, 42)
            await worker.commit()

    await asyncio.gather(approve(), approve())

    async with factory() as check:
        assert await check.scalar(text("select count(*) from tutor_bot.lesson_settlements")) == 1
        assert await check.scalar(text("select count(*) from tutor_bot.audit_log where action = 'approve'")) == 1
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA tutor_bot CASCADE"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_receipt_submit_reuses_same_telegram_file(session) -> None:
    chat = await ChatService(session).setup_chat(-1015, 42, "Иван")
    await ChatService(session).assign_student(chat, 89, "Мария")
    student = await session.get(Student, chat.student_id)
    service = AccountService(session)

    first = await service.submit_receipt(student, "telegram-file", "photo", Decimal("125.50"))
    second = await service.submit_receipt(student, "telegram-file", "photo", Decimal("999.00"))

    assert second.id == first.id
    assert second.submitted_amount == Decimal("125.50")
    assert await session.scalar(text("select count(*) from tutor_bot.payment_receipts")) == 1


class FakeState:
    def __init__(self) -> None:
        self.state = None
        self.data = {}

    async def set_state(self, value) -> None:
        self.state = value

    async def update_data(self, **values) -> None:
        self.data.update(values)

    async def get_data(self) -> dict:
        return self.data

    async def clear(self) -> None:
        self.state = None
        self.data = {}


def payment_handler(name: str):
    router = create_router(
        Settings(
            bot_token="123456:testing",
            owner_telegram_id=42,
            database_url="postgresql+asyncpg://user:pass@localhost/test",
        ),
        lambda: "https://example.test/app",
    )
    return next(item.callback for item in router.message.handlers if item.callback.__name__ == name)


@pytest.mark.asyncio
async def test_payment_bot_flow_sends_receipt_once_and_records_delivery(session) -> None:
    chat = await ChatService(session).setup_chat(-1018, 42, "Иван")
    await ChatService(session).assign_student(chat, 901, "Мария")
    await session.commit()
    state = FakeState()
    bot = AsyncMock()
    bot.send_photo.return_value = SimpleNamespace(message_id=555)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=901),
        chat=SimpleNamespace(type="private"),
        text=None,
        photo=[SimpleNamespace(file_id="receipt-photo")],
        document=None,
        bot=bot,
        answer=AsyncMock(),
    )

    await payment_handler("payment")(message, session, state)
    assert state.state == PaymentFlow.choosing_currency
    state.data = {"currency": "PLN"}
    await state.set_state(PaymentFlow.waiting_for_amount)
    message.text = "150,50"
    await payment_handler("payment_amount")(message, state)
    assert state.state == PaymentFlow.waiting_for_file
    await payment_handler("payment_file")(message, session, state)

    receipt = await session.scalar(
        text("select id from tutor_bot.payment_receipts where telegram_file_id = 'receipt-photo'")
    )
    row = await session.get(PaymentReceipt, receipt)
    assert row.submitted_amount == Decimal("150.50")
    assert (row.teacher_chat_id, row.teacher_message_id) == (42, 555)
    bot.send_photo.assert_awaited_once()

    state.data = {"submitted_amount": "150.50"}
    await payment_handler("payment_file")(message, session, state)
    bot.send_photo.assert_awaited_once()

    state.state = PaymentFlow.waiting_for_file
    state.data = {"submitted_amount": "150.50"}
    await payment_handler("cancel_state")(message, state)
    assert state.state is None
    assert state.data == {}


@pytest.mark.asyncio
async def test_payment_bot_keeps_receipt_when_teacher_delivery_fails(session) -> None:
    chat = await ChatService(session).setup_chat(-1019, 42, "Иван")
    await ChatService(session).assign_student(chat, 902, "Анна")
    await session.commit()
    state = FakeState()
    state.data = {"submitted_amount": "80.00"}
    bot = AsyncMock()
    bot.send_document.side_effect = RuntimeError("telegram unavailable")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=902),
        chat=SimpleNamespace(type="private"),
        text=None,
        photo=None,
        document=SimpleNamespace(file_id="receipt-pdf", mime_type="application/pdf"),
        bot=bot,
        answer=AsyncMock(),
    )

    await payment_handler("payment_file")(message, session, state)

    receipt = await session.scalar(
        text("select id from tutor_bot.payment_receipts where telegram_file_id = 'receipt-pdf'")
    )
    row = await session.get(PaymentReceipt, receipt)
    assert row.submitted_amount == Decimal("80.00")
    assert row.teacher_message_id is None
    assert "сохранено" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_conflicts_use_overlapping_intervals_per_teacher_and_student(session) -> None:
    first_chat = await ChatService(session).setup_chat(-1005, 42, "Иван")
    await ChatService(session).assign_student(first_chat, 80, "А")
    service = LessonService(session)
    lesson = await service.create(first_chat.id, datetime(2026, 9, 24, 15, tzinfo=UTC), duration_minutes=90)
    conflicts = await service.conflicts(
        datetime(2026, 9, 24, 16, tzinfo=UTC),
        duration_minutes=60,
        teacher_id=first_chat.teacher_id,
        student_id=first_chat.student_id,
    )
    assert [row.id for row in conflicts] == [lesson.id]
    assert (
        await service.conflicts(
            datetime(2026, 9, 24, 16, 30, tzinfo=UTC),
            duration_minutes=60,
            teacher_id=first_chat.teacher_id,
            student_id=first_chat.student_id,
        )
        == []
    )


@pytest.mark.asyncio
async def test_link_token_is_one_time_and_needs_approval(session) -> None:
    chat = await ChatService(session).setup_chat(-1006, 42, "Иван")
    service = LinkService(session)
    token = await service.create(chat.student_id)
    claimed = await service.claim(token, 999)
    with pytest.raises(DomainError, match="link_already_used"):
        await service.claim(token, 1000)
    student = await service.approve(claimed.id, chat.teacher_id)
    assert student.telegram_user_id == 999
    assert (await service.approve(claimed.id, chat.teacher_id)).id == student.id


@pytest.mark.asyncio
async def test_approving_link_restores_same_archived_student(session) -> None:
    chat = await ChatService(session).setup_chat(-1021, 42, "Иван")
    student = await session.get(Student, chat.student_id)
    student.telegram_user_id = 999
    student.active = False
    chat.active = False
    service = LinkService(session)
    claimed = await service.claim(await service.create(student.id), 999)

    restored = await service.approve(claimed.id, chat.teacher_id)
    assert restored.id == student.id
    assert restored.active is True
    await session.refresh(chat)
    assert chat.active is True


@pytest.mark.asyncio
async def test_linking_id_from_another_archived_student_is_actionable(session) -> None:
    chats = ChatService(session)
    archived_chat = await chats.setup_chat(-1022, 42, "Иван")
    archived_student = await session.get(Student, archived_chat.student_id)
    archived_student.telegram_user_id = 998
    archived_student.active = False
    target_chat = await chats.setup_chat(-1023, 42, "Иван")
    service = LinkService(session)
    claimed = await service.claim(await service.create(target_chat.student_id), 998)

    with pytest.raises(DomainError, match="telegram_user_linked_to_archived_student"):
        await service.approve(claimed.id, target_chat.teacher_id)


@pytest.mark.asyncio
async def test_setup_recreates_student_after_account_deletion(session) -> None:
    chat = await ChatService(session).setup_chat(-1007, 42, "Иван", "Группа")
    old_student_id = chat.student_id
    await session.execute(update(StudyChat).where(StudyChat.id == chat.id).values(student_id=None, active=False))
    await session.execute(delete(Student).where(Student.id == old_student_id))
    await session.flush()

    recreated = await ChatService(session).setup_chat(-1007, 42, "Иван", "Группа")

    assert recreated.student_id is not None
    assert recreated.student_id != old_student_id
    assert recreated.active is True


@pytest.mark.asyncio
async def test_lesson_mutations_are_idempotent(session) -> None:
    chat = await ChatService(session).setup_chat(-1016, 42, "Иван")
    service = LessonService(session)
    complete = await service.create(chat.id, datetime(2026, 9, 24, 15, tzinfo=UTC))
    await service.complete(complete)
    await service.complete(complete)
    assert complete.status == "completed"

    cancelled = await service.create(chat.id, datetime(2026, 9, 25, 15, tzinfo=UTC))
    await service.cancel(cancelled)
    await service.cancel(cancelled)
    assert cancelled.status == "cancelled"

    deleted = await service.create(chat.id, datetime(2026, 9, 26, 15, tzinfo=UTC))
    await service.soft_delete(deleted, 42)
    await service.soft_delete(deleted, 42)
    assert deleted.deleted_by_telegram_id == 42


@pytest.mark.asyncio
async def test_only_one_pending_reschedule_per_lesson(session) -> None:
    chat = await ChatService(session).setup_chat(-1017, 42, "Иван")
    lesson = await LessonService(session).create(chat.id, datetime(2026, 9, 24, 15, tzinfo=UTC))
    session.add(
        ServiceRequest(
            type="lesson_reschedule",
            lesson_id=lesson.id,
            student_id=chat.student_id,
            teacher_id=chat.teacher_id,
            details={"proposed_datetime": datetime(2026, 9, 25, 15, tzinfo=UTC).isoformat()},
        )
    )
    await session.flush()
    session.add(
        ServiceRequest(
            type="lesson_reschedule",
            lesson_id=lesson.id,
            student_id=chat.student_id,
            teacher_id=chat.teacher_id,
            details={"proposed_datetime": datetime(2026, 9, 26, 15, tzinfo=UTC).isoformat()},
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_reminder_claim_is_idempotent_and_stops_after_resolution(session) -> None:
    chat = await ChatService(session).setup_chat(-1018, 42, "Иван")
    request = ServiceRequest(
        type="other",
        student_id=chat.student_id,
        teacher_id=chat.teacher_id,
        created_at=datetime(2026, 9, 28, 10, tzinfo=UTC),
    )
    session.add(request)
    await session.commit()
    bot = AsyncMock()
    settings = Settings(
        bot_token="123456:testing",
        owner_telegram_id=42,
        database_url=TEST_DATABASE_URL,
        timezone="Europe/Warsaw",
    )
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)

    assert await run_reminder_once(session, bot, settings, now) is True
    assert request.reminder_count == 1
    assert request.next_reminder_at == datetime(2026, 9, 28, 13, tzinfo=UTC)
    bot.send_message.assert_awaited_once()
    assert await run_reminder_once(session, bot, settings, now) is False

    request.status = "approved"
    request.resolved_at = now
    request.next_reminder_at = now - timedelta(minutes=1)
    await session.commit()
    assert await run_reminder_once(session, bot, settings, now) is False


def test_quiet_hours_can_cross_midnight() -> None:
    assert quiet_now(time(22), time(7), time(23, 30))
    assert quiet_now(time(22), time(7), time(6, 30))
    assert not quiet_now(time(22), time(7), time(12))
