from aiogram.filters.callback_data import CallbackData
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Order

COPY_LABELS = {"card": "📋 Скопировать номер карты", "iban": "📋 Скопировать IBAN"}
SWITCH_LABELS = {"card": "🔄 Оплатить картой", "iban": "🔄 Оплатить по IBAN"}


class PayCallback(CallbackData, prefix="pay"):
    """Кнопки оплаты у клиента, например pay:proof:5.

    action: show — прислать реквизиты; card | iban — переключить способ; proof — отправить чек.
    Номер заказа мог устареть после перенумерации — владелец и статус проверяются в хендлере.
    """

    action: str
    order_id: int


def _button(text: str, action: str, order: Order) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=PayCallback(action=action, order_id=order.id).pack()
    )


def pay_button(order: Order) -> InlineKeyboardButton:
    return _button(f"💳 Оплатить №{order.id}", "show", order)


def pay_kb(order: Order) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[pay_button(order)]])


def orders_pay_kb(orders: list[Order]) -> InlineKeyboardMarkup:
    """Под списком «Мои заказы»: кнопка оплаты для каждого неоплаченного заказа."""
    return InlineKeyboardMarkup(inline_keyboard=[[pay_button(order)] for order in orders])


def requisites_kb(
    order: Order, method: str | None, value: str, other_method: str | None
) -> InlineKeyboardMarkup:
    """Под реквизитами: скопировать номер и сумму (одним нажатием, без выделения текста),
    отправить чек, переключиться на другой способ. method=None — реквизитов нет."""
    rows = []
    if method is not None:
        # Пробелы в номере карты и IBAN мешают вставке в банковское приложение.
        rows.append([
            InlineKeyboardButton(
                text=COPY_LABELS[method], copy_text=CopyTextButton(text="".join(value.split()))
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text=f"📋 Скопировать сумму ({order.amount})",
            copy_text=CopyTextButton(text=str(order.amount)),
        )
    ])
    rows.append([_button("📎 Я оплатил — отправить чек", "proof", order)])
    if other_method is not None:
        rows.append([_button(SWITCH_LABELS[other_method], other_method, order)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
