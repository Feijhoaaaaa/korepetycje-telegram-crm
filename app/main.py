from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher

from app.bot.handlers import create_router
from app.bot.middleware import DatabaseMiddleware
from app.config import get_settings
from app.database.session import check_database, create_engine, create_session_factory
from app.services.chats import ChatService


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    engine = create_engine(settings)
    await check_database(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        await ChatService(session).ensure_owner_teacher(settings.owner_telegram_id)
        await session.commit()

    bot = Bot(token=settings.bot_token.get_secret_value())
    dispatcher = Dispatcher()
    middleware = DatabaseMiddleware(factory)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)
    dispatcher.include_router(create_router(settings))

    try:
        await dispatcher.start_polling(bot)
    finally:
        await engine.dispose()


def run() -> None:
    asyncio.run(main())
