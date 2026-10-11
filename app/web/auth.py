from __future__ import annotations

import hashlib
import hmac
import json
import time
from asyncio import timeout
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, Request, status
from sqlalchemy import select

from app.config import Settings
from app.database.models import Student, Teacher


@dataclass(frozen=True, slots=True)
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str | None = None


@dataclass(frozen=True, slots=True)
class Actor:
    user: TelegramUser
    role: str
    teacher_id: int | None = None
    student_id: int | None = None
    is_admin: bool = False

    @property
    def capabilities(self) -> list[str]:
        if self.is_admin:
            return ["admin", "manage_teachers", "manage_all", "manage_payments", "export"]
        if self.role == "teacher":
            return ["manage_own", "manage_payments", "availability", "export"]
        return ["view_own", "submit_payment", "request_reschedule", "availability"]


def validate_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int,
    now: int | None = None,
) -> TelegramUser:
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        if len(pairs) != len(dict(pairs)):
            raise ValueError("duplicate_fields")
        values = dict(pairs)
        received_hash = values.pop("hash")
        auth_date = int(values["auth_date"])
        raw_user = json.loads(values["user"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_init_data") from exc
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, expected):
        raise ValueError("invalid_signature")
    current = int(time.time()) if now is None else now
    if auth_date > current + 30 or current - auth_date > max_age_seconds:
        raise ValueError("expired_init_data")
    try:
        return TelegramUser(
            id=int(raw_user["id"]),
            first_name=str(raw_user.get("first_name", "")),
            last_name=str(raw_user.get("last_name", "")),
            username=raw_user.get("username"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_user") from exc


async def require_actor(
    request: Request,
    init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> Actor:
    cached = getattr(request.state, "actor", None)
    if cached is not None:
        return cached
    settings: Settings = request.app.state.settings
    if not init_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_init_data")
    try:
        user = validate_init_data(
            init_data,
            bot_token=settings.bot_token.get_secret_value(),
            max_age_seconds=settings.init_data_max_age_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if user.id == settings.owner_telegram_id:
        request.state.actor = Actor(user=user, role="admin", is_admin=True)
        return request.state.actor
    factory = request.app.state.session_factory
    try:
        async with timeout(2), factory() as session:
            teacher = await session.scalar(
                select(Teacher).where(Teacher.telegram_user_id == user.id, Teacher.active.is_(True))
            )
            if teacher:
                request.state.actor = Actor(
                    user=user,
                    role="admin" if teacher.is_admin else "teacher",
                    teacher_id=teacher.id,
                    is_admin=teacher.is_admin,
                )
                return request.state.actor
            student = await session.scalar(
                select(Student).where(Student.telegram_user_id == user.id, Student.active.is_(True))
            )
            if student:
                request.state.actor = Actor(user=user, role="student", student_id=student.id)
                return request.state.actor
    except (TimeoutError, OSError):
        pass
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_not_linked")


async def require_owner(
    request: Request, init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")
) -> TelegramUser:
    """Compatibility dependency for callers that still explicitly require the administrator."""
    actor = await require_actor(request, init_data)
    if not actor.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_only")
    return actor.user
