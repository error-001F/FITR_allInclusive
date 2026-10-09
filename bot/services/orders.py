from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Order, OrderType


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
    return order
