from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Order, OrderStatus

ADMIN_CANCEL_CALLBACK = "adm_cancel"
ADMIN_SKIP_FILE_CALLBACK = "adm_skip_file"


class AdminOrderCallback(CallbackData, prefix="adm"):
    """Кнопка карточки заказа у мастера, например adm:invoice:5."""

    # invoice | paid | ready | reject | ask | params | issue | issue_confirm | issue_back | open
    action: str
    order_id: int


class AdminPrintCallback(CallbackData, prefix="admprint"):
    """Выбор параметра печати мастером, например admprint:material:PETG.

    Отдельный префикс (а не клиентский print:…): иначе кнопки мастера перехватил бы
    клиентский обработчик устаревших кнопок в handlers/orders.py.
    """

    field: str
    value: str


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
            [_button("🛠 Параметры печати", "params", order),
             _button("❌ Отклонить", "reject", order)],
        ]
    elif status == OrderStatus.AWAITING_PAYMENT:
        rows = [
            [_button("✅ Оплачен → в печать", "paid", order),
             _button("💳 Изменить сумму", "invoice", order)],
            [_button("🛠 Параметры печати", "params", order),
             _button("❓ Уточнить", "ask", order)],
            [_button("❌ Отклонить", "reject", order)],
        ]
    elif status == OrderStatus.PRINTING:
        rows = [
            [_button("📦 Готов к выдаче", "ready", order), _button("❓ Уточнить", "ask", order)],
        ]
    elif status == OrderStatus.READY:
        rows = [
            [_button("📬 Выдан клиенту", "issue", order),
             _button("❓ Написать клиенту", "ask", order)],
        ]
    else:
        # rejected — заказ удалится через сутки, но написать клиенту ещё можно.
        rows = [[_button("❓ Написать клиенту", "ask", order)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def issue_confirm_kb(order: Order) -> InlineKeyboardMarkup:
    """Подтверждение выдачи: заказ после этого удаляется, отменить нельзя."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("✅ Да, выдан — удалить заказ", "issue_confirm", order)],
            [_button("↩️ Назад", "issue_back", order)],
        ]
    )


def orders_list_kb(orders: list[Order]) -> InlineKeyboardMarkup:
    """Кнопки «№N» под списком /orders — по 5 в ряд, нажатие открывает карточку."""
    buttons = [_button(f"№{order.id}", "open", order) for order in orders]
    rows = [buttons[i : i + 5] for i in range(0, len(buttons), 5)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_file_kb(has_file: bool) -> InlineKeyboardMarkup:
    """Последний шаг параметров: можно не присылать файл модели."""
    skip_text = "Оставить текущий файл" if has_file else "Пропустить — без файла"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=skip_text, callback_data=ADMIN_SKIP_FILE_CALLBACK)],
            [InlineKeyboardButton(text="Отмена", callback_data=ADMIN_CANCEL_CALLBACK)],
        ]
    )
