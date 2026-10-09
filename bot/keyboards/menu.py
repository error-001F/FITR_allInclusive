from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

PRINT_BUTTON = "Заказать печать"
DESIGN_BUTTON = "Заказать разработку модели"
PRICE_BUTTON = "Прайс и информация"
MY_ORDERS_BUTTON = "Мои заказы"
CONTACT_MASTER_BUTTON = "Связаться с мастером"


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=PRINT_BUTTON), KeyboardButton(text=DESIGN_BUTTON)],
            [KeyboardButton(text=PRICE_BUTTON), KeyboardButton(text=MY_ORDERS_BUTTON)],
            [KeyboardButton(text=CONTACT_MASTER_BUTTON)],
        ],
        resize_keyboard=True,
    )


def master_link_kb(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Написать мастеру", url=f"https://t.me/{username}")]
        ]
    )
