import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import SimpleEventIsolation
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.catalog import BOT_DESCRIPTION, CLEANUP_INTERVAL
from bot.config import Settings, get_settings
from bot.database.engine import create_engine, create_session_pool, init_db
from bot.handlers import get_routers
from bot.middlewares.db import DbSessionMiddleware
from bot.services.history import setup_history
from bot.services.maintenance import cleanup_rejected
from bot.services.promo import check_promo_codes


logger = logging.getLogger(__name__)


async def cleanup_rejected_orders(
    bot: Bot,
    dp: Dispatcher,
    settings: Settings,
    session_pool: async_sessionmaker[AsyncSession],
) -> None:
    """Фоновая задача: при старте и раз в CLEANUP_INTERVAL удаляет давно отклонённые
    заказы и перенумеровывает оставшиеся."""
    while True:
        try:
            async with session_pool() as session:
                deleted = await cleanup_rejected(bot, session, dp.fsm.storage, settings)
            if deleted:
                logger.info("Удалено отклонённых заказов: %s", deleted)
        except Exception:
            # Ошибка очистки не должна останавливать ни бота, ни саму задачу — попробуем позже.
            logger.exception("Не удалось удалить отклонённые заказы")
        await asyncio.sleep(CLEANUP_INTERVAL.total_seconds())


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
    # Журнал истории — рядом с базой: data/orders_history.log (папка data/ не попадает в git).
    setup_history(settings.database_path.parent / "orders_history.log")

    engine = create_engine(settings.database_path)
    await init_db(engine)
    check_promo_codes()

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        # Telegram просто перезаписывает описание, поэтому выставляем его при каждом запуске.
        await bot.set_my_description(BOT_DESCRIPTION)
    except TelegramAPIError as error:
        # Без описания бот работает, поэтому сбой не мешает запуску.
        logger.warning("Не удалось обновить описание бота: %s", error)

    session_pool = create_session_pool(engine)
    dp = create_dispatcher(settings, session_pool)
    cleanup_task = asyncio.create_task(cleanup_rejected_orders(bot, dp, settings, session_pool))

    try:
        await dp.start_polling(bot)
    finally:
        cleanup_task.cancel()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
