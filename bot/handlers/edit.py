"""Клиент редактирует свой заказ из «📦 Мои заказы», пока мастер не выставил счёт."""
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, html
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from bot.catalog import (
    COLORS,
    DELIVERY_METHODS,
    INFILL_HINT,
    INFILL_SURCHARGES,
    LAYER_HINT,
    LAYER_PRICES,
    MATERIALS,
    PAYMENT_METHOD_TITLES,
    PICKUP_POINT_METHOD,
    PRINT_EXTENSIONS,
    delivery_title,
)
from bot.config import Settings
from bot.database.models import Order, OrderStatus
from bot.handlers.orders import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    MAX_PICKUP_POINT_LENGTH,
    MAX_REFERENCES,
    MIN_DESCRIPTION_LENGTH,
    MIN_PICKUP_POINT_LENGTH,
    NAME_PATTERN,
)
from bot.keyboards.edit import (
    EDIT_BACK_CALLBACK,
    EditCallback,
    EditOptionCallback,
    edit_menu_kb,
    editable_fields,
)
from bot.keyboards.menu import CONTACT_MASTER_BUTTON, main_menu_kb
from bot.keyboards.orders import (
    CONTACT_BUTTON,
    DONE_BUTTON,
    SKIP_BUTTON,
    cancel_kb,
    color_kb,
    contact_kb,
    infill_kb,
    layer_kb,
    material_kb,
    options_kb,
    references_kb,
)
from bot.services.formatting import order_details_lines
from bot.services.history import log_event
from bot.services.notifications import notify_admins_order_changed
from bot.services.orders import get_order, update_client_order
from bot.states import EditForm

# Роутер подключается после роутера заказов: там общий «Отмена»//cancel, он должен
# срабатывать раньше, чем ввод нового значения примет «Отмена» за ФИО или описание.
router = Router(name="edit")

NOT_EDITABLE = (
    "Этот заказ уже нельзя изменить: мастер выставил счёт или заказ закрыт. "
    f"Изменения обсудите через «{CONTACT_MASTER_BUTTON}»."
)
EXTENSIONS_TEXT = ", ".join(PRINT_EXTENSIONS)

# Поле заказа → как его называть в сообщении мастеру и в журнале.
ATTR_LABELS = {
    "material": "материал",
    "layer_height": "слой",
    "infill": "заполнение",
    "color": "цвет",
    "file_name": "файл модели",
    "description": "описание",
    "attachments": "референсы",
    "delivery_method": "доставка",
    "pickup_point": "адрес ПВЗ",
    "payment_method": "оплата",
    "customer_name": "ФИО",
    "phone": "телефон",
}
OPTION_PROMPTS = {
    "material": "Выберите материал:",
    "layer": f"Выберите качество печати (толщину слоя):\n\n{html.italic(LAYER_HINT)}",
    "infill": f"Выберите плотность заполнения:\n\n{html.italic(INFILL_HINT)}",
    "color": "Выберите цвет пластика:",
    "delivery": "Выберите способ доставки:",
    "payment": "Выберите способ оплаты:",
}


async def load_editable(session: AsyncSession, order_id: int, user_id: int) -> Order | None:
    """Заказ, если его ещё можно редактировать этому клиенту."""
    order = await get_order(session, order_id)
    if order is None or order.user_id != user_id or order.status != OrderStatus.NEW:
        return None
    return order


def menu_text(order: Order) -> str:
    lines = [
        html.bold(f"✏️ Изменение заказа №{order.id}"),
        "",
        *order_details_lines(order),
        f"ФИО: {html.quote(order.customer_name)}",
        f"Телефон: {html.quote(order.phone)}",
        "",
        "Что изменить? Каждое изменение сохраняется сразу и приходит мастеру.",
    ]
    return "\n".join(lines)


async def show_menu(message: Message, order: Order) -> None:
    await message.answer(menu_text(order), reply_markup=edit_menu_kb(order))


def value_text(attr: str, value: Any) -> str:
    if value is None or value == []:
        return "—"
    if attr == "layer_height":
        return f"{value} мм"
    if attr == "infill":
        return f"{value}%"
    if attr == "delivery_method":
        return delivery_title(value)
    if attr == "payment_method":
        return PAYMENT_METHOD_TITLES.get(value, value)
    if attr == "attachments":
        return f"{len(value)} шт."
    text = str(value)
    return text if len(text) <= 100 else text[:100].rstrip() + "…"


