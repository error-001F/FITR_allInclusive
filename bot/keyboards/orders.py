from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.catalog import COLORS, INFILL_SURCHARGES, LAYER_PRICES, MATERIALS
from bot.database.models import OrderType

CANCEL_BUTTON = "Отмена"
CONTACT_BUTTON = "Поделиться номером телефона"
DONE_BUTTON = "Готово"
SKIP_BUTTON = "Пропустить"
CONFIRM_CALLBACK = "order_confirm"
CANCEL_CALLBACK = "order_cancel"

ORDER_TYPE_TITLES = {
    OrderType.PRINT: "Печать по моей 3D-модели",
    OrderType.DESIGN: "Разработка 3D-модели",
}


class PrintOptionCallback(CallbackData, prefix="print"):
    """Кнопка выбора параметра печати, например print:material:PETG."""

    field: str
    value: str


# Значки-градиент «грубо → качественно» и «легко → прочно» в тексте кнопок.
LAYER_ICONS = {"0.28": "🔴", "0.20": "🟠", "0.16": "🟡", "0.12": "🟢", "0.08": "🟢"}
INFILL_ICONS = {10: "🔴", 20: "🟠", 40: "🟡", 60: "🟡", 80: "🟢", 100: "🟢"}
# Для значений, добавленных в catalog.py без значка.
DEFAULT_ICON = "⚪"


def _options_kb(field: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """options — список (значение, текст кнопки)."""
    rows = [
        [
            InlineKeyboardButton(
                text=title,
                callback_data=PrintOptionCallback(field=field, value=value).pack(),
            )
        ]
        for value, title in options
    ]
    rows.append([InlineKeyboardButton(text=CANCEL_BUTTON, callback_data=CANCEL_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def material_kb() -> InlineKeyboardMarkup:
    return _options_kb("material", [(m, m) for m in MATERIALS])


def layer_kb() -> InlineKeyboardMarkup:
    return _options_kb(
        "layer",
        [
            (layer, f"{LAYER_ICONS.get(layer, DEFAULT_ICON)} {layer} мм — {price} BYN/г")
            for layer, price in LAYER_PRICES.items()
        ],
    )


def infill_kb() -> InlineKeyboardMarkup:
    options = []
    for infill, surcharge in INFILL_SURCHARGES.items():
        icon = INFILL_ICONS.get(infill, DEFAULT_ICON)
        price = f"+{surcharge} BYN" if surcharge else "без доплаты"
        options.append((str(infill), f"{icon} {infill}% ({price})"))
    return _options_kb("infill", options)


def color_kb() -> InlineKeyboardMarkup:
    return _options_kb("color", [(c, c) for c in COLORS])


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CANCEL_BUTTON)]],
        resize_keyboard=True,
    )


def references_kb() -> ReplyKeyboardMarkup:
    # Reply-клавиатура, а не inline: пользователь шлёт несколько файлов,
    # и кнопки под сообщением уехали бы вверх по чату.
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=DONE_BUTTON), KeyboardButton(text=SKIP_BUTTON)],
            [KeyboardButton(text=CANCEL_BUTTON)],
        ],
        resize_keyboard=True,
    )


def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CONTACT_BUTTON, request_contact=True)],
            [KeyboardButton(text=CANCEL_BUTTON)],
        ],
        resize_keyboard=True,
    )


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data=CONFIRM_CALLBACK),
                InlineKeyboardButton(text="Отменить", callback_data=CANCEL_CALLBACK),
            ]
        ]
    )
