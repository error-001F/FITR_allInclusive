from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Numeric, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrderType(StrEnum):
    PRINT = "print"  # печать по модели заказчика
    DESIGN = "design"  # разработка модели с нуля


class OrderStatus(StrEnum):
    NEW = "new"  # оформлен клиентом, ждёт оценки мастера
    AWAITING_PAYMENT = "awaiting_payment"  # мастер выставил счёт
    PRINTING = "printing"  # оплачен, в работе
    READY = "ready"  # готов к выдаче
    REJECTED = "rejected"  # отклонён мастером


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(32))
    order_type: Mapped[str] = mapped_column(String(16))
    # Для печати: файл модели хранится в Telegram, в БД — только его file_id.
    file_id: Mapped[str | None] = mapped_column(String(255))
    file_name: Mapped[str | None] = mapped_column(String(255))
    # Для печати: параметры (значения — ключи из bot/catalog.py).
    material: Mapped[str | None] = mapped_column(String(16))
    layer_height: Mapped[str | None] = mapped_column(String(8))
    infill: Mapped[int | None]
    color: Mapped[str | None] = mapped_column(String(32))
    # Для разработки: описание модели и референсы ([{"file_id": ..., "kind": "photo"|"document"}]).
    description: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    customer_name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.NEW)
    # Сумма счёта, BYN — выставляет мастер.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Когда мастер отклонил заказ (UTC). По нему удаляем старые отклонённые заказы.
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime)
