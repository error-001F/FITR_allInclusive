"""Отправка сообщений мастеру и клиенту с обработкой ошибок Telegram."""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.config import Settings
from bot.database.models import Order
from bot.keyboards.admin import admin_order_kb
from bot.services.formatting import admin_card_text

logger = logging.getLogger(__name__)

# Ошибки «получатель недоступен»: заблокировал бота, ни разу не нажимал /start, неверный id.
DELIVERY_ERRORS = (TelegramForbiddenError, TelegramBadRequest)


async def send_order_files(bot: Bot, chat_id: int, order: Order) -> None:
    """Пересылает файлы заказа по file_id — без скачивания и без лимита 20 МБ."""
    caption = f"Заказ №{order.id}"
    if order.file_id:
        await bot.send_document(chat_id, order.file_id, caption=f"{caption}: файл модели")
    for item in order.attachments or []:
        if item.get("kind") == "photo":
            await bot.send_photo(chat_id, item["file_id"], caption=caption)
        else:
            await bot.send_document(chat_id, item["file_id"], caption=caption)


async def send_admin_card(bot: Bot, chat_id: int, order: Order, title: str = "") -> None:
    text = f"{title}\n\n{admin_card_text(order)}" if title else admin_card_text(order)
    await bot.send_message(chat_id, text, reply_markup=admin_order_kb(order))
    await send_order_files(bot, chat_id, order)


async def notify_admin_new_order(bot: Bot, settings: Settings, order: Order) -> None:
    """Уведомление мастеру о новом заказе. Ошибки не должны ломать оформление у клиента."""
    if settings.admin_id is None:
        logger.warning("ADMIN_ID не задан в .env — мастер не узнает о заказе №%s", order.id)
        return
    try:
        await send_admin_card(bot, settings.admin_id, order, title="🆕 Новый заказ!")
    except DELIVERY_ERRORS as error:
        logger.warning(
            "Не удалось уведомить мастера о заказе №%s: %s. Мастер нажимал /start у бота?",
            order.id,
            error,
        )


async def notify_client(bot: Bot, order: Order, text: str) -> bool:
    """Сообщение клиенту. False — не доставлено (например, клиент заблокировал бота)."""
    try:
        # В личных чатах id чата совпадает с id пользователя.
        await bot.send_message(order.user_id, text)
    except DELIVERY_ERRORS as error:
        logger.warning("Не удалось написать клиенту по заказу №%s: %s", order.id, error)
        return False
    return True
