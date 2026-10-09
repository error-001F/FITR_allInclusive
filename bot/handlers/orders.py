import re
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, html
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from bot.catalog import (
    COLORS,
    INFILL_HINT,
    INFILL_SURCHARGES,
    LAYER_HINT,
    LAYER_PRICES,
    MATERIALS,
    PRINT_EXTENSIONS,
)
from bot.config import Settings
from bot.database.models import OrderType
from bot.keyboards.menu import DESIGN_BUTTON, PRINT_BUTTON, main_menu_kb
from bot.keyboards.orders import (
    CANCEL_BUTTON,
    CANCEL_CALLBACK,
    CONFIRM_CALLBACK,
    CONTACT_BUTTON,
    DONE_BUTTON,
    EDIT_CONTACTS_CALLBACK,
    ORDER_TYPE_TITLES,
    SKIP_BUTTON,
    PrintOptionCallback,
    cancel_kb,
    color_kb,
    confirm_kb,
    contact_kb,
    infill_kb,
    layer_kb,
    material_kb,
    references_kb,
)
from bot.services.notifications import notify_admins_new_order
from bot.services.orders import create_order, get_last_contacts
from bot.states import OrderForm

router = Router(name="orders")

EXTENSIONS_TEXT = ", ".join(PRINT_EXTENSIONS)
MIN_DESCRIPTION_LENGTH = 20
# Запас до лимита Telegram в 4096 символов: описание целиком попадает в сводку заказа.
MAX_DESCRIPTION_LENGTH = 3000
# ФИО: от 2 до 4 слов из букв; внутри слова допустимы дефис и апостроф («Анна-Мария», «О'Нил»).
NAME_WORD = r"[A-Za-zА-Яа-яЁё]+(?:[-'][A-Za-zА-Яа-яЁё]+)*"
NAME_PATTERN = re.compile(rf"{NAME_WORD}(?: {NAME_WORD}){{1,3}}")
MAX_NAME_LENGTH = 64  # длина колонки customer_name в БД
MAX_REFERENCES = 10
PRINT_ACCEPTED_TEXT = (
    "Модель принята. Скоро я закину её в слайсер, рассчитаю точный вес "
    "и напишу итоговую сумму."
)
DESIGN_ACCEPTED_TEXT = "ТЗ отправлено. Мастер скоро свяжется для оценки сложности."


def infill_text(infill: int) -> str:
    surcharge = INFILL_SURCHARGES[infill]
    return f"{infill}% (" + (f"+{surcharge} BYN" if surcharge else "без доплаты") + ")"


def print_params_lines(data: dict[str, Any]) -> list[str]:
    """Строки с уже выбранными параметрами печати — для промежуточных шагов и сводки."""
    lines = [f"Файл: {html.quote(data['file_name'])}"]
    if "material" in data:
        lines.append(f"Материал: {data['material']}")
    if "layer_height" in data:
        layer = data["layer_height"]
        lines.append(f"Толщина слоя: {layer} мм ({LAYER_PRICES[layer]} BYN/г)")
    if "infill" in data:
        lines.append(f"Заполнение: {infill_text(data['infill'])}")
    if "color" in data:
        lines.append(f"Цвет: {data['color']}")
    return lines


def format_order(data: dict[str, Any]) -> str:
    order_type = OrderType(data["order_type"])
    lines = [f"Тип заказа: {html.bold(ORDER_TYPE_TITLES[order_type])}"]
    if order_type == OrderType.PRINT:
        lines.extend(print_params_lines(data))
    else:
        lines.append(f"Описание: {html.quote(data['description'])}")
        count = len(data.get("attachments", []))
        lines.append(f"Референсы: {count} шт." if count else "Референсы: нет")
    lines.append(f"ФИО: {html.quote(data['customer_name'])}")
    lines.append(f"Телефон: {html.quote(data['phone'])}")
    if order_type == OrderType.PRINT:
        lines.append(
            "\nИтог = вес × цена за грамм + наценка за заполнение. "
            "Точный вес рассчитаю в слайсере."
        )
    return "\n".join(lines)


async def show_print_step(
    callback: CallbackQuery, state: FSMContext, prompt: str, reply_markup=None
) -> None:
    """Редактирует сообщение с кнопками: выбранные параметры + следующий вопрос."""
    data = await state.get_data()
    progress = "\n".join(print_params_lines(data))
    await callback.message.edit_text(f"{progress}\n\n{prompt}", reply_markup=reply_markup)


