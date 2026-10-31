"""Переписка клиента и мастера внутри бота: одно сообщение — одно нажатие кнопки."""
import logging

from aiogram import Bot, F, Router, html
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.database.models import Order
from bot.handlers.admin import CLIENT_UNREACHABLE, IsAdmin, master_name
from bot.keyboards.chat import (
    CHAT_CANCEL_CALLBACK,
    GENERAL_QUESTION,
    ChatCallback,
    choose_order_kb,
    history_kb,
    to_client_kb,
    to_master_kb,
)
from bot.keyboards.menu import CONTACT_MASTER_TEXTS, main_menu_kb, master_link_kb
from bot.keyboards.orders import cancel_kb
from bot.services.chat import (
    HISTORY_LIMIT,
    content_error,
    content_summary,
    deliver,
    list_attachments,
    list_messages,
    message_content,
    count_messages,
    history_text,
    save_message,
)
from bot.services.formatting import local_time
from bot.services.history import log_event, log_general
from bot.services.orders import get_order, list_user_orders
from bot.states import ChatForm

logger = logging.getLogger(__name__)

# Вход из меню — в отдельном роутере, который подключается раньше роутера заказов:
# иначе посреди оформления заказа шаг «Напишите ФИО» принял бы кнопку меню за имя.
entry_router = Router(name="chat_entry")
# Остальная переписка — после роутера заказов, чтобы «Отмена» и /cancel срабатывали
# раньше, чем «принять любое сообщение и переслать».
router = Router(name="chat")

CLIENT_ORDERS_LIMIT = 10
UNSUPPORTED_CONTENT = "Можно отправить текст, фото или файл. Отмена — /cancel."


def telegram_link(user_id: int, username: str | None) -> str:
    if username:
        return f"@{html.quote(username)}"
    return f'<a href="tg://user?id={user_id}">профиль</a>'


async def client_order(session: AsyncSession, order_id: int, client_id: int) -> Order | None:
    """Заказ, только если он принадлежит этому клиенту.

    Номер в кнопке мог устареть после перенумерации — тогда вопрос считается общим,
    а не уходит в историю чужого заказа.
    """
    if order_id == GENERAL_QUESTION:
        return None
    order = await get_order(session, order_id)
    return order if order is not None and order.user_id == client_id else None


async def ask_client_message(
    message: Message, state: FSMContext, order_id: int, master_id: int | None
) -> None:
    await state.set_state(ChatForm.client_message)
    await state.update_data(order_id=order_id, master_id=master_id)
    topic = f"по заказу №{order_id}" if order_id != GENERAL_QUESTION else "(общий вопрос)"
    await message.answer(
        f"Напишите сообщение мастеру {topic} — текстом, фото или файлом.",
        reply_markup=cancel_kb(),
    )


# --- Клиент ---


