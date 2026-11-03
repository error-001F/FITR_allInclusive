"""Сообщение с реквизитами для оплаты: его получает клиент со счётом и по кнопке «Оплатить»."""
import logging

from aiogram import html
from aiogram.types import InlineKeyboardMarkup

from bot.catalog import CASH, PAYMENT_METHOD_TITLES
from bot.config import Settings
from bot.database.models import Order
from bot.keyboards.menu import MY_ORDERS_BUTTON
from bot.keyboards.payment import requisites_kb

logger = logging.getLogger(__name__)

VALUE_LABELS = {"card": "Номер карты", "iban": "IBAN"}
IBAN_INSTRUCTION = (
    f"{html.bold('Как оплатить:')} в приложении банка откройте "
    "ЕРИП → Банки, НКФО → Альфа Банк, выберите пополнение счёта, "
    "вставьте IBAN и сумму."
)


def online_methods(settings: Settings) -> list[str]:
    """Способы, по которым клиент платит заранее и присылает чек: карта и IBAN."""
    return [method for method in settings.available_payment_methods() if method != CASH]


def payment_method_for(order: Order, settings: Settings) -> str | None:
    """Способ из заказа, если его реквизиты заданы; иначе первый доступный (у старых заказов
    способа нет). None — в .env нет ни карты, ни IBAN."""
    methods = online_methods(settings)
    if order.payment_method in methods:
        return order.payment_method
    return methods[0] if methods else None


def payment_message(
    order: Order, settings: Settings, method: str | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    """Текст с реквизитами и кнопки под ним. method — способ, на который переключился клиент."""
    method = method or payment_method_for(order, settings)
    lines = [html.bold(f"Заказ №{order.id} — к оплате {order.amount} BYN"), ""]
    value = ""
    if method is None:
        logger.warning("PAYMENT_CARD и PAYMENT_IBAN не заданы в .env — клиент получил счёт без реквизитов")
        lines.append("Реквизиты мастер пришлёт отдельным сообщением.")
    else:
        value = settings.payment_value(method)
        lines.append(f"Способ: {PAYMENT_METHOD_TITLES[method]}")
        # <code>: номер копируется нажатием прямо на текст.
        lines.append(f"{VALUE_LABELS[method]}: {html.code(html.quote(value))}")
        if settings.payment_recipient:
            lines.append(f"Получатель: {html.quote(settings.payment_recipient)}")
        if method == "iban":
            lines.append(f"\n{IBAN_INSTRUCTION}")
    lines.append(
        "\nПосле оплаты нажмите «📎 Я оплатил» и пришлите скриншот, файл чека "
        f"или напишите детали платежа. Оплатить позже можно в разделе «{MY_ORDERS_BUTTON}»."
    )
    other = next((m for m in online_methods(settings) if m != method), None) if method else None
    return "\n".join(lines), requisites_kb(order, method, value, other)