async def ask_name(message: Message, state: FSMContext) -> None:
    await state.set_state(OrderForm.waiting_name)
    await message.answer(
        "Напишите ваши ФИО (например: Иванов Иван Иванович).", reply_markup=cancel_kb()
    )


async def show_summary(message: Message, state: FSMContext) -> None:
    await state.set_state(OrderForm.confirming)
    data = await state.get_data()
    await message.answer(
        f"Проверьте заказ:\n\n{format_order(data)}", reply_markup=confirm_kb()
    )


async def ask_contacts(
    message: Message, state: FSMContext, session: AsyncSession, user_id: int
) -> None:
    """Последний шаг перед сводкой: постоянному клиенту подставляем ФИО и телефон
    из прошлого заказа, новому — спрашиваем."""
    contacts = await get_last_contacts(session, user_id)
    if contacts is None:
        await ask_name(message, state)
        return

    customer_name, phone = contacts
    await state.update_data(customer_name=customer_name, phone=phone)
    await message.answer(
        "Использую ваши данные из прошлого заказа.", reply_markup=ReplyKeyboardRemove()
    )
    await show_summary(message, state)


@router.message(Command("cancel"))
@router.message(F.text == CANCEL_BUTTON)
async def cancel_order(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu_kb())


@router.callback_query(F.data == CANCEL_CALLBACK)
async def cancel_order_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Заказ отменён.")
    await callback.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await callback.answer()


@router.message(F.text == PRINT_BUTTON)
async def start_print_order(message: Message, state: FSMContext) -> None:
    # clear() сбрасывает недооформленный заказ, если пользователь начал заново.
    await state.clear()
    await state.update_data(order_type=OrderType.PRINT.value)
    await state.set_state(OrderForm.waiting_file)
    await message.answer(
        f"Пришлите файл модели ({EXTENSIONS_TEXT}) как документ.",
        reply_markup=cancel_kb(),
    )