@entry_router.message(F.text.in_(CONTACT_MASTER_TEXTS))
async def contact_master(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    # Начатое оформление заказа сбрасывается, как и при других кнопках меню заказа.
    await state.clear()
    if not settings.admin_ids:
        # Мастера в боте не настроены — даём запасной контакт, если он есть.
        username = (settings.master_username or "").lstrip("@")
        if username:
            await message.answer(
                f"Напишите мастеру напрямую: @{html.quote(username)}",
                reply_markup=master_link_kb(username),
            )
        else:
            logger.warning("ADMIN_IDS и MASTER_USERNAME не заданы — клиенту некому написать")
            await message.answer("Мастер пока недоступен. Попробуйте позже.")
        return

    orders = await list_user_orders(session, message.from_user.id, limit=CLIENT_ORDERS_LIMIT)
    if not orders:
        await ask_client_message(message, state, GENERAL_QUESTION, None)
        return
    await message.answer("По какому заказу вопрос?", reply_markup=choose_order_kb(orders))


@router.callback_query(ChatCallback.filter(F.action == "pick"))
async def pick_order(
    callback: CallbackQuery,
    callback_data: ChatCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    order = await client_order(session, callback_data.order_id, callback.from_user.id)
    order_id = order.id if order else GENERAL_QUESTION
    await callback.message.edit_reply_markup(reply_markup=None)
    await ask_client_message(callback.message, state, order_id, None)
    await callback.answer()


@router.callback_query(F.data == CHAT_CANCEL_CALLBACK)
async def chat_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(ChatCallback.filter(F.action == "client_reply"))
async def client_reply_start(
    callback: CallbackQuery,
    callback_data: ChatCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    order = await client_order(session, callback_data.order_id, callback.from_user.id)
    order_id = order.id if order else GENERAL_QUESTION
    # Ответ уйдёт именно тому мастеру, который написал (user_id в кнопке).
    await ask_client_message(callback.message, state, order_id, callback_data.user_id)
    await callback.answer()


@router.message(ChatForm.client_message)
async def client_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    content = message_content(message)
    if content is None:
        await message.answer(UNSUPPORTED_CONTENT)
        return
    kind, text, file_id = content
    if error := content_error(kind, text):
        await message.answer(error)
        return

    data = await state.get_data()
    await state.clear()
    client = message.from_user
    order = await client_order(session, data["order_id"], client.id)
    order_id = order.id if order else GENERAL_QUESTION

    # Ответ на сообщение мастера — только этому мастеру; новое сообщение — всем мастерам.
    # Если мастера уже нет в ADMIN_IDS, сообщение тоже уходит всем.
    master_id = data.get("master_id")
    recipients = [master_id] if master_id in settings.admin_ids else list(settings.admin_ids)
    if not recipients:
        await message.answer("Мастер пока недоступен. Попробуйте позже.", reply_markup=main_menu_kb())
        return

    name = order.customer_name if order else client.full_name
    who = f"{html.quote(name)}, {telegram_link(client.id, client.username)}"
    if order:
        header = f"💬 Клиент по заказу №{order.id} ({who})"
    else:
        header = f"💬 Вопрос от клиента без заказа ({who})"
    delivered = False
    for admin_id in recipients:
        # Недоступность одного мастера не мешает доставить остальным.
        if await deliver(bot, admin_id, kind, text, file_id, header, to_master_kb(order_id, client.id)):
            delivered = True

    if not delivered:
        await message.answer(
            "Не удалось доставить сообщение мастеру. Попробуйте позже.", reply_markup=main_menu_kb()
        )
        return
    summary = content_summary(kind, text)
    if order:
        await save_message(
            session,
            order_id=order.id,
            author_role="client",
            author_id=client.id,
            author_name=order.customer_name,
            kind=kind,
            text=text,
            file_id=file_id,
        )
        log_event(order, f"клиент → мастеру: {summary}")
    else:
        log_general(client.full_name, client.username, client.id, f"клиент → мастерам: {summary}")
    await message.answer(
        "Сообщение отправлено мастеру. Ответ придёт сюда.", reply_markup=main_menu_kb()
    )


# --- Мастер ---


@router.callback_query(IsAdmin(), ChatCallback.filter(F.action.in_({"master_reply", "write"})))
async def master_write_start(
    callback: CallbackQuery,
    callback_data: ChatCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    client_id = callback_data.user_id
    order = await client_order(session, callback_data.order_id, client_id)
    if callback_data.order_id != GENERAL_QUESTION and order is None:
        if callback_data.action == "write":
            # Кнопка из устаревшего списка/истории: номер уже принадлежит другому заказу.
            await callback.answer("Заказ изменился — обновите список: /orders", show_alert=True)
            return
        # Ответ на сообщение клиента: человек тот же, просто без привязки к заказу.
    order_id = order.id if order else GENERAL_QUESTION

    # clear(): убираем данные прошлых действий мастера (ввод суммы и т.п.).
    await state.clear()
    await state.set_state(ChatForm.master_message)
    await state.update_data(order_id=order_id, client_id=client_id)
    target = f"клиенту по заказу №{order.id} ({html.quote(order.customer_name)})" if order else "клиенту"
    await callback.message.answer(
        f"Напишите сообщение {target} — текстом, фото или файлом. Отмена — /cancel."
    )
    await callback.answer()


@router.message(IsAdmin(), ChatForm.master_message)
async def master_message(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    content = message_content(message)
    if content is None:
        await message.answer(UNSUPPORTED_CONTENT)
        return
    kind, text, file_id = content
    if error := content_error(kind, text):
        await message.answer(error)
        return

    data = await state.get_data()
    await state.clear()
    master: User = message.from_user
    client_id = data["client_id"]
    order = await client_order(session, data["order_id"], client_id)
    order_id = order.id if order else GENERAL_QUESTION

    header = f"💬 Мастер по заказу №{order.id}:" if order else "💬 Сообщение от мастера:"
    # Другим мастерам ничего не пересылаем — они увидят переписку в «💬 Уточнить».
    delivered = await deliver(
        bot, client_id, kind, text, file_id, header, to_client_kb(order_id, master.id)
    )
    if not delivered:
        await message.answer(CLIENT_UNREACHABLE)
        return
    summary = content_summary(kind, text)
    if order:
        await save_message(
            session,
            order_id=order.id,
            author_role="master",
            author_id=master.id,
            author_name=master_name(master),
            kind=kind,
            text=text,
            file_id=file_id,
        )
        log_event(order, f"мастер {master_name(master)} → клиенту: {summary}")
    else:
        log_general(
            "клиент", None, client_id, f"мастер {master_name(master)} → клиенту: {summary}"
        )
    await message.answer("Сообщение отправлено клиенту.")


@router.callback_query(IsAdmin(), ChatCallback.filter(F.action == "history"))
async def show_history(
    callback: CallbackQuery, callback_data: ChatCallback, session: AsyncSession
) -> None:
    order = await get_order(session, callback_data.order_id)
    if order is None:
        await callback.answer("Заказ не найден — обновите список: /orders", show_alert=True)
        return
    messages = await list_messages(session, order.id, HISTORY_LIMIT)
    total = (await count_messages(session, [order.id])).get(order.id, 0)
    attachments = await list_attachments(session, order.id)
    await callback.message.answer(
        history_text(order, messages, total), reply_markup=history_kb(order, len(attachments))
    )
    await callback.answer()


@router.callback_query(IsAdmin(), ChatCallback.filter(F.action == "files"))
async def show_attachments(
    callback: CallbackQuery, callback_data: ChatCallback, session: AsyncSession, bot: Bot
) -> None:
    attachments = await list_attachments(session, callback_data.order_id)
    if not attachments:
        await callback.answer("Вложений нет.", show_alert=True)
        return
    await callback.answer()
    for item in attachments:
        author = "Клиент" if item.author_role == "client" else item.author_name
        caption = f"№{item.order_id} · {html.quote(author)} · {local_time(item.created_at):%d.%m %H:%M}"
        if item.kind == "photo":
            await bot.send_photo(callback.message.chat.id, item.file_id, caption=caption)
        else:
            await bot.send_document(callback.message.chat.id, item.file_id, caption=caption)
