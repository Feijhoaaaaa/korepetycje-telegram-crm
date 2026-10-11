from __future__ import annotations

import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.web.api import create_web_app
from app.web.auth import validate_init_data
from app.web.security import RateLimiter

TOKEN = "123456:testing"


def signed_init_data(
    user_id: int | None,
    *,
    auth_date: int | None = None,
    user: dict | None = None,
) -> str:
    values = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAExample",
        "user": json.dumps(
            user if user is not None else {"id": user_id, "first_name": "Teacher"}, separators=(",", ":")
        ),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def settings() -> Settings:
    return Settings(
        bot_token=SecretStr(TOKEN),
        owner_telegram_id=42,
        database_url=SecretStr("postgresql+asyncpg://user:pass@localhost/test"),
    )


class EmptySession:
    async def scalar(self, _query):
        return None

    async def rollback(self):
        return None


class TeacherSession:
    async def scalar(self, _query):
        return type("TeacherAccount", (), {"id": 7, "is_admin": False})()


@asynccontextmanager
async def empty_session_factory():
    yield EmptySession()


def test_signed_init_data_is_accepted() -> None:
    user = validate_init_data(signed_init_data(42), bot_token=TOKEN, max_age_seconds=300)
    assert user.id == 42
    assert user.first_name == "Teacher"


@pytest.mark.parametrize("mutation", ["tampered", "expired", "future", "duplicate", "invalid_user"])
def test_invalid_init_data_is_rejected(mutation: str) -> None:
    auth_date = int(time.time()) - 301 if mutation == "expired" else None
    if mutation == "future":
        auth_date = int(time.time()) + 31
    data = signed_init_data(42, auth_date=auth_date, user={} if mutation == "invalid_user" else None)
    if mutation == "tampered":
        data = data.replace("Teacher", "Other")
    elif mutation == "duplicate":
        data += "&auth_date=1"
    with pytest.raises(ValueError):
        validate_init_data(data, bot_token=TOKEN, max_age_seconds=300)


@pytest.mark.asyncio
async def test_api_distinguishes_missing_signature_and_foreign_user() -> None:
    app = create_web_app(settings())
    app.state.session_factory = empty_session_factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/bootstrap")).status_code == 401
        response = await client.get(
            "/api/v1/bootstrap",
            headers={"X-Telegram-Init-Data": signed_init_data(99)},
        )
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "telegram.org" in response.headers["content-security-policy"]
    await app.state.engine.dispose()


@pytest.mark.asyncio
async def test_api_rejects_oversized_json_before_processing() -> None:
    app = create_web_app(settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/v1/settings/quiet-hours",
            headers={"X-Telegram-Init-Data": signed_init_data(42)},
            json={"start": "22:00", "padding": "x" * (64 * 1024)},
        )
    assert response.status_code == 413
    assert response.json()["detail"] == "request_too_large"
    await app.state.engine.dispose()


def test_rate_limiter_returns_retry_after() -> None:
    limiter = RateLimiter()
    assert limiter.retry_after("client", "read", 2) is None
    assert limiter.retry_after("client", "read", 2) is None
    assert limiter.retry_after("client", "read", 2) >= 1


@pytest.mark.asyncio
async def test_bootstrap_accepts_owner() -> None:
    app = create_web_app(settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/bootstrap",
            headers={"X-Telegram-Init-Data": signed_init_data(42)},
        )
        assert response.status_code == 200
        assert response.json()["timezone"] == "Europe/Warsaw"
    await app.state.engine.dispose()


@pytest.mark.asyncio
async def test_actor_lookup_runs_once_per_request() -> None:
    app = create_web_app(settings())
    calls = 0

    @asynccontextmanager
    async def teacher_session_factory():
        nonlocal calls
        calls += 1
        yield TeacherSession()

    app.state.session_factory = teacher_session_factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/bootstrap",
            headers={"X-Telegram-Init-Data": signed_init_data(99)},
        )
    assert response.status_code == 200
    assert response.json()["role"] == "teacher"
    assert calls == 1
    await app.state.engine.dispose()


@pytest.mark.asyncio
async def test_owner_specific_endpoint_fails_closed_without_account() -> None:
    app = create_web_app(settings())
    app.state.session_factory = empty_session_factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/availability",
            headers={"X-Telegram-Init-Data": signed_init_data(42)},
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "account_not_found"
    await app.state.engine.dispose()
