from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Order, OrderStatus


class AdminOrderCallback(CallbackData, prefix="adm"):
    """Кнопка карточки заказа у мастера, например adm:invoice:5."""

    action: str  # invoice | paid | ready | reject | ask
    order_id: int


def _button(text: str, action: str, order: Order) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=AdminOrderCallback(action=action, order_id=order.id).pack(),
    )


def admin_order_kb(order: Order) -> InlineKeyboardMarkup:
    """Кнопки зависят от статуса: мастер видит только допустимые действия."""
    status = order.status
    if status == OrderStatus.NEW:
        rows = [
            [_button("💳 Выставить счёт", "invoice", order),
             _button("❓ Уточнить детали", "ask", order)],
            [_button("❌ Отклонить", "reject", order)],
        ]
    elif status == OrderStatus.AWAITING_PAYMENT:
        rows = [
            [_button("✅ Оплачен → в печать", "paid", order),
             _button("💳 Изменить сумму", "invoice", order)],
            [_button("❓ Уточнить", "ask", order), _button("❌ Отклонить", "reject", order)],
        ]
    elif status == OrderStatus.PRINTING:
        rows = [
            [_button("📦 Готов к выдаче", "ready", order), _button("❓ Уточнить", "ask", order)],
        ]
    else:
        # ready / rejected — работа закончена, но написать клиенту ещё можно.
        rows = [[_button("❓ Написать клиенту", "ask", order)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)
