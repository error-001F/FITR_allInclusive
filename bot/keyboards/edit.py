from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Order, OrderType

EDIT_BACK_CALLBACK = "edit_back"  # «Отмена» под вариантами — вернуться в меню редактирования

FIELD_TITLES = {
    "material": "🧵 Материал",
    "layer": "📏 Слой",
    "infill": "🧱 Заполнение",
    "color": "🎨 Цвет",
    "file": "📎 Файл модели",
    "description": "📝 Описание",
    "references": "🖼 Референсы",
    "delivery": "🚚 Доставка",
    "payment": "💳 Оплата",
    "name": "👤 ФИО",
    "phone": "📞 Телефон",
}
PRINT_FIELDS = ["material", "layer", "infill", "color", "file"]
DESIGN_FIELDS = ["description", "references"]
COMMON_FIELDS = ["delivery", "payment", "name", "phone"]


class EditCallback(CallbackData, prefix="edit"):
    """Редактирование заказа клиентом, например edit:field:3:color.

    Номер заказа мог устареть после перенумерации — владелец, статус и дата создания
    проверяются в хендлере.
    """

    action: str  # open | field | done
    order_id: int
    field: str = ""


class EditOptionCallback(CallbackData, prefix="editv"):
    """Выбор нового значения кнопкой, например editv:color:Чёрный.

    Отдельный префикс: иначе кнопки перехватил бы обработчик устаревших кнопок оформления.
    """

    field: str
    value: str


def editable_fields(order: Order) -> list[str]:
    specific = PRINT_FIELDS if order.order_type == OrderType.PRINT else DESIGN_FIELDS
    return specific + COMMON_FIELDS


def edit_button(order: Order) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=f"✏️ Изменить №{order.id}",
        callback_data=EditCallback(action="open", order_id=order.id).pack(),
    )


def edit_menu_kb(order: Order) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=FIELD_TITLES[field],
            callback_data=EditCallback(action="field", order_id=order.id, field=field).pack(),
        )
        for field in editable_fields(order)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton(
            text="✅ Готово", callback_data=EditCallback(action="done", order_id=order.id).pack()
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
