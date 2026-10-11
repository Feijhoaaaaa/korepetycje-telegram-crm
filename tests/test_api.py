from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.database.models import Base, PaymentReceipt, ServiceRequest, StudyChat
from app.database.session import check_database
from app.services.chats import ChatService
from app.services.lessons import LessonService
from app.web.api import create_web_app

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")
TOKEN = "123456:testing"


def auth_header(user_id: int = 42) -> dict[str, str]:
    values = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Teacher"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(values)}


@pytest_asyncio.fixture
async def api_client():
    assert TEST_DATABASE_URL and "test" in TEST_DATABASE_URL.lower(), "Refusing non-test database"
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS tutor_bot CASCADE"))
        await connection.execute(text("CREATE SCHEMA tutor_bot"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        chat = await ChatService(session).setup_chat(-10001, 42, "Teacher", "Алгебра")
        await session.commit()
        chat_id = chat.id

    settings = Settings(
        bot_token=SecretStr(TOKEN),
        owner_telegram_id=42,
        database_url=SecretStr(TEST_DATABASE_URL),
    )
    bot = AsyncMock()
    app = create_web_app(settings, engine=engine, bot=bot)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=auth_header()) as client:
        yield client, chat_id, bot
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS tutor_bot CASCADE"))
    await engine.dispose()


@pytest_asyncio.fixture
async def scoped_api_client():
    assert TEST_DATABASE_URL and "test" in TEST_DATABASE_URL.lower(), "Refusing non-test database"
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS tutor_bot CASCADE"))
        await connection.execute(text("CREATE SCHEMA tutor_bot"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        chats = ChatService(session)
        await chats.ensure_teacher(42, "Owner", is_admin=True)
        first_chat = await chats.setup_chat(-20001, 1001, "Teacher One", "Group One")
        second_chat = await chats.setup_chat(-20002, 1002, "Teacher Two", "Group Two")
        await chats.assign_student(first_chat, 2001, "Student One")
        await chats.assign_student(second_chat, 2002, "Student Two")
        lessons = LessonService(session)
        first_lesson = await lessons.create(first_chat.id, datetime(2026, 9, 24, 15, tzinfo=UTC))
        second_lesson = await lessons.create(second_chat.id, datetime(2026, 9, 25, 15, tzinfo=UTC))
        first_receipt = PaymentReceipt(
            student_id=first_chat.student_id,
            teacher_id=first_chat.teacher_id,
            telegram_file_id="first-file",
            telegram_file_type="photo",
            submitted_amount=100,
        )
        second_receipt = PaymentReceipt(
            student_id=second_chat.student_id,
            teacher_id=second_chat.teacher_id,
            telegram_file_id="second-file",
            telegram_file_type="photo",
            submitted_amount=200,
        )
        session.add_all((first_receipt, second_receipt))
        await session.flush()
        first_payment_request = ServiceRequest(
            type="payment_confirmation",
            student_id=first_chat.student_id,
            teacher_id=first_chat.teacher_id,
            payment_id=first_receipt.id,
        )
        second_payment_request = ServiceRequest(
            type="payment_confirmation",
            student_id=second_chat.student_id,
            teacher_id=second_chat.teacher_id,
            payment_id=second_receipt.id,
        )
        first_request = ServiceRequest(
            type="lesson_reschedule",
            lesson_id=first_lesson.id,
            student_id=first_chat.student_id,
            teacher_id=first_chat.teacher_id,
            details={"proposed_datetime": datetime(2026, 9, 26, 15, tzinfo=UTC).isoformat()},
        )
        second_request = ServiceRequest(
            type="lesson_reschedule",
            lesson_id=second_lesson.id,
            student_id=second_chat.student_id,
            teacher_id=second_chat.teacher_id,
            details={"proposed_datetime": datetime(2026, 9, 27, 15, tzinfo=UTC).isoformat()},
        )
        session.add_all((first_payment_request, second_payment_request, first_request, second_request))
        await session.commit()
        ids = {
            "first_chat": first_chat.id,
            "second_chat": second_chat.id,
            "first_student": first_chat.student_id,
            "second_student": second_chat.student_id,
            "first_lesson": first_lesson.id,
            "second_lesson": second_lesson.id,
            "first_receipt": first_receipt.id,
            "second_receipt": second_receipt.id,
            "first_request": first_request.id,
            "second_request": second_request.id,
        }

    settings = Settings(
        bot_token=SecretStr(TOKEN),
        owner_telegram_id=42,
        database_url=SecretStr(TEST_DATABASE_URL),
    )
    app = create_web_app(settings, engine=engine, bot=AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, ids
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS tutor_bot CASCADE"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_complete_mini_app_crud_flow(api_client) -> None:
    client, chat_id, bot = api_client

    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/ready")).json() == {"status": "ready"}
    html = await client.get("/")
    assert html.status_code == 200
    assert html.headers["cache-control"] == "no-cache"

    students = (await client.get("/api/v1/students")).json()
    assert [item["id"] for item in students["pending"]] == [chat_id]
    concurrent_assignments = await asyncio.gather(
        client.patch(f"/api/v1/students/{chat_id}", json={"name": "Анна"}),
        client.patch(f"/api/v1/students/{chat_id}", json={"name": "Анна"}),
    )
    assigned, repeated = concurrent_assignments
    assert assigned.status_code == repeated.status_code == 200
    assert assigned.json()["student"]["student_name"] == "Анна"
    sent_after_assignment = bot.send_message.await_count
    repeated_assignment = await client.patch(f"/api/v1/students/{chat_id}", json={"name": "Анна"})
    assert repeated_assignment.status_code == 200
    assert bot.send_message.await_count == sent_after_assignment

    payload = {"study_chat_id": chat_id, "date": "2026-09-24", "time": "17:00", "force": False}
    created = await client.post("/api/v1/lessons", json=payload)
    assert created.status_code == 201
    first_id = created.json()["lesson"]["id"]

    conflict = await client.post("/api/v1/lessons", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "lesson_conflict"
    payload["force"] = True
    second_id = (await client.post("/api/v1/lessons", json=payload)).json()["lesson"]["id"]

    moved = await client.patch(
        f"/api/v1/lessons/{second_id}/reschedule",
        json={"date": "2026-09-25", "time": "18:30", "force": False},
    )
    assert moved.json()["lesson"]["time"] == "18:30"

    completed = await client.post(f"/api/v1/lessons/{second_id}/complete")
    assert completed.json()["lesson"]["status"] == "completed"
    unpaid = await client.patch(f"/api/v1/lessons/{second_id}/payment", json={"payment_status": "unpaid"})
    assert unpaid.json()["lesson"]["payment_status"] == "unpaid"
    assert (await client.get("/api/v1/payments/unpaid")).json()["total"] == 1

    cancelled = await client.post(f"/api/v1/lessons/{first_id}/cancel")
    assert cancelled.json()["lesson"]["status"] == "cancelled"
    removed = await client.delete(f"/api/v1/lessons/{second_id}")
    assert removed.status_code == 200
    assert (await client.get(f"/api/v1/lessons/{second_id}")).status_code == 404

    calendar = await client.get("/api/v1/calendar?start=2026-09-21&end=2026-09-28")
    assert [item["id"] for item in calendar.json()["lessons"]] == [first_id]
    export = await client.get("/api/v1/exports.xlsx?month=2026-09")
    assert export.status_code == 200
    assert export.content.startswith(b"PK")
    assert bot.send_message.await_count >= 6

    availability = await client.put(
        "/api/v1/availability",
        json={
            "rules": [
                {"weekday": 3, "start_time": "10:00", "end_time": "12:00"},
                {"weekday": 3, "start_time": "16:00", "end_time": "20:00"},
            ]
        },
    )
    assert availability.status_code == 200
    assert len([row for row in availability.json()["rules"] if row["weekday"] == 3]) == 2
    exception = await client.post(
        "/api/v1/availability/exceptions",
        json={"date": "2026-09-24", "available": False},
    )
    assert exception.status_code == 200
    removed_exception = await client.delete("/api/v1/availability/exceptions/2026-09-24")
    assert removed_exception.status_code == 200

    student_id = assigned.json()["student"]["student_id"]
    archived = await client.post(f"/api/v1/admin/students/{student_id}/archive")
    assert archived.status_code == 200
    archived_rows = (await client.get("/api/v1/students?include_archived=true")).json()["active"]
    assert any(row["student_id"] == student_id and not row["active"] for row in archived_rows)
    restored = await client.post(f"/api/v1/admin/students/{student_id}/restore")
    assert restored.status_code == 200

    historical = await client.post(
        "/api/v1/lessons",
        json={"study_chat_id": chat_id, "date": "2026-09-26", "time": "17:00", "force": True},
    )
    assert historical.status_code == 201
    historical_id = historical.json()["lesson"]["id"]
    deleted = await client.delete(f"/api/v1/admin/students/{student_id}")
    assert deleted.status_code == 200
    assert deleted.json()["lessons_preserved"] is True
    calendar_after_delete = await client.get("/api/v1/calendar?start=2026-09-26&end=2026-09-27")
    preserved = next(row for row in calendar_after_delete.json()["lessons"] if row["id"] == historical_id)
    assert preserved["student_id"] is None
    assert preserved["student_name"] == "Анна"
    export_after_delete = await client.post("/api/v1/exports/send?month=2026-09")
    assert export_after_delete.status_code == 200
    bot.send_document.assert_awaited()


@pytest.mark.asyncio
async def test_orphaned_chat_is_repairable_and_lesson_error_is_actionable(api_client) -> None:
    client, chat_id, _ = api_client
    app = client._transport.app
    async with app.state.session_factory() as session:
        chat = await session.get(StudyChat, chat_id)
        chat.student_id = None
        chat.student_name = "Stale name"
        await session.commit()

    students = (await client.get("/api/v1/students")).json()
    assert any(row["id"] == chat_id for row in students["pending"])
    failed = await client.post(
        "/api/v1/lessons",
        json={"study_chat_id": chat_id, "date": "2026-09-24", "time": "17:00"},
    )
    assert failed.status_code == 409
    assert failed.json()["detail"] == "student_not_assigned"

    assigned = await client.patch(f"/api/v1/students/{chat_id}", json={"name": "Анна"})
    assert assigned.status_code == 200
    created = await client.post(
        "/api/v1/lessons",
        json={"study_chat_id": chat_id, "date": "2026-09-24", "time": "17:00", "force": True},
    )
    assert created.status_code == 201


@pytest.mark.asyncio
async def test_api_isolates_teacher_student_and_admin_scopes(scoped_api_client) -> None:
    client, ids = scoped_api_client
    date_range = "start=2026-09-20&end=2026-09-30"

    teacher_headers = auth_header(1001)
    foreign_lesson = await client.post(
        "/api/v1/lessons",
        headers=teacher_headers,
        json={"study_chat_id": ids["second_chat"], "date": "2026-09-27", "time": "15:00", "force": True},
    )
    assert foreign_lesson.status_code == 404
    assert foreign_lesson.json()["detail"] == "student_not_found"
    teacher_students = (await client.get("/api/v1/students", headers=teacher_headers)).json()["active"]
    assert [row["student_id"] for row in teacher_students] == [ids["first_student"]]
    teacher_receipts = (await client.get("/api/v1/payments/receipts", headers=teacher_headers)).json()["receipts"]
    assert [row["id"] for row in teacher_receipts] == [ids["first_receipt"]]
    assert teacher_receipts[0]["submitted_amount"] == "100.00"
    teacher_requests = (await client.get("/api/v1/requests", headers=teacher_headers)).json()["requests"]
    assert {(row["type"], row["student_id"]) for row in teacher_requests} == {
        ("payment_confirmation", ids["first_student"]),
        ("lesson_reschedule", ids["first_student"]),
    }
    assert (await client.get(f"/api/v1/students/{ids['second_chat']}", headers=teacher_headers)).status_code == 404
    assert (await client.get(f"/api/v1/lessons/{ids['second_lesson']}", headers=teacher_headers)).status_code == 404
    assert (
        await client.post(f"/api/v1/lessons/{ids['second_lesson']}/cancel", headers=teacher_headers)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/payments/receipts/{ids['second_receipt']}/file", headers=teacher_headers)
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/exports.xlsx?month=2099-01&study_chat_id={ids['second_chat']}",
            headers=teacher_headers,
        )
    ).status_code == 404
    own_empty_export = await client.get(
        f"/api/v1/exports.xlsx?month=2099-01&study_chat_id={ids['first_chat']}",
        headers=teacher_headers,
    )
    assert own_empty_export.status_code == 200
    assert own_empty_export.content.startswith(b"PK")
    forbidden_reassignment = await client.patch(
        f"/api/v1/lessons/{ids['first_lesson']}",
        headers=teacher_headers,
        json={"student_id": ids["second_student"]},
    )
    assert forbidden_reassignment.status_code == 403
    assert (
        await client.get(
            f"/api/v1/availability/common?day=2026-09-24&student_id={ids['second_student']}",
            headers=teacher_headers,
        )
    ).status_code == 404
    assert (await client.get("/api/v1/admin/teachers", headers=teacher_headers)).status_code == 403
    own_availability = await client.put(
        "/api/v1/availability",
        headers=teacher_headers,
        json={"rules": [{"weekday": 3, "start_time": "10:00", "end_time": "12:00"}]},
    )
    assert own_availability.status_code == 200

    student_headers = auth_header(2001)
    student_calendar = (await client.get(f"/api/v1/calendar?{date_range}", headers=student_headers)).json()["lessons"]
    assert [row["id"] for row in student_calendar] == [ids["first_lesson"]]
    student_receipts = (await client.get("/api/v1/payments/receipts", headers=student_headers)).json()["receipts"]
    assert [row["id"] for row in student_receipts] == [ids["first_receipt"]]
    student_requests = (await client.get("/api/v1/reschedule-requests", headers=student_headers)).json()["requests"]
    assert [row["lesson_id"] for row in student_requests] == [ids["first_lesson"]]
    unified_student_requests = (await client.get("/api/v1/requests", headers=student_headers)).json()["requests"]
    assert {row["type"] for row in unified_student_requests} == {"payment_confirmation", "lesson_reschedule"}
    assert (await client.get(f"/api/v1/lessons/{ids['second_lesson']}", headers=student_headers)).status_code == 404
    assert (
        await client.post(f"/api/v1/lessons/{ids['first_lesson']}/cancel", headers=student_headers)
    ).status_code == 403
    assert (await client.get("/api/v1/admin/teachers", headers=student_headers)).status_code == 403

    admin_headers = auth_header(42)
    inconsistent_reassignment = await client.patch(
        f"/api/v1/lessons/{ids['first_lesson']}",
        headers=admin_headers,
        json={"student_id": ids["second_student"], "study_chat_id": ids["first_chat"]},
    )
    assert inconsistent_reassignment.status_code == 409
    assert len((await client.get("/api/v1/students", headers=admin_headers)).json()["active"]) == 2
    assert len((await client.get(f"/api/v1/calendar?{date_range}", headers=admin_headers)).json()["lessons"]) == 2
    assert len((await client.get("/api/v1/payments/receipts", headers=admin_headers)).json()["receipts"]) == 2
    assert len((await client.get("/api/v1/reschedule-requests", headers=admin_headers)).json()["requests"]) == 2
    assert len((await client.get("/api/v1/requests", headers=admin_headers)).json()["requests"]) == 4
    pending_payments = (
        await client.get(
            "/api/v1/requests?type=payment_confirmation&status=pending", headers=admin_headers
        )
    ).json()["requests"]
    assert {row["payment_id"] for row in pending_payments} == {ids["first_receipt"], ids["second_receipt"]}

    decisions = await asyncio.gather(
        client.post(
            f"/api/v1/reschedule-requests/{ids['first_request']}/accept",
            headers=teacher_headers,
            json={"reason": "", "force": True},
        ),
        client.post(
            f"/api/v1/reschedule-requests/{ids['first_request']}/accept",
            headers=teacher_headers,
            json={"reason": "", "force": True},
        ),
    )
    assert [response.status_code for response in decisions] == [200, 200]
    assert {response.json()["status"] for response in decisions} == {"approved"}


@pytest.mark.asyncio
async def test_time_proposal_counter_confirmation_and_cancellation(scoped_api_client) -> None:
    client, ids = scoped_api_client
    student_headers = auth_header(2001)
    teacher_headers = auth_header(1001)

    proposal = await client.post(
        f"/api/v1/lessons/{ids['first_lesson']}/time-proposals",
        headers=student_headers,
        json={"date": "2026-10-01", "time": "16:00", "duration_minutes": 60, "reason": ""},
    )
    assert proposal.status_code == 201
    proposal_id = proposal.json()["id"]

    counter = await client.post(
        f"/api/v1/requests/{proposal_id}/counter",
        headers=teacher_headers,
        json={"date": "2026-10-02", "time": "17:00", "reason": ""},
    )
    assert counter.status_code == 200
    assert counter.json()["phase"] == "student_confirmation"
    calendar = await client.get(
        "/api/v1/calendar?start=2026-09-24&end=2026-10-03", headers=student_headers
    )
    current = next(row for row in calendar.json()["lessons"] if row["id"] == ids["first_lesson"])
    assert current["date"] == "2026-09-24"

    confirmed = await client.post(
        f"/api/v1/requests/{proposal_id}/confirm", headers=student_headers, json={"force": True}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "approved"

    cancellation = await client.post(
        f"/api/v1/lessons/{ids['first_lesson']}/cancellation-requests",
        headers=student_headers,
        json={"reason": ""},
    )
    assert cancellation.status_code == 201
    cancellation_id = cancellation.json()["id"]
    approved = await client.post(
        f"/api/v1/requests/{cancellation_id}/approve", headers=teacher_headers, json={"force": True}
    )
    assert approved.status_code == 200
    calendar = await client.get(
        "/api/v1/calendar?start=2026-09-24&end=2026-10-03", headers=student_headers
    )
    lesson = next(row for row in calendar.json()["lessons"] if row["id"] == ids["first_lesson"])
    assert lesson["status"] == "cancelled"


@pytest.mark.asyncio
async def test_migrations_keep_private_schema_behind_rls() -> None:
    assert TEST_DATABASE_URL and "test" in TEST_DATABASE_URL.lower(), "Refusing non-test database"
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS tutor_bot CASCADE"))
    await engine.dispose()

    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "BOT_TOKEN": TOKEN,
        "OWNER_TELEGRAM_ID": "42",
        "DATABASE_URL": TEST_DATABASE_URL,
        "MIGRATION_DATABASE_URL": TEST_DATABASE_URL,
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0008_payment_submission_details"],
        cwd=root,
        env=env,
        check=True,
    )
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tutor_bot.teachers (telegram_user_id, name) VALUES (9001, 'Teacher')")
        )
        await connection.execute(
            text(
                "INSERT INTO tutor_bot.students (teacher_id, telegram_user_id, name) "
                "VALUES (1, 9002, 'Student')"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO tutor_bot.lessons (teacher_id, student_id, lesson_datetime) "
                "VALUES (1, 1, '2026-09-24 15:00:00+00')"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO tutor_bot.payment_receipts ("
                "student_id, teacher_id, telegram_file_id, telegram_file_type, status, submitted_amount, amount, "
                "reviewed_by_teacher_id, reviewed_at"
                ") VALUES (1, 1, 'legacy-file', 'photo', 'approved', 150, 150, 1, now())"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO tutor_bot.reschedule_requests ("
                "lesson_id, student_id, proposed_datetime, status, reason, reviewed_by_teacher_id, reviewed_at"
                ") VALUES (1, 1, '2026-09-25 15:00:00+00', 'rejected', 'Legacy reason', 1, now())"
            )
        )
    await engine.dispose()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, env=env, check=True)

    engine = create_async_engine(TEST_DATABASE_URL)
    await check_database(engine)
    async with engine.begin() as connection:
        tables = (
            await connection.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'tutor_bot' AND c.relkind = 'r'"
                )
            )
        ).all()
        client_access = await connection.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) acl "
                "WHERE n.nspname = 'tutor_bot' AND acl.privilege_type = 'USAGE' "
                "AND (acl.grantee = 0 OR acl.grantee IN ("
                "SELECT oid FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
                "))"
                ")"
            )
        )
        payment_columns = set(
            await connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'tutor_bot' AND table_name = 'payment_receipts' "
                    "AND column_name IN ('submitted_amount', 'teacher_chat_id', 'teacher_message_id', "
                    "'status', 'rejection_reason', 'reviewed_at')"
                )
            )
        )
        submitted_constraint = await connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conname = 'payment_receipt_submitted_amount_check' "
                "AND conrelid = 'tutor_bot.payment_receipts'::regclass)"
            )
        )
        migrated_requests = (
            await connection.execute(
                text(
                    "SELECT type, status, payment_id, lesson_id, comment "
                    "FROM tutor_bot.service_requests ORDER BY type"
                )
            )
        ).all()
    await engine.dispose()

    expected_tables = {table.name for table in Base.metadata.tables.values()} | {"alembic_version"}
    assert {name for name, _ in tables} == expected_tables
    assert all(rls_enabled for _, rls_enabled in tables)
    assert client_access is False
    assert payment_columns == {"submitted_amount", "teacher_chat_id", "teacher_message_id"}
    assert submitted_constraint is True
    assert migrated_requests == [
        ("lesson_reschedule", "rejected", None, 1, "Legacy reason"),
        ("payment_confirmation", "approved", 1, None, None),
    ]

    subprocess.run(
        [str(root / ".venv/bin/alembic"), "downgrade", "0007_idempotency_guards"],
        cwd=root,
        env=env,
        check=True,
    )
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        remaining_columns = set(
            await connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'tutor_bot' AND table_name = 'payment_receipts' "
                    "AND column_name IN ('submitted_amount', 'teacher_chat_id', 'teacher_message_id')"
                )
            )
        )
        legacy_payment_status = await connection.scalar(
            text("SELECT status FROM tutor_bot.payment_receipts WHERE telegram_file_id = 'legacy-file'")
        )
        legacy_reschedule_status = await connection.scalar(
            text("SELECT status FROM tutor_bot.reschedule_requests WHERE lesson_id = 1")
        )
        await connection.execute(text("DROP SCHEMA tutor_bot CASCADE"))
    await engine.dispose()
    assert remaining_columns == set()
    assert legacy_payment_status == "approved"
    assert legacy_reschedule_status == "rejected"
