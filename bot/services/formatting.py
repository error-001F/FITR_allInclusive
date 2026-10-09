"""Оформление заказа для сообщений: общее для «Моих заказов» и карточки мастера."""
from datetime import UTC, datetime

from aiogram import html

from bot.catalog import LOCAL_TIMEZONE, ORDER_STATUS_TITLES
from bot.database.models import Order
from bot.keyboards.orders import ORDER_TYPE_TITLES


def local_time(dt: datetime) -> datetime:
    # SQLite возвращает время без часового пояса, хотя записано оно в UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(LOCAL_TIMEZONE)


def order_header(order: Order) -> str:
    return html.bold(f"Заказ №{order.id} от {local_time(order.created_at):%d.%m.%Y %H:%M}")


def order_details_lines(order: Order, description_limit: int | None = None) -> list[str]:
    """Строки заказа: тип, описание, файлы, параметры печати, статус, сумма.

    Форма универсальная: выводим все заполненные поля независимо от типа заказа —
    мастер может добавить в заказ на разработку файл и параметры печати.
    """
    lines = [ORDER_TYPE_TITLES.get(order.order_type, order.order_type)]

    if order.description:
        description = order.description
        if description_limit and len(description) > description_limit:
            description = description[:description_limit].rstrip() + "…"
        lines.append(f"Описание: «{html.quote(description)}»")
    if order.attachments:
        lines.append(f"Референсы: {len(order.attachments)} шт.")
    if order.file_name:
        lines.append(f"Файл модели: {html.quote(order.file_name)}")

    print_params = [
        order.material,
        f"{order.layer_height} мм" if order.layer_height else None,
        f"заполнение {order.infill}%" if order.infill is not None else None,
        order.color,
    ]
    print_params = [param for param in print_params if param]
    if print_params:
        lines.append(f"Печать: {', '.join(print_params)}")

    # Неизвестный статус (если появится новый) показываем как есть, а не падаем.
    lines.append(f"Статус: {ORDER_STATUS_TITLES.get(order.status, order.status)}")
    if order.amount is not None:
        lines.append(f"Сумма: {order.amount} BYN")
    return lines


def admin_card_text(order: Order) -> str:
    """Карточка заказа для мастера: контакты клиента + все данные заказа."""
    if order.username:
        telegram = f"@{html.quote(order.username)}"
    else:
        # Без тега на клиента можно сослаться только по id — ссылка откроет его профиль.
        telegram = f'<a href="tg://user?id={order.user_id}">профиль клиента</a>'
    lines = [
        order_header(order),
        f"ФИО: {html.quote(order.customer_name)}",
        f"Телефон: {html.quote(order.phone)}",
        f"Telegram: {telegram}",
        "",
        *order_details_lines(order),
    ]
    return "\n".join(lines)
