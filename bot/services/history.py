"""Журнал истории заказов: текстовый файл, одна строка на событие.

Нужен, потому что выданные и давно отклонённые заказы удаляются из БД, а номера сдвигаются —
по журналу всегда можно восстановить, что и когда происходило с заказом.
"""
import logging
from pathlib import Path

from bot.catalog import SELF_PICKUP_METHOD
from bot.database.models import Order

history_logger = logging.getLogger("orders_history")


def setup_history(path: Path) -> None:
    """Подключает запись журнала в файл (дописывается, не перезаписывается)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M"))
    history_logger.addHandler(handler)
    history_logger.setLevel(logging.INFO)
    # Не дублировать события журнала в общий лог бота.
    history_logger.propagate = False


def log_event(order: Order, event: str) -> None:
    """Пишет событие заказа. Ошибку записи в файл logging обрабатывает сам — бот не упадёт."""
    telegram = f"@{order.username}" if order.username else f"id {order.user_id}"
    history_logger.info(
        "№%s | %s, %s, %s | %s",
        order.id,
        order.customer_name,
        order.phone,
        telegram,
        # Переносы строк из описаний и причин сворачиваем: одно событие — одна строка.
        " ".join(event.split()),
    )


def describe_order(order: Order) -> str:
    """Содержимое заказа для записи «создан» и «параметры изменены»."""
    parts = []
    if order.description:
        parts.append(f"описание «{order.description}»")
    if order.attachments:
        parts.append(f"референсов {len(order.attachments)}")
    if order.file_name:
        parts.append(f"файл {order.file_name}")
    params = [
        order.material,
        f"{order.layer_height} мм" if order.layer_height else None,
        f"заполнение {order.infill}%" if order.infill is not None else None,
        order.color,
    ]
    params = [param for param in params if param]
    if params:
        parts.append("печать " + ", ".join(params))
    if order.delivery_method:
        delivery = f"доставка {order.delivery_method}"
        if order.pickup_point:
            label = "адрес" if order.delivery_method == SELF_PICKUP_METHOD else "ПВЗ"
            delivery += f", {label} «{order.pickup_point}»"
        parts.append(delivery)
    if order.payment_method:
        parts.append(f"оплата {order.payment_method}")
    if order.promo_code:
        parts.append(f"промокод {order.promo_code} ({order.promo_kind})")
    return "; ".join(parts) or "без деталей"


def log_general(name: str, username: str | None, user_id: int, event: str) -> None:
    """Событие без заказа — например, общий вопрос клиента мастеру."""
    telegram = f"@{username}" if username else f"id {user_id}"
    history_logger.info("без заказа | %s, %s | %s", name, telegram, " ".join(event.split()))
