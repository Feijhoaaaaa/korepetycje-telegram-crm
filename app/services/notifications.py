from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)


async def notify_chat(bot: Bot | None, chat_id: int, text: str) -> list[str]:
    if bot is None:
        return ["telegram_not_configured"]
    try:
        await bot.send_message(chat_id, text, disable_notification=True)
    except (TelegramAPIError, OSError) as exc:
        logger.warning("Telegram notification to %s failed: %s", chat_id, exc)
        return ["telegram_notification_failed"]
    return []


async def notify_private(
    bot: Bot | None,
    telegram_user_id: int | None,
    text: str,
    *,
    silent: bool = False,
) -> list[str]:
    if telegram_user_id is None:
        return ["recipient_not_linked"]
    if bot is None:
        return ["telegram_not_configured"]
    try:
        await bot.send_message(telegram_user_id, text, disable_notification=silent)
    except (TelegramAPIError, OSError) as exc:
        logger.warning("Telegram notification to %s failed: %s", telegram_user_id, exc)
        return ["telegram_notification_failed"]
    return []
