"""Отправка сообщений мастерам и клиенту с обработкой ошибок Telegram."""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.database.models import Order
from bot.keyboards.admin import admin_order_kb
from bot.services.formatting import admin_card_text
from bot.services.orders import list_admin_cards, save_admin_card

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


async def send_admin_card(
    bot: Bot, session: AsyncSession, chat_id: int, order: Order, title: str = ""
) -> None:
    """Карточка + файлы мастеру. Карточку запоминаем, чтобы потом обновлять её у всех."""
    text = f"{title}\n\n{admin_card_text(order)}" if title else admin_card_text(order)
    card = await bot.send_message(chat_id, text, reply_markup=admin_order_kb(order))
    await save_admin_card(session, order.id, card.chat.id, card.message_id)
    await send_order_files(bot, chat_id, order)


async def notify_admins_new_order(
    bot: Bot, session: AsyncSession, settings: Settings, order: Order
) -> None:
    """Уведомление всем мастерам. Ошибки не должны ломать оформление у клиента."""
    if not settings.admin_ids:
        logger.warning("ADMIN_IDS не задан в .env — мастера не узнают о заказе №%s", order.id)
        return
    for admin_id in settings.admin_ids:
        # Ошибка у одного мастера не мешает остальным получить карточку.
        try:
            await send_admin_card(bot, session, admin_id, order, title="🆕 Новый заказ!")
        except DELIVERY_ERRORS as error:
            logger.warning(
                "Мастер %s не получил заказ №%s: %s. Он нажимал /start у бота?",
                admin_id,
                order.id,
                error,
            )


async def refresh_admin_cards(
    bot: Bot,
    session: AsyncSession,
    order: Order,
    pressed_card: tuple[int, int] | None = None,
) -> None:
    """Перерисовывает карточку заказа у всех мастеров: новый статус и кнопки.

    pressed_card — (chat_id, message_id) карточки, на которой нажали кнопку. Её обновляем,
    даже если она не записана в БД (карточки, отправленные до появления учёта).
    """
    cards = {(card.chat_id, card.message_id) for card in await list_admin_cards(session, order.id)}
    if pressed_card is not None:
        cards.add(pressed_card)
    for chat_id, message_id in cards:
        try:
            await bot.edit_message_text(
                text=admin_card_text(order),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=admin_order_kb(order),
            )
        except DELIVERY_ERRORS as error:
            # «message is not modified», карточку удалили, мастер заблокировал бота —
            # не критично, остальные карточки всё равно обновляем.
            logger.info("Карточка заказа №%s у %s не обновлена: %s", order.id, chat_id, error)


async def notify_client(bot: Bot, order: Order, text: str) -> bool:
    """Сообщение клиенту. False — не доставлено (например, клиент заблокировал бота)."""
    try:
        # В личных чатах id чата совпадает с id пользователя.
        await bot.send_message(order.user_id, text)
    except DELIVERY_ERRORS as error:
        logger.warning("Не удалось написать клиенту по заказу №%s: %s", order.id, error)
        return False
    return True
