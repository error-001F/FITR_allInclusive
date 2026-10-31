from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import AdminCard, ChatMessage, Order, OrderStatus, OrderType


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
    weight_grams: Decimal | None | object = ...,
) -> Order | None:
    """Меняет статус, только если текущий входит в allowed_from.

    weight_grams: вес, по которому посчитан счёт; None — очистить (сумма введена вручную),
    ... (по умолчанию) — не трогать.

    None — заказа нет или его статус уже другой (например, мастер нажал старую кнопку).
    """
    order = await session.get(Order, order_id)
    if order is None or order.status not in allowed_from:
        return None
    order.status = new_status
    if amount is not None:
        order.amount = amount
    if weight_grams is not ...:
        order.weight_grams = weight_grams
    if new_status == OrderStatus.REJECTED:
        order.rejected_at = utc_now()
    await session.commit()
    return order


async def update_print_params(
    session: AsyncSession,
    order_id: int,
    allowed_from: set[OrderStatus],
    *,
    material: str,
    layer_height: str,
    infill: int,
    color: str,
    file_id: str | None = None,
    file_name: str | None = None,
) -> Order | None:
    """Мастер задаёт параметры печати. Файл меняется, только если прислан новый.

    None — заказа нет или его статус не позволяет менять параметры.
    """
    order = await session.get(Order, order_id)
    if order is None or order.status not in allowed_from:
        return None
    order.material = material
    order.layer_height = layer_height
    order.infill = infill
    order.color = color
    if file_id is not None:
        order.file_id = file_id
        order.file_name = file_name
    await session.commit()
    return order


async def find_expired_rejected_orders(
    session: AsyncSession, older_than: timedelta
) -> list[Order]:
    """Заказы, отклонённые раньше чем older_than назад, — их пора удалять."""
    result = await session.scalars(
        select(Order).where(
            Order.status == OrderStatus.REJECTED,
            Order.rejected_at < utc_now() - older_than,
        )
    )
    return list(result)


async def delete_order(session: AsyncSession, order_id: int) -> None:
    """Удаляет заказ вместе с записями о его карточках у мастеров и перепиской."""
    await session.execute(delete(AdminCard).where(AdminCard.order_id == order_id))
    await session.execute(delete(ChatMessage).where(ChatMessage.order_id == order_id))
    await session.execute(delete(Order).where(Order.id == order_id))
    await session.commit()


async def renumber_orders(session: AsyncSession) -> dict[int, int]:
    """Перенумеровывает заказы подряд 1..N без пропусков, сохраняя их порядок.

    Возвращает {старый номер: новый} только для изменившихся. Идём по возрастанию номера,
    поэтому целевой номер всегда свободен: например, 2,4,5 → сначала 2→1 (№1 удалён),
    потом 4→2 (№2 только что освободился) и т.д.
    """
    ids = (await session.scalars(select(Order.id).order_by(Order.id))).all()
    mapping = {old: new for new, old in enumerate(ids, start=1) if old != new}
    for old, new in mapping.items():
        await session.execute(
            update(Order).where(Order.id == old).values(id=new),
            execution_options={"synchronize_session": False},
        )
        await session.execute(
            update(AdminCard).where(AdminCard.order_id == old).values(order_id=new),
            execution_options={"synchronize_session": False},
        )
        # История переписки переезжает на новый номер вместе с заказом.
        await session.execute(
            update(ChatMessage).where(ChatMessage.order_id == old).values(order_id=new),
            execution_options={"synchronize_session": False},
        )
    if mapping:
        await session.commit()
        # Объекты, загруженные до перенумерации, помнят старые номера — забываем их,
        # чтобы следующие запросы в этой сессии читали заказы из БД заново.
        session.expunge_all()
    return mapping


async def list_orders(
    session: AsyncSession, statuses: set[OrderStatus] | None, limit: int
) -> list[Order]:
    """Заказы с указанными статусами (None — все), новые сверху."""
    query = select(Order).order_by(Order.id.desc()).limit(limit)
    if statuses is not None:
        query = query.where(Order.status.in_(statuses))
    return list(await session.scalars(query))


async def count_orders(session: AsyncSession, statuses: set[OrderStatus] | None) -> int:
    query = select(func.count()).select_from(Order)
    if statuses is not None:
        query = query.where(Order.status.in_(statuses))
    return await session.scalar(query)


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
