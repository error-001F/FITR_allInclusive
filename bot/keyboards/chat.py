from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Order

GENERAL_QUESTION = 0  # order_id для вопроса без привязки к заказу
CHAT_CANCEL_CALLBACK = "chat_cancel"
ORDER_TYPE_SHORT = {"print": "Печать", "design": "Разработка"}


class ChatCallback(CallbackData, prefix="chat"):
    """Кнопки переписки, например chat:master_reply:3:879832039.

    order_id — номер заказа (0 — общий вопрос); user_id — постоянный id клиента или мастера.
    Номер заказа может измениться после перенумерации, поэтому он лишь подпись: доставка
    идёт по user_id, а принадлежность заказа клиенту проверяется в хендлере.
    """

    # pick | client_reply | master_reply | history | write | files
    action: str
    order_id: int
    user_id: int = 0


def _button(text: str, action: str, order_id: int, user_id: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=ChatCallback(action=action, order_id=order_id, user_id=user_id).pack(),
    )


def history_button(order: Order, count: int = 0) -> InlineKeyboardButton:
    """«💬 Уточнить» — история переписки по заказу (в /orders и на карточке)."""
    text = f"💬 Уточнить №{order.id}" + (f" ({count})" if count else "")
    return _button(text, "history", order.id)


def choose_order_kb(orders: list[Order]) -> InlineKeyboardMarkup:
    rows = [
        [_button(f"№{order.id} · {ORDER_TYPE_SHORT.get(order.order_type, '')}", "pick", order.id)]
        for order in orders
    ]
    rows.append([_button("❓ Общий вопрос", "pick", GENERAL_QUESTION)])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=CHAT_CANCEL_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def to_client_kb(order_id: int, master_id: int) -> InlineKeyboardMarkup:
    """Под сообщением мастера у клиента: ответ уйдёт именно этому мастеру."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button("✍️ Ответить", "client_reply", order_id, master_id)]]
    )


def to_master_kb(order_id: int, client_id: int) -> InlineKeyboardMarkup:
    """Под сообщением клиента у мастера."""
    row = [_button("✍️ Ответить", "master_reply", order_id, client_id)]
    if order_id != GENERAL_QUESTION:
        row.append(_button("📜 История", "history", order_id))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def history_kb(order: Order, attachments: int) -> InlineKeyboardMarkup:
    rows = [[_button("✍️ Написать клиенту", "write", order.id, order.user_id)]]
    if attachments:
        rows.append([_button(f"📎 Вложения ({attachments})", "files", order.id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
