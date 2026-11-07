"""Промокоды: проверка, «израсходовать», «вернуть», защита от подбора.

Сами коды и расчёт скидки — в bot/catalog.py, использованные коды — в таблице promo_uses.
"""

import logging
import time
from collections import defaultdict, deque

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.catalog import (
    FREE_PROMO_CODES,
    HALF_PROMO_CODES,
    PROMO_ATTEMPTS_WINDOW,
    PROMO_MAX_ATTEMPTS,
    normalize_promo,
    promo_kind,
)
from bot.database.models import PromoUse

logger = logging.getLogger(__name__)

# user_id → моменты неверных попыток (time.monotonic). Хранится в памяти: после перезапуска
# бота счётчик обнуляется — для защиты от перебора этого достаточно.
_failed_attempts: dict[int, deque[float]] = defaultdict(deque)


def check_promo_codes() -> None:
    """При запуске: предупредить об ошибках в списках кодов в catalog.py."""
    free = {normalize_promo(code) for code in FREE_PROMO_CODES}
    half = {normalize_promo(code) for code in HALF_PROMO_CODES}
    for code in free & half:
        logger.warning("Промокод %s есть в обоих списках — он будет считаться бесплатным", code)
    for code in free | half:
        if not code or len(code) > 32:
            logger.warning("Промокод %r пустой или длиннее 32 символов — он не будет работать", code)


def attempts_left(user_id: int) -> bool:
    """Можно ли пользователю ещё пробовать коды (не превышен лимит неверных за час)."""
    attempts = _failed_attempts[user_id]
    border = time.monotonic() - PROMO_ATTEMPTS_WINDOW.total_seconds()
    while attempts and attempts[0] < border:
        attempts.popleft()
    return len(attempts) < PROMO_MAX_ATTEMPTS


def register_failed_attempt(user_id: int) -> None:
    _failed_attempts[user_id].append(time.monotonic())


async def is_promo_used(session: AsyncSession, code: str) -> bool:
    return await session.get(PromoUse, code) is not None


async def find_available_promo(session: AsyncSession, text: str) -> tuple[str, str] | None:
    """(код, вид), если код есть в catalog.py и ещё не использован; иначе None."""
    code = normalize_promo(text)
    kind = promo_kind(code)
    if kind is None or len(code) > 32 or await is_promo_used(session, code):
        return None
    return code, kind


async def reserve_promo(session: AsyncSession, code: str, user_id: int) -> bool:
    """Помечает код израсходованным. False — его уже израсходовали (в т.ч. только что,
    другим клиентом: первичный ключ не даст записать код дважды)."""
    session.add(PromoUse(code=code, user_id=user_id))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    return True


async def release_promo(session: AsyncSession, code: str) -> None:
    """Код снова действует (заказ с ним отклонён)."""
    await session.execute(delete(PromoUse).where(PromoUse.code == code))
    await session.commit()
