from pathlib import Path

from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.database.models import Base


def create_engine(db_path: Path) -> AsyncEngine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}")


def create_session_pool(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: после commit поля объекта (например, id заказа)
    # читаются без повторного запроса к БД.
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn: Connection) -> None:
    """Простейшая миграция: create_all не добавляет новые колонки в существующую таблицу.

    Когда схема начнёт меняться часто, стоит перейти на Alembic.
    """
    columns = {column["name"] for column in inspect(conn).get_columns("orders")}
    if "rejected_at" not in columns:
        conn.execute(text("ALTER TABLE orders ADD COLUMN rejected_at DATETIME"))
        # Уже отклонённым заказам время отказа неизвестно — считаем от момента миграции.
        conn.execute(
            text("UPDATE orders SET rejected_at = CURRENT_TIMESTAMP WHERE status = 'rejected'")
        )