def change_lines(changes: list[tuple[str, Any, Any]], as_html: bool) -> list[str]:
    """«цвет: Белый → Чёрный»; file_id и прочие служебные поля не показываем."""
    quote = html.quote if as_html else str
    return [
        f"{ATTR_LABELS[attr]}: {quote(value_text(attr, old))} → {quote(value_text(attr, new))}"
        for attr, old, new in changes
        if attr in ATTR_LABELS
    ]


async def save_changes(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
    client: User,
    fields: dict[str, Any],
    new_files: list[dict] = (),
) -> None:
    """Сохраняет изменение одного поля, уведомляет мастеров и снова показывает меню."""
    data = await state.get_data()
    await state.clear()
    result = await update_client_order(
        session, data["order_id"], client.id, data["order_created_at"], fields
    )
    if result is None:
        # Пока клиент вводил новое значение, мастер выставил счёт или номер сдвинулся.
        await message.answer(NOT_EDITABLE, reply_markup=main_menu_kb())
        return
    order, changes = result
    lines = change_lines(changes, as_html=True)
    if not lines:
        await message.answer("Ничего не изменилось.", reply_markup=main_menu_kb())
        await show_menu(message, order)
        return

    log_event(order, "клиент изменил: " + "; ".join(change_lines(changes, as_html=False)))
    await message.answer("✅ Сохранено, мастер получил уведомление.", reply_markup=main_menu_kb())
    await notify_admins_order_changed(bot, session, settings, order, lines, list(new_files))
    await show_menu(message, order)


# --- Меню ---


@router.callback_query(EditCallback.filter(F.action == "open"))
async def open_edit(
    callback: CallbackQuery, callback_data: EditCallback, state: FSMContext, session: AsyncSession
) -> None:
    order = await load_editable(session, callback_data.order_id, callback.from_user.id)
    if order is None:
        await callback.answer(NOT_EDITABLE, show_alert=True)
        return
    await state.clear()
    await show_menu(callback.message, order)
    await callback.answer()


