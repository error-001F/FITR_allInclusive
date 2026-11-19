"""Рассылка всем, кто заходил в бота: список получателей и отправка в фоне."""
import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.models import BotUser
from bot.services.notifications import DELIVERY_ERRORS

logger = logging.getLogger(__name__)

# ~20 сообщений в секунду: лимит Telegram — около 30 в секунду для разных чатов.
SEND_DELAY = 0.05
# Сколько раз ждать и повторять одному человеку при «слишком много запросов».
MAX_RETRIES = 3

# Кого уже записали в bot_users с момента запуска — чтобы не писать в БД на каждое сообщение.
_known_users: set[int] = set()
# Текущая рассылка; ссылку храним, иначе задачу может собрать сборщик мусора.
_task: asyncio.Task | None = None


async def remember_user(session: AsyncSession, user_id: int) -> None:
    """Добавить пользователя в получатели (или вернуть, если он раньше заблокировал бота)."""
    if user_id in _known_users:
        return
    await session.execute(
        insert(BotUser)
        .values(user_id=user_id, blocked=False)
        .on_conflict_do_update(index_elements=[BotUser.user_id], set_={"blocked": False})
    )
    await session.commit()
    _known_users.add(user_id)


async def list_recipients(session: AsyncSession, admin_ids: list[int]) -> list[int]:
    rows = await session.scalars(
        select(BotUser.user_id).where(BotUser.blocked.is_(False)).order_by(BotUser.first_seen)
    )
    return [user_id for user_id in rows if user_id not in admin_ids]


def broadcast_running() -> bool:
    return _task is not None and not _task.done()


def start_broadcast(
    bot: Bot,
    session_pool: async_sessionmaker[AsyncSession],
    recipients: list[int],
    from_chat_id: int,
    message_id: int,
    report_chat_id: int,
) -> None:
    global _task
    _task = asyncio.create_task(
        run_broadcast(bot, session_pool, recipients, from_chat_id, message_id, report_chat_id)
    )


async def copy_to(bot: Bot, user_id: int, from_chat_id: int, message_id: int) -> None:
    """Копия сообщения мастера без пометки «переслано»; при перегрузке ждём и повторяем."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            await bot.copy_message(user_id, from_chat_id, message_id)
            return
        except TelegramRetryAfter as error:
            if attempt == MAX_RETRIES:
                raise
            await asyncio.sleep(error.retry_after + 1)


async def run_broadcast(
    bot: Bot,
    session_pool: async_sessionmaker[AsyncSession],
    recipients: list[int],
    from_chat_id: int,
    message_id: int,
    report_chat_id: int,
) -> None:
    delivered, blocked, failed = 0, [], 0
    try:
        for user_id in recipients:
            try:
                await copy_to(bot, user_id, from_chat_id, message_id)
                delivered += 1
            except TelegramForbiddenError:
                blocked.append(user_id)
            except (*DELIVERY_ERRORS, TelegramRetryAfter) as error:
                failed += 1
                logger.warning("Рассылка: не доставлено %s: %s", user_id, error)
            await asyncio.sleep(SEND_DELAY)

        if blocked:
            async with session_pool() as session:
                await session.execute(
                    update(BotUser).where(BotUser.user_id.in_(blocked)).values(blocked=True)
                )
                await session.commit()
            # Если такой человек снова напишет боту, middleware вернёт его в получатели.
            _known_users.difference_update(blocked)
    except Exception:
        logger.exception("Рассылка прервана ошибкой")
        await bot.send_message(
            report_chat_id,
            f"⚠️ Рассылка прервана ошибкой. Успели доставить: {delivered}. Подробности — в логе.",
        )
        return

    logger.info(
        "Рассылка завершена: доставлено %s, заблокировали %s, ошибок %s",
        delivered, len(blocked), failed,
    )
    lines = [f"📣 Рассылка завершена.\nДоставлено: {delivered}"]
    if blocked:
        lines.append(f"Заблокировали бота: {len(blocked)} — им больше не пишем")
    if failed:
        lines.append(f"Ошибки доставки: {failed} — подробности в логе")
    await bot.send_message(report_chat_id, "\n".join(lines))
