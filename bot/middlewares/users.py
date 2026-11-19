import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from bot.services.broadcast import remember_user

logger = logging.getLogger(__name__)


class TrackUsersMiddleware(BaseMiddleware):
    """Запоминает каждого, кто заходил в бота (сообщение, кнопка), — для рассылки /broadcast."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is not None and not user.is_bot and user.id not in data["settings"].admin_ids:
            try:
                await remember_user(data["session"], user.id)
            except Exception:
                # Сбой учёта не должен мешать обработке сообщения.
                await data["session"].rollback()
                logger.exception("Не удалось запомнить пользователя для рассылки")
        return await handler(event, data)
