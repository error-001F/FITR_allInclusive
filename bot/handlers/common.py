import logging

from aiogram import F, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.catalog import price_info_text
from bot.config import Settings
from bot.keyboards.menu import (
    CONTACT_MASTER_BUTTON,
    DESIGN_BUTTON,
    MY_ORDERS_BUTTON,
    PRICE_BUTTON,
    PRINT_BUTTON,
    main_menu_kb,
    master_link_kb,
)

router = Router(name="common")
logger = logging.getLogger(__name__)


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


@router.message(F.text == MY_ORDERS_BUTTON)
async def my_orders(message: Message) -> None:
    # Список заказов появится на этапе 4.
    await message.answer("Раздел «Мои заказы» скоро заработает.")