@router.message(F.text == DESIGN_BUTTON)
async def start_design_order(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(order_type=OrderType.DESIGN.value)
    await state.set_state(OrderForm.waiting_description)
    await message.answer(
        "Опишите задачу: что нужно сделать, примерные размеры, для чего деталь "
        "и желаемые сроки.",
        reply_markup=cancel_kb(),
    )


# --- Ветка печати: файл и параметры ---


@router.message(OrderForm.waiting_file, F.document)
async def receive_file(message: Message, state: FSMContext) -> None:
    file_name = message.document.file_name or ""
    if Path(file_name).suffix.lower() not in PRINT_EXTENSIONS:
        await message.answer(f"Нужен файл модели с расширением {EXTENSIONS_TEXT}.")
        return

    await state.update_data(file_id=message.document.file_id, file_name=file_name)
    await state.set_state(OrderForm.choosing_material)
    await message.answer(
        f"Файл получен: {html.quote(file_name)}\n\nВыберите материал:",
        reply_markup=material_kb(),
    )


@router.message(OrderForm.waiting_file)
async def receive_file_invalid(message: Message) -> None:
    await message.answer(f"Пришлите файл {EXTENSIONS_TEXT} как документ (скрепка → Файл).")


@router.callback_query(
    OrderForm.choosing_material, PrintOptionCallback.filter(F.field == "material")
)
async def choose_material(
    callback: CallbackQuery, callback_data: PrintOptionCallback, state: FSMContext
) -> None:
    # Значение из кнопки проверяем по каталогу: callback_data можно подделать.
    if callback_data.value not in MATERIALS:
        await callback.answer("Такого варианта нет.", show_alert=True)
        return
    await state.update_data(material=callback_data.value)
    await state.set_state(OrderForm.choosing_layer)
    await show_print_step(
        callback,
        state,
        f"Выберите качество печати (толщину слоя):\n\n{html.italic(LAYER_HINT)}",
        layer_kb(),
    )
    await callback.answer()


@router.callback_query(
    OrderForm.choosing_layer, PrintOptionCallback.filter(F.field == "layer")
)
async def choose_layer(
    callback: CallbackQuery, callback_data: PrintOptionCallback, state: FSMContext
) -> None:
    if callback_data.value not in LAYER_PRICES:
        await callback.answer("Такого варианта нет.", show_alert=True)
        return
    await state.update_data(layer_height=callback_data.value)
    await state.set_state(OrderForm.choosing_infill)
    await show_print_step(
        callback,
        state,
        f"Выберите плотность заполнения:\n\n{html.italic(INFILL_HINT)}",
        infill_kb(),
    )
    await callback.answer()


@router.callback_query(
    OrderForm.choosing_infill, PrintOptionCallback.filter(F.field == "infill")
)
async def choose_infill(
    callback: CallbackQuery, callback_data: PrintOptionCallback, state: FSMContext
) -> None:
    value = callback_data.value
    if not value.isdigit() or int(value) not in INFILL_SURCHARGES:
        await callback.answer("Такого варианта нет.", show_alert=True)
        return
    await state.update_data(infill=int(value))
    await state.set_state(OrderForm.choosing_color)
    await show_print_step(callback, state, "Выберите цвет пластика:", color_kb())
    await callback.answer()


@router.callback_query(
    OrderForm.choosing_color, PrintOptionCallback.filter(F.field == "color")
)
async def choose_color(
    callback: CallbackQuery,
    callback_data: PrintOptionCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if callback_data.value not in COLORS:
        await callback.answer("Такого варианта нет.", show_alert=True)
        return
    await state.update_data(color=callback_data.value)
    # Убираем кнопки: параметры выбраны, дальше — контактные данные.
    await show_print_step(callback, state, "Параметры печати выбраны.")
    # callback.message отправлено ботом, поэтому id клиента берём из callback.from_user.
    await ask_contacts(callback.message, state, session, callback.from_user.id)
    await callback.answer()


@router.message(
    StateFilter(
        OrderForm.choosing_material,
        OrderForm.choosing_layer,
        OrderForm.choosing_infill,
        OrderForm.choosing_color,
    )
)
async def choose_with_buttons(message: Message) -> None:
    await message.answer("Выберите вариант кнопкой под сообщением выше.")


# --- Ветка разработки ---


@router.message(OrderForm.waiting_description, F.text)
async def receive_description(message: Message, state: FSMContext) -> None:
    description = message.text.strip()
    if len(description) < MIN_DESCRIPTION_LENGTH:
        await message.answer(
            f"Слишком коротко — опишите модель подробнее "
            f"(минимум {MIN_DESCRIPTION_LENGTH} символов)."
        )
        return
    if len(description) > MAX_DESCRIPTION_LENGTH:
        await message.answer(
            f"Слишком длинное описание — сократите его до {MAX_DESCRIPTION_LENGTH} символов."
        )
        return

    await state.update_data(description=description, attachments=[])
    await state.set_state(OrderForm.waiting_references)
    await message.answer(
        "Пришлите фото, эскиз или чертёж — можно несколько "
        f"(до {MAX_REFERENCES}). Когда закончите, нажмите «{DONE_BUTTON}».\n"
        f"Если исходников нет — «{SKIP_BUTTON}».",
        reply_markup=references_kb(),
    )


@router.message(OrderForm.waiting_description)
async def receive_description_invalid(message: Message) -> None:
    await message.answer("Опишите модель текстовым сообщением.")


@router.message(OrderForm.waiting_references, F.photo | F.document)
async def receive_reference(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    attachments = list(data.get("attachments", []))

    # Альбом приходит отдельным сообщением на каждое фото с общим media_group_id.
    # Отвечаем только на первое фото альбома, чтобы не присылать N одинаковых ответов.
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
        # Telegram присылает фото в нескольких размерах, последний — самый большой.
        item = {"file_id": message.photo[-1].file_id, "kind": "photo", "file_name": None}
    else:
        item = {
            "file_id": message.document.file_id,
            "kind": "document",
            "file_name": message.document.file_name,
        }
    attachments.append(item)
    await state.update_data(attachments=attachments)

    if is_same_album:
        return
    received = "Файлы из альбома получены." if album_id else (
        f"Файл получен ({len(attachments)} из {MAX_REFERENCES})."
    )
    await message.answer(f"{received} Можно прислать ещё или нажать «{DONE_BUTTON}».")


@router.message(OrderForm.waiting_references, F.text.in_({DONE_BUTTON, SKIP_BUTTON}))
async def references_done(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    # «Готово» без файлов работает так же, как «Пропустить».
    await ask_contacts(message, state, session, message.from_user.id)


@router.message(OrderForm.waiting_references)
async def receive_reference_invalid(message: Message) -> None:
    await message.answer(
        f"Пришлите фото или файл, либо нажмите «{DONE_BUTTON}» / «{SKIP_BUTTON}»."
    )


# --- Общие шаги: ФИО, телефон, подтверждение ---


@router.message(OrderForm.waiting_name, F.text)
async def receive_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if len(name) > MAX_NAME_LENGTH or not NAME_PATTERN.fullmatch(name):
        await message.answer(
            "Напишите фамилию и имя (можно с отчеством) буквами, через пробел. "
            f"Допустимы дефис и апостроф, до {MAX_NAME_LENGTH} символов."
        )
        return

    await state.update_data(customer_name=name)
    await state.set_state(OrderForm.waiting_contact)
    await message.answer(
        f"Нажмите кнопку «{CONTACT_BUTTON}» ниже, чтобы мы могли с вами связаться.",
        reply_markup=contact_kb(),
    )


@router.message(OrderForm.waiting_name)
async def receive_name_invalid(message: Message) -> None:
    await message.answer("Напишите ФИО текстовым сообщением.")


@router.message(OrderForm.waiting_contact, F.contact)
async def receive_contact(message: Message, state: FSMContext) -> None:
    contact = message.contact
    # Принимаем только собственный контакт пользователя, отправленный кнопкой,
    # а не чужой контакт из записной книжки.
    if contact.user_id != message.from_user.id:
        await message.answer(f"Это не ваш контакт. Нажмите кнопку «{CONTACT_BUTTON}».")
        return

    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = f"+{phone}"
    await state.update_data(phone=phone)

    await message.answer("Номер получен.", reply_markup=ReplyKeyboardRemove())
    await show_summary(message, state)


@router.message(OrderForm.waiting_contact)
async def receive_contact_invalid(message: Message) -> None:
    await message.answer(
        f"Номер, введённый вручную, не принимается — нажмите кнопку «{CONTACT_BUTTON}»."
    )


@router.callback_query(OrderForm.confirming, F.data == EDIT_CONTACTS_CALLBACK)
async def edit_contacts(callback: CallbackQuery, state: FSMContext) -> None:
    # Убираем кнопки со старой сводки, чтобы её нельзя было подтвердить со старыми данными.
    await callback.message.edit_reply_markup(reply_markup=None)
    # Дальше обычный путь: ФИО → кнопка контакта (с проверкой, что контакт свой) → сводка.
    await ask_name(callback.message, state)
    await callback.answer()


@router.callback_query(OrderForm.confirming, F.data == CONFIRM_CALLBACK)
async def confirm_order(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    data = await state.get_data()
    # Очищаем состояние до записи в БД, чтобы повторное нажатие не создало дубль.
    await state.clear()

    order_type = OrderType(data["order_type"])
    order = await create_order(
        session,
        user_id=callback.from_user.id,
        # username собирается автоматически; у части пользователей его нет — тогда None.
        username=callback.from_user.username,
        order_type=order_type,
        customer_name=data["customer_name"],
        phone=data["phone"],
        file_id=data.get("file_id"),
        file_name=data.get("file_name"),
        material=data.get("material"),
        layer_height=data.get("layer_height"),
        infill=data.get("infill"),
        color=data.get("color"),
        description=data.get("description"),
        # Пустой список сохраняем как NULL: «референсов нет».
        attachments=data.get("attachments") or None,
    )

    await callback.message.edit_text(
        f"{format_order(data)}\n\n{html.bold(f'Заказ №{order.id} оформлен.')}"
    )
    if order_type == OrderType.PRINT:
        final_text = PRINT_ACCEPTED_TEXT
    else:
        final_text = DESIGN_ACCEPTED_TEXT
    await callback.message.answer(final_text, reply_markup=main_menu_kb())
    await callback.answer()
    # Мастера уведомляем после ответа клиенту: сбой отправки мастеру его не затронет.
    await notify_admins_new_order(bot, session, settings, order)


@router.callback_query(PrintOptionCallback.filter())
@router.callback_query(F.data.in_({CONFIRM_CALLBACK, EDIT_CONTACTS_CALLBACK}))
async def stale_button(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка уже неактуальна.")
