from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

PRINT_BUTTON = "🖨 Заказать печать"
DESIGN_BUTTON = "✏️ Заказать разработку модели"
PRICE_BUTTON = "💰 Прайс и информация"
MY_ORDERS_BUTTON = "📦 Мои заказы"
CONTACT_MASTER_BUTTON = "💬 Связаться с мастером"

# Кнопка reply-клавиатуры присылает боту свой текст как обычное сообщение. У тех, кто
# давно не открывал меню заново, на экране осталась старая клавиатура без эмодзи —
# её тексты тоже принимаем, иначе кнопки перестали бы работать.
PRINT_TEXTS = {PRINT_BUTTON, "Заказать печать"}
DESIGN_TEXTS = {DESIGN_BUTTON, "Заказать разработку модели"}
PRICE_TEXTS = {PRICE_BUTTON, "Прайс и информация"}
MY_ORDERS_TEXTS = {MY_ORDERS_BUTTON, "Мои заказы"}
CONTACT_MASTER_TEXTS = {CONTACT_MASTER_BUTTON, "Связаться с мастером"}


def main_menu_kb() -> ReplyKeyboardMarkup:
    # Две главные кнопки — каждая во всю ширину, чтобы длинный текст не обрезался
    # на узком экране; вспомогательные — ниже, попарно.
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=PRINT_BUTTON)],
            [KeyboardButton(text=DESIGN_BUTTON)],
            [KeyboardButton(text=PRICE_BUTTON), KeyboardButton(text=MY_ORDERS_BUTTON)],
            [KeyboardButton(text=CONTACT_MASTER_BUTTON)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие в меню ↓",
    )


def master_link_kb(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Написать мастеру", url=f"https://t.me/{username}")]
        ]
    )
