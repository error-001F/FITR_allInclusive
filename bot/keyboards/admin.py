from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Order, OrderStatus
from bot.keyboards.chat import history_button

ADMIN_CANCEL_CALLBACK = "adm_cancel"
ADMIN_SKIP_FILE_CALLBACK = "adm_skip_file"


class AdminOrderCallback(CallbackData, prefix="adm"):
    """Кнопка карточки заказа у мастера, например adm:invoice:5."""

    # invoice | invoice_ok | invoice_manual | invoice_weight | paid | pay_reject | print | ready |
    # reject | params | issue | issue_ask (из /orders) | issue_confirm | issue_back | open
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
             history_button(order)],
            [_button("🛠 Параметры печати", "params", order),
             _button("❌ Отклонить", "reject", order)],
        ]
    elif status == OrderStatus.AWAITING_PAYMENT:
        rows = [
            [_button("✅ Оплата получена", "paid", order),
             _button("💳 Изменить сумму", "invoice", order)],
            [_button("🛠 Параметры печати", "params", order),
             history_button(order)],
            [_button("❌ Отклонить", "reject", order)],
        ]
    elif status == OrderStatus.PAYMENT_CHECK:
        # Клиент прислал чек (он в «💬 Уточнить» → «📎 Вложения»); мастер сверяет поступление.
        rows = [
            [_button("✅ Оплата подтверждена", "paid", order),
             _button("⚠️ Оплата не найдена", "pay_reject", order)],
            [history_button(order), _button("❌ Отклонить", "reject", order)],
        ]
    elif status == OrderStatus.PAID:
        rows = [
            [_button("🖨 В печать", "print", order), history_button(order)],
            [_button("❌ Отклонить", "reject", order)],
        ]
    elif status == OrderStatus.PRINTING:
        rows = [
            [_button("📦 Готов к выдаче", "ready", order), history_button(order)],
        ]
    elif status == OrderStatus.READY:
        rows = [
            [_button("📬 Выдан клиенту", "issue", order),
             history_button(order)],
        ]
    else:
        # rejected — заказ удалится через сутки, но написать клиенту ещё можно.
        rows = [[history_button(order)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def issue_confirm_kb(order: Order) -> InlineKeyboardMarkup:
    """Подтверждение выдачи: заказ после этого удаляется, отменить нельзя."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("✅ Да, выдан — удалить заказ", "issue_confirm", order)],
            [_button("↩️ Назад", "issue_back", order)],
        ]
    )


def orders_list_kb(orders: list[Order], chat_counts: dict[int, int]) -> InlineKeyboardMarkup:
    """Под списком /orders строка на заказ: [📋 №N] — карточка, [📬 Выдать №N] — только у
    готовых к выдаче, [💬 Уточнить №N (k)] — история переписки, k — сколько в ней сообщений."""
    rows = []
    for order in orders:
        row = [_button(f"📋 №{order.id}", "open", order)]
        if order.status == OrderStatus.READY:
            row.append(_button(f"📬 Выдать №{order.id}", "issue_ask", order))
        row.append(history_button(order, chat_counts.get(order.id, 0)))
        rows.append(row)
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


def price_calc_kb(order: Order, total) -> InlineKeyboardMarkup:
    """Под расчётом цены: принять сумму бота, ввести свою или пересчитать с другим весом."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(f"✅ Выставить {total} BYN", "invoice_ok", order)],
            [_button("✏️ Своя сумма", "invoice_manual", order),
             _button("⚖️ Другой вес", "invoice_weight", order)],
        ]
    )
