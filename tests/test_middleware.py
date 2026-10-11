from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import CallbackQuery, User

from app.bot.middleware import OwnerCallbackMiddleware


@pytest.mark.asyncio
async def test_non_owner_callback_is_rejected() -> None:
    event = CallbackQuery(
        id="callback",
        from_user=User(id=99, is_bot=False, first_name="Student"),
        chat_instance="instance",
        data="m:today:0",
    )
    handler = AsyncMock()

    with patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer:
        result = await OwnerCallbackMiddleware(42)(handler, event, {})

    assert result is None
    handler.assert_not_awaited()
    answer.assert_awaited_once_with("Нет доступа", show_alert=True)


@pytest.mark.asyncio
async def test_owner_callback_reaches_handler() -> None:
    event = CallbackQuery(
        id="callback",
        from_user=User(id=42, is_bot=False, first_name="Teacher"),
        chat_instance="instance",
        data="m:today:0",
    )
    handler = AsyncMock(return_value="ok")

    assert await OwnerCallbackMiddleware(42)(handler, event, {}) == "ok"
    handler.assert_awaited_once_with(event, {})
