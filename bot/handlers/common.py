import logging

from aiogram import F, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.catalog import price_info_text
from bot.config import Settings
from bot.database.models import Order
from bot.keyboards.menu import (
    CONTACT_MASTER_BUTTON,
    DESIGN_BUTTON,
    MY_ORDERS_BUTTON,
    PRICE_BUTTON,
    PRINT_BUTTON,
    main_menu_kb,
    master_link_kb,
)
from bot.services.formatting import order_details_lines, order_header
from bot.services.orders import list_user_orders

router = Router(name="common")
logger = logging.getLogger(__name__)

# 10 заказов по ~250 символов укладываются в лимит Telegram (4096 символов на сообщение).
MY_ORDERS_LIMIT = 10
DESCRIPTION_PREVIEW_LENGTH = 60


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = message.from_user.full_name if message.from_user else "друг"
    await message.answer(
        f"Привет, {html.bold(html.quote(name))}! Выберите действие в меню ниже.",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        f"«{PRINT_BUTTON}» — печать по вашему файлу модели\n"
        f"«{DESIGN_BUTTON}» — разработка модели по описанию\n"
        f"«{PRICE_BUTTON}» — цены, габариты, доставка\n"
        f"«{MY_ORDERS_BUTTON}» — статусы ваших заказов\n"
        f"«{CONTACT_MASTER_BUTTON}» — написать мастеру напрямую\n\n"
        "/start — главное меню\n"
        "/cancel — отменить текущее действие\n"
        "/help — эта справка"
    )


@router.message(F.text == PRICE_BUTTON)
async def show_price(message: Message) -> None:
    await message.answer(price_info_text())


@router.message(F.text == CONTACT_MASTER_BUTTON)
async def contact_master(message: Message, settings: Settings) -> None:
    username = (settings.master_username or "").lstrip("@")
    if not username:
        logger.warning("MASTER_USERNAME не задан в .env — ссылка на мастера недоступна")
        await message.answer("Контакт мастера пока не указан. Попробуйте позже.")
        return
    await message.answer(
        f"Напишите мастеру напрямую: @{html.quote(username)}",
        reply_markup=master_link_kb(username),
    )


def format_order_line(order: Order) -> str:
    lines = [
        order_header(order),
        *order_details_lines(order, description_limit=DESCRIPTION_PREVIEW_LENGTH),
    ]
    return "\n".join(lines)


@router.message(F.text == MY_ORDERS_BUTTON)
async def my_orders(message: Message, session: AsyncSession) -> None:
    orders = await list_user_orders(session, message.from_user.id, limit=MY_ORDERS_LIMIT)
    if not orders:
        await message.answer("У вас пока нет заказов. Оформить — кнопками меню ниже.")
        return

    blocks = "\n\n".join(format_order_line(order) for order in orders)
    await message.answer(f"Ваши последние заказы:\n\n{blocks}")
