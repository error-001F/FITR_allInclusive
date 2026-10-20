"""Удаление заказов и перенумерация без пропусков — со всем, что ссылается на номер заказа."""
import logging
from collections import defaultdict

from aiogram import Bot
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy.ext.asyncio import AsyncSession

from bot.catalog import REJECTED_ORDER_TTL
from bot.config import Settings
from bot.services.history import log_event
from bot.services.notifications import DELIVERY_ERRORS, close_admin_cards, refresh_admin_cards
from bot.services.orders import delete_order, find_expired_rejected_orders, get_order, renumber_orders

logger = logging.getLogger(__name__)


async def reset_admin_inputs(
    bot: Bot,
    storage: BaseStorage,
    settings: Settings,
    affected_ids: set[int],
) -> None:
    """Сбрасывает незаконченный ввод мастера (сумма, причина, параметры), если он относится
    к удалённому или перенумерованному заказу — иначе ввод применился бы к чужому заказу."""
    for admin_id in settings.admin_ids:
        key = StorageKey(bot_id=bot.id, chat_id=admin_id, user_id=admin_id)
        data = await storage.get_data(key)
        if data.get("order_id") not in affected_ids:
            continue
        await storage.set_state(key, None)
        await storage.set_data(key, {})
        try:
            await bot.send_message(
                admin_id,
                f"⚠️ Номера заказов обновились, действие по заказу №{data['order_id']} "
                "прервано. Откройте нужный заказ через /orders и начните заново.",
            )
        except DELIVERY_ERRORS as error:
            logger.info("Мастер %s не получил сообщение о сбросе ввода: %s", admin_id, error)


async def renumber_after_delete(
    bot: Bot,
    session: AsyncSession,
    storage: BaseStorage,
    settings: Settings,
    deleted_ids: set[int],
) -> dict[int, int]:
    """Шаг сразу после удаления: перенумерация и обновление всего, что зависит от номеров."""
    mapping = await renumber_orders(session)
    # Ввод мастера мог относиться и к удалённому заказу, и к сдвинутому.
    await reset_admin_inputs(bot, storage, settings, deleted_ids | set(mapping))
    if not mapping:
        return mapping

    changes_by_client: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for old, new in mapping.items():
        order = await get_order(session, new)
        log_event(order, f"номер изменён: №{old} → №{new}")
        await refresh_admin_cards(bot, session, order)
        changes_by_client[order.user_id].append((old, new))

    # Одному клиенту — одно сообщение, даже если сдвинулось несколько его заказов.
    for user_id, changes in changes_by_client.items():
        lines = "\n".join(f"№{old} → №{new}" for old, new in changes)
        try:
            await bot.send_message(
                user_id,
                f"Номера ваших заказов обновились:\n{lines}\n\n"
                "Актуальный список — в разделе «📦 Мои заказы».",
            )
        except DELIVERY_ERRORS as error:
            logger.info("Клиент %s не получил новые номера заказов: %s", user_id, error)

    logger.info("Заказы перенумерованы: %s", mapping)
    return mapping


async def cleanup_rejected(
    bot: Bot, session: AsyncSession, storage: BaseStorage, settings: Settings
) -> int:
    """Удаляет заказы, отклонённые больше суток назад, и перенумеровывает оставшиеся.

    Перенумерация вызывается и без удалений: если в номерах уже есть пропуски
    (например, после обновления бота), они закроются при первом запуске.
    """
    expired = await find_expired_rejected_orders(session, REJECTED_ORDER_TTL)
    for order in expired:
        log_event(order, "удалён: прошли сутки после отказа")
        await close_admin_cards(
            bot, session, order, f"🗑 Заказ №{order.id} удалён: прошли сутки после отказа."
        )
        await delete_order(session, order.id)
    await renumber_after_delete(bot, session, storage, settings, {order.id for order in expired})
    return len(expired)