@router.callback_query(EditCallback.filter(F.action == "done"))
async def edit_done(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Готово.", reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(EditCallback.filter(F.action == "field"))
async def edit_field(
    callback: CallbackQuery,
    callback_data: EditCallback,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    order = await load_editable(session, callback_data.order_id, callback.from_user.id)
    if order is None:
        await callback.answer(NOT_EDITABLE, show_alert=True)
        return
    field = callback_data.field
    if field not in editable_fields(order):
        await callback.answer("Это поле у такого заказа не меняется.", show_alert=True)
        return

    await state.clear()
    # Дату создания запоминаем, чтобы при сохранении убедиться: под этим номером всё тот же
    # заказ (номера сдвигаются после удаления выданных и отклонённых заказов).
    await state.update_data(
        order_id=order.id, order_created_at=order.created_at.isoformat(), field=field
    )
    await callback.answer()
    message = callback.message

    option_kbs = {
        "material": lambda: material_kb(EditOptionCallback, EDIT_BACK_CALLBACK),
        "layer": lambda: layer_kb(EditOptionCallback, EDIT_BACK_CALLBACK),
        "infill": lambda: infill_kb(EditOptionCallback, EDIT_BACK_CALLBACK),
        "color": lambda: color_kb(EditOptionCallback, EDIT_BACK_CALLBACK),
        "delivery": lambda: options_kb(
            "delivery",
            [(method, delivery_title(method)) for method in DELIVERY_METHODS],
            EditOptionCallback,
            EDIT_BACK_CALLBACK,
        ),
        "payment": lambda: options_kb(
            "payment",
            [(m, PAYMENT_METHOD_TITLES[m]) for m in settings.available_payment_methods()],
            EditOptionCallback,
            EDIT_BACK_CALLBACK,
        ),
    }
    if field in option_kbs:
        await state.set_state(EditForm.choosing_option)
        await message.answer(OPTION_PROMPTS[field], reply_markup=option_kbs[field]())
    elif field == "file":
        await state.set_state(EditForm.waiting_file)
        await message.answer(
            f"Пришлите новый файл модели ({EXTENSIONS_TEXT}) как документ.",
            reply_markup=cancel_kb(),
        )
    elif field == "description":
        await state.set_state(EditForm.waiting_description)
        await message.answer(
            "Напишите новое описание задачи: что нужно сделать, примерные размеры, "
            "для чего деталь и желаемые сроки.",
            reply_markup=cancel_kb(),
        )
    elif field == "references":
        await state.set_state(EditForm.waiting_references)
        await state.update_data(attachments=[])
        await message.answer(
            f"Пришлите новые фото, эскизы или чертежи (до {MAX_REFERENCES}) — они заменят "
            f"прежние. Когда закончите, нажмите «{DONE_BUTTON}». «{SKIP_BUTTON}» — убрать все.",
            reply_markup=references_kb(),
        )
    elif field == "name":
        await state.set_state(EditForm.waiting_name)
        await message.answer(
            "Напишите ФИО (например: Иванов Иван Иванович).", reply_markup=cancel_kb()
        )
    elif field == "phone":
        await state.set_state(EditForm.waiting_contact)
        await message.answer(
            f"Нажмите кнопку «{CONTACT_BUTTON}» ниже.", reply_markup=contact_kb()
        )


@router.callback_query(F.data == EDIT_BACK_CALLBACK)
async def edit_back(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    order = await load_editable(session, data.get("order_id", 0), callback.from_user.id)
    if order is None:
        await callback.message.answer("Главное меню:", reply_markup=main_menu_kb())
        return
    await show_menu(callback.message, order)


# --- Выбор кнопками: параметры печати, доставка, оплата ---


@router.callback_query(EditForm.choosing_option, EditOptionCallback.filter())
async def option_chosen(
    callback: CallbackQuery,
    callback_data: EditOptionCallback,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    field = (await state.get_data())["field"]
    value = callback_data.value
    if callback_data.field != field:
        await callback.answer("Эта кнопка уже неактуальна.")
        return

    # Значение из кнопки проверяем по справочникам: callback_data можно подделать.
    fields: dict[str, Any] | None = None
    if field == "material" and value in MATERIALS:
        fields = {"material": value}
    elif field == "layer" and value in LAYER_PRICES:
        fields = {"layer_height": value}
    elif field == "infill" and value.isdigit() and int(value) in INFILL_SURCHARGES:
        fields = {"infill": int(value)}
    elif field == "color" and value in COLORS:
        fields = {"color": value}
    elif field == "payment" and value in settings.available_payment_methods():
        fields = {"payment_method": value}
    elif field == "delivery" and value in DELIVERY_METHODS:
        if value == PICKUP_POINT_METHOD:
            # Для Европочты нужен адрес ПВЗ — сохраняем доставку вместе с ним.
            await state.update_data(pending_delivery=value)
            await state.set_state(EditForm.waiting_pickup_point)
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(
                "Напишите адрес пункта выдачи Европочты (город, улица, дом или номер ПВЗ).",
                reply_markup=cancel_kb(),
            )
            await callback.answer()
            return
        fields = {"delivery_method": value}
        order = await get_order(session, (await state.get_data())["order_id"])
        if order is not None and order.delivery_method != value:
            # При смене способа старый адрес не подходит (ПВЗ Европочты ≠ адрес самовывоза).
            # Повторный выбор того же самовывоза адрес, вписанный мастером, не стирает.
            fields["pickup_point"] = None
    if fields is None:
        await callback.answer("Такого варианта нет.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await save_changes(callback.message, state, session, bot, settings, callback.from_user, fields)


@router.message(EditForm.choosing_option)
async def option_use_buttons(message: Message) -> None:
    await message.answer("Выберите вариант кнопкой под сообщением выше. Отмена — /cancel.")


@router.message(EditForm.waiting_pickup_point, F.text)
async def edit_pickup_point(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    address = " ".join(message.text.split())
    if not MIN_PICKUP_POINT_LENGTH <= len(address) <= MAX_PICKUP_POINT_LENGTH:
        await message.answer(
            f"Напишите адрес ПВЗ: от {MIN_PICKUP_POINT_LENGTH} "
            f"до {MAX_PICKUP_POINT_LENGTH} символов."
        )
        return
    delivery = (await state.get_data())["pending_delivery"]
    await save_changes(
        message, state, session, bot, settings, message.from_user,
        {"delivery_method": delivery, "pickup_point": address},
    )


@router.message(EditForm.waiting_pickup_point)
async def edit_pickup_point_invalid(message: Message) -> None:
    await message.answer("Напишите адрес ПВЗ текстовым сообщением.")


# --- Ввод сообщением: файл, описание, референсы, ФИО, телефон ---


@router.message(EditForm.waiting_file, F.document)
async def edit_file(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    file_name = message.document.file_name or ""
    if Path(file_name).suffix.lower() not in PRINT_EXTENSIONS:
        await message.answer(f"Нужен файл модели с расширением {EXTENSIONS_TEXT}.")
        return
    await save_changes(
        message, state, session, bot, settings, message.from_user,
        {"file_id": message.document.file_id, "file_name": file_name},
        new_files=[{"file_id": message.document.file_id, "kind": "document"}],
    )


@router.message(EditForm.waiting_file)
async def edit_file_invalid(message: Message) -> None:
    await message.answer(f"Пришлите файл {EXTENSIONS_TEXT} как документ (скрепка → Файл).")


@router.message(EditForm.waiting_description, F.text)
async def edit_description(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    description = message.text.strip()
    if not MIN_DESCRIPTION_LENGTH <= len(description) <= MAX_DESCRIPTION_LENGTH:
        await message.answer(
            f"Описание должно быть от {MIN_DESCRIPTION_LENGTH} "
            f"до {MAX_DESCRIPTION_LENGTH} символов."
        )
        return
    await save_changes(
        message, state, session, bot, settings, message.from_user, {"description": description}
    )


@router.message(EditForm.waiting_description)
async def edit_description_invalid(message: Message) -> None:
    await message.answer("Опишите задачу текстовым сообщением.")


@router.message(EditForm.waiting_references, F.photo | F.document)
async def edit_reference(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    attachments = list(data.get("attachments", []))
    # Альбом приходит отдельным сообщением на каждое фото — отвечаем один раз на альбом.
    album_id = message.media_group_id
    is_same_album = album_id is not None and album_id == data.get("last_album_id")
    await state.update_data(last_album_id=album_id)

    if len(attachments) >= MAX_REFERENCES:
        if not is_same_album:
            await message.answer(
                f"Можно прислать не больше {MAX_REFERENCES} файлов. Нажмите «{DONE_BUTTON}»."
            )
        return
    if message.photo:
        item = {"file_id": message.photo[-1].file_id, "kind": "photo", "file_name": None}
    else:
        item = {
            "file_id": message.document.file_id,
            "kind": "document",
            "file_name": message.document.file_name,
        }
    attachments.append(item)
    await state.update_data(attachments=attachments)
    if not is_same_album:
        await message.answer(
            f"Получено ({len(attachments)} из {MAX_REFERENCES}). "
            f"Можно прислать ещё или нажать «{DONE_BUTTON}»."
        )


@router.message(EditForm.waiting_references, F.text.in_({DONE_BUTTON, SKIP_BUTTON}))
async def edit_references_done(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    attachments = [] if message.text == SKIP_BUTTON else (await state.get_data()).get("attachments", [])
    await save_changes(
        message, state, session, bot, settings, message.from_user,
        # Пустой набор храним как NULL — так же, как при оформлении.
        {"attachments": attachments or None},
        new_files=attachments,
    )


@router.message(EditForm.waiting_references)
async def edit_references_invalid(message: Message) -> None:
    await message.answer(
        f"Пришлите фото или файл, либо нажмите «{DONE_BUTTON}» / «{SKIP_BUTTON}»."
    )


@router.message(EditForm.waiting_name, F.text)
async def edit_name(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    name = " ".join(message.text.split())
    if len(name) > MAX_NAME_LENGTH or not NAME_PATTERN.fullmatch(name):
        await message.answer(
            "Напишите фамилию и имя (можно с отчеством) буквами, через пробел. "
            f"Допустимы дефис и апостроф, до {MAX_NAME_LENGTH} символов."
        )
        return
    await save_changes(
        message, state, session, bot, settings, message.from_user, {"customer_name": name}
    )


@router.message(EditForm.waiting_name)
async def edit_name_invalid(message: Message) -> None:
    await message.answer("Напишите ФИО текстовым сообщением.")


@router.message(EditForm.waiting_contact, F.contact)
async def edit_contact(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, settings: Settings
) -> None:
    contact = message.contact
    # Только собственный контакт, отправленный кнопкой, — как при оформлении.
    if contact.user_id != message.from_user.id:
        await message.answer(f"Это не ваш контакт. Нажмите кнопку «{CONTACT_BUTTON}».")
        return
    phone = contact.phone_number if contact.phone_number.startswith("+") else f"+{contact.phone_number}"
    await save_changes(message, state, session, bot, settings, message.from_user, {"phone": phone})


@router.message(EditForm.waiting_contact)
async def edit_contact_invalid(message: Message) -> None:
    await message.answer(
        f"Номер, введённый вручную, не принимается — нажмите кнопку «{CONTACT_BUTTON}»."
    )


@router.callback_query(EditOptionCallback.filter())
async def edit_option_stale(callback: CallbackQuery) -> None:
    # Кнопка варианта нажата не на своём шаге (после сохранения, отмены или в другом меню).
    await callback.answer("Эта кнопка уже неактуальна.")
