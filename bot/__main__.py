import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import SimpleEventIsolation
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings, get_settings
from bot.database.engine import create_engine, create_session_pool, init_db
from bot.handlers import get_routers
from bot.middlewares.db import DbSessionMiddleware


def create_dispatcher(
    settings: Settings, session_pool: async_sessionmaker[AsyncSession]
) -> Dispatcher:
    # settings попадают в хендлеры как аргумент `settings`.
    # events_isolation: апдейты одного пользователя обрабатываются по очереди, а не
    # параллельно. Иначе фото из альбома (приходят отдельными апдейтами одновременно)
    # перезаписывали бы друг друга в данных FSM.
    dp = Dispatcher(settings=settings, events_isolation=SimpleEventIsolation())
    dp.update.middleware(DbSessionMiddleware(session_pool))
    dp.include_routers(*get_routers())
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()

    engine = create_engine(settings.database_path)
    await init_db(engine)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher(settings, create_session_pool(engine))

    try:
        await dp.start_polling(bot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
