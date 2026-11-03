"""Оплата клиентом: реквизиты, смена способа (карта ↔ IBAN), отправка чека мастеру."""
import logging

from aiogram import Bot, F, Router, html
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.catalog import PAYMENT_METHOD_TITLES
from bot.config import Settings
from bot.database.models import Order, OrderStatus
from bot.keyboards.menu import MY_ORDERS_BUTTON, main_menu_kb
from bot.keyboards.orders import cancel_kb
from bot.keyboards.payment import PayCallback
from bot.services.chat import content_error, content_summary, deliver, message_content, save_message
from bot.services.history import log_event
from bot.services.notifications import refresh_admin_cards
from bot.services.orders import get_order, set_payment_method, update_order_status
from bot.services.payment import online_methods, payment_message
from bot.states import PaymentForm

logger = logging.getLogger(__name__)

# Подключается после роутера заказов: «Отмена» и /cancel срабатывают раньше приёма чека.
router = Router(name="payment")

PAYABLE = {OrderStatus.AWAITING_PAYMENT}
NOT_PAYABLE = f"Заказ уже оплачен или изменился — откройте «{MY_ORDERS_BUTTON}»."
UNSUPPORTED_CONTENT = "Пришлите скриншот, файл чека или текст. Отмена — /cancel."


async def payable_order(session: AsyncSession, order_id: int, client_id: int) -> Order | None:
    """Заказ, только если он этого клиента и ждёт оплаты.

    Кнопка могла устареть: заказ уже оплачен, отклонён или номер сдвинулся после перенумерации.
    """
    order = await get_order(session, order_id)
    if order is None or order.user_id != client_id or order.status not in PAYABLE:
        return None
    return order


@router.callback_query(PayCallback.filter(F.action == "show"))
async def show_requisites(
    callback: CallbackQuery,
    callback_data: PayCallback,
    session: AsyncSession,
    settings: Settings,
) -> None:
    order = await payable_order(session, callback_data.order_id, callback.from_user.id)
    if order is None:
        await callback.answer(NOT_PAYABLE, show_alert=True)
        return
    text, kb = payment_message(order, settings)
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(PayCallback.filter(F.action.in_({"card", "iban"})))
async def switch_method(
    callback: CallbackQuery,
    callback_data: PayCallback,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    order = await payable_order(session, callback_data.order_id, callback.from_user.id)
    if order is None:
        await callback.answer(NOT_PAYABLE, show_alert=True)
        return
    method = callback_data.action
    # Реквизиты могли убрать из .env после отправки кнопки.
    if method not in online_methods(settings):
        await callback.answer("Этот способ сейчас недоступен.", show_alert=True)
        return
    order = await set_payment_method(session, order.id, method, PAYABLE)
    if order is None:
        await callback.answer(NOT_PAYABLE, show_alert=True)
        return
    log_event(order, f"клиент сменил способ оплаты: {method}")
    text, kb = payment_message(order, settings, method)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()
    # Способ оплаты виден в карточке мастера.
    await refresh_admin_cards(bot, session, order)


@router.callback_query(PayCallback.filter(F.action == "proof"))
async def ask_proof(
    callback: CallbackQuery,
    callback_data: PayCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    order = await payable_order(session, callback_data.order_id, callback.from_user.id)
    if order is None:
        await callback.answer(NOT_PAYABLE, show_alert=True)
        return
    # clear(): сбрасываем недооформленный заказ или начатое сообщение мастеру.
    await state.clear()
    await state.set_state(PaymentForm.waiting_proof)
    await state.update_data(order_id=order.id)
    await callback.message.answer(
        f"Пришлите подтверждение оплаты заказа №{order.id}: скриншот, файл чека "
        "или текст с деталями платежа (время, сумма, последние цифры карты).",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(PaymentForm.waiting_proof)
async def receive_proof(
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
    order = await payable_order(session, data["order_id"], client.id)
    if order is not None:
        order = await update_order_status(
            session, order.id, OrderStatus.PAYMENT_CHECK, PAYABLE
        )
    if order is None:
        await message.answer(NOT_PAYABLE, reply_markup=main_menu_kb())
        return

    # Чек — часть переписки по заказу: виден мастеру в истории и в «📎 Вложения».
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
    log_event(order, f"клиент прислал чек: {content_summary(kind, text)}")

    method = PAYMENT_METHOD_TITLES.get(order.payment_method or "", "способ не указан")
    header = (
        f"🧾 Чек по заказу №{order.id} — {method}, {order.amount} BYN "
        f"({html.quote(order.customer_name)}).\nПроверьте поступление и подтвердите оплату "
        "в карточке заказа."
    )
    if not settings.admin_ids:
        logger.warning("ADMIN_IDS не задан в .env — чек по заказу №%s никто не увидит", order.id)
    for admin_id in settings.admin_ids:
        await deliver(bot, admin_id, kind, text, file_id, header)
    await refresh_admin_cards(bot, session, order)

    await message.answer(
        f"Чек по заказу №{order.id} отправлен мастеру. После проверки статус сменится "
        "на «💰 Оплачен».",
        reply_markup=main_menu_kb(),
    )
