"""Статистика запусков бота (/start) для команды мастера /activity."""
from datetime import date, datetime, timedelta

from aiogram import html
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.catalog import LOCAL_TIMEZONE
from bot.database.models import StartEvent

ACTIVITY_DAYS = 7  # за сколько дней показывать сводку (включая сегодня)
ACTIVITY_KEEP_DAYS = 30  # сколько дней хранить записи о запусках
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def local_today() -> date:
    return datetime.now(LOCAL_TIMEZONE).date()


async def record_start(session: AsyncSession, user_id: int) -> None:
    """Отметить, что пользователь запускал бота сегодня. Повторный /start за день не дублируется."""
    today = local_today()
    await session.execute(
        insert(StartEvent).values(day=today, user_id=user_id).on_conflict_do_nothing()
    )
    await session.execute(
        delete(StartEvent).where(StartEvent.day < today - timedelta(days=ACTIVITY_KEEP_DAYS))
    )
    await session.commit()


async def start_stats(
    session: AsyncSession, days: int = ACTIVITY_DAYS
) -> tuple[list[tuple[date, int]], int]:
    """Уникальные люди по дням (от старых к новым, дни без запусков — 0) и всего за период."""
    today = local_today()
    first_day = today - timedelta(days=days - 1)
    rows = await session.execute(
        select(StartEvent.day, func.count())
        .where(StartEvent.day >= first_day)
        .group_by(StartEvent.day)
    )
    per_day = dict(rows.all())
    total = await session.scalar(
        select(func.count(func.distinct(StartEvent.user_id))).where(StartEvent.day >= first_day)
    )
    by_day = [
        (day, per_day.get(day, 0))
        for day in (first_day + timedelta(days=offset) for offset in range(days))
    ]
    return by_day, total or 0


async def activity_text(session: AsyncSession) -> str:
    by_day, total = await start_stats(session)
    today = local_today()
    lines = [
        f"{WEEKDAYS[day.weekday()]} {day:%d.%m} — {count}" + (" (сегодня)" if day == today else "")
        for day, count in by_day
    ]
    return (
        f"{html.bold(f'📈 Запуски бота (/start) за {ACTIVITY_DAYS} дней')}\n"
        "Уникальные люди по дням (мастера не считаются):\n\n"
        + "\n".join(lines)
        + f"\n\nУникальных людей за неделю: {html.bold(str(total))}\n"
        "Сумма по дням может быть больше: один человек мог заходить в разные дни."
    )
