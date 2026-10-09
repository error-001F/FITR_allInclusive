from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import AdminCard, Order, OrderStatus, OrderType


async def get_last_contacts(session: AsyncSession, user_id: int) -> tuple[str, str] | None:
    """ФИО и телефон из последнего заказа пользователя или None, если заказов не было.

    Клиента узнаём по user_id — постоянному id аккаунта Telegram (тег @username может меняться).
    """
    row = (
        await session.execute(
            select(Order.customer_name, Order.phone)
            .where(Order.user_id == user_id)
            .order_by(Order.id.desc())
            .limit(1)
        )
    ).first()
    return (row.customer_name, row.phone) if row else None


async def list_user_orders(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[Order]:
    """Последние заказы пользователя, новые сверху."""
    result = await session.scalars(
        select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(limit)
    )
    return list(result)


async def create_order(
    session: AsyncSession,
    *,
    user_id: int,
    username: str | None,
    order_type: OrderType,
    customer_name: str,
    phone: str,
    file_id: str | None = None,
    file_name: str | None = None,
    material: str | None = None,
    layer_height: str | None = None,
    infill: int | None = None,
    color: str | None = None,
    description: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> Order:
    order = Order(
        user_id=user_id,
        username=username,
        order_type=order_type,
        customer_name=customer_name,
        phone=phone,
        file_id=file_id,
        file_name=file_name,
        material=material,
        layer_height=layer_height,
        infill=infill,
        color=color,
        description=description,
        attachments=attachments,
    )
    session.add(order)
    await session.commit()
    # created_at заполняет сама БД; перечитываем заказ, чтобы это поле было доступно
    # (иначе SQLAlchemy попытается догрузить его неявно, что в async-коде — ошибка).
    await session.refresh(order)
    return order


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.get(Order, order_id)


async def update_order_status(
    session: AsyncSession,
    order_id: int,
    new_status: OrderStatus,
    allowed_from: set[OrderStatus],
    amount: Decimal | None = None,
) -> Order | None:
    """Меняет статус, только если текущий входит в allowed_from.

    None — заказа нет или его статус уже другой (например, мастер нажал старую кнопку).
    """
    order = await session.get(Order, order_id)
    if order is None or order.status not in allowed_from:
        return None
    order.status = new_status
    if amount is not None:
        order.amount = amount
    if new_status == OrderStatus.REJECTED:
        order.rejected_at = utc_now()
    await session.commit()
    return order


async def delete_old_rejected_orders(session: AsyncSession, older_than: timedelta) -> int:
    """Удаляет заказы, отклонённые раньше чем older_than назад, вместе с их карточками
    у мастеров. Возвращает количество удалённых заказов."""
    order_ids = (
        await session.scalars(
            select(Order.id).where(
                Order.status == OrderStatus.REJECTED,
                Order.rejected_at < utc_now() - older_than,
            )
        )
    ).all()
    if not order_ids:
        return 0
    await session.execute(delete(AdminCard).where(AdminCard.order_id.in_(order_ids)))
    await session.execute(delete(Order).where(Order.id.in_(order_ids)))
    await session.commit()
    return len(order_ids)


async def save_admin_card(
    session: AsyncSession, order_id: int, chat_id: int, message_id: int
) -> None:
    session.add(AdminCard(order_id=order_id, chat_id=chat_id, message_id=message_id))
    await session.commit()


async def list_admin_cards(session: AsyncSession, order_id: int) -> list[AdminCard]:
    return list(await session.scalars(select(AdminCard).where(AdminCard.order_id == order_id)))


def utc_now() -> datetime:
    # Время в БД храним в UTC без часового пояса — так же, как created_at от SQLite.
    return datetime.now(UTC).replace(tzinfo=None)
