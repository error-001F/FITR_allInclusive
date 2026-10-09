"""Действия мастера с заказом: счёт, отказ, уточнение, смена статусов."""
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router, html
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.catalog import PICKUP_ADDRESS
from bot.config import Settings
from bot.database.models import Order, OrderStatus
from bot.keyboards.admin import AdminOrderCallback
from bot.keyboards.menu import CONTACT_MASTER_BUTTON
from bot.services.notifications import notify_client, refresh_admin_cards, send_admin_card
from bot.services.orders import get_order, update_order_status
from bot.states import AdminForm

logger = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    """Пропускает только мастеров из ADMIN_IDS. Если список пуст — не пропускает никого."""

    async def __call__(self, event: Message | CallbackQuery, settings: Settings) -> bool:
        return event.from_user is not None and event.from_user.id in settings.admin_ids


router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

MAX_AMOUNT = Decimal("100000")
MAX_TEXT_LENGTH = 1000

# Из каких статусов допустимо каждое действие.
INVOICE_FROM = {OrderStatus.NEW, OrderStatus.AWAITING_PAYMENT}
PAID_FROM = {OrderStatus.AWAITING_PAYMENT}
READY_FROM = {OrderStatus.PRINTING}
REJECT_FROM = {OrderStatus.NEW, OrderStatus.AWAITING_PAYMENT, OrderStatus.PRINTING}

CLIENT_UNREACHABLE = (
    "⚠️ Клиент не получил сообщение (возможно, заблокировал бота). "
    "Свяжитесь с ним по телефону из карточки."
)


def parse_amount(text: str) -> Decimal | None:
    """«12.5», «12,50», «12 BYN» → Decimal('12.50'); некорректный ввод → None."""
    cleaned = text.lower().replace("byn", "").replace(",", ".").replace(" ", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    # is_finite отсекает NaN и Infinity, которые Decimal тоже умеет разбирать.
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        return None
    return amount.quantize(Decimal("0.01"))


def pressed_card(callback: CallbackQuery) -> tuple[int, int]:
    return callback.message.chat.id, callback.message.message_id


async def answer_stale(
    callback: CallbackQuery, session: AsyncSession, order: Order | None
) -> None:
    if order is None:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    # Обычно это значит, что заказ уже изменил другой мастер.
    await callback.answer("Статус заказа уже изменился — карточка обновлена.", show_alert=True)
    await refresh_admin_cards(callback.bot, session, order, pressed_card(callback))


async def start_text_input(
    callback: CallbackQuery, state: FSMContext, order: Order, new_state, prompt: str
) -> None:
    """Переводит мастера в режим ввода текста для заказа и запоминает карточку."""
    await state.set_state(new_state)
    await state.update_data(
        order_id=order.id,
        card_chat_id=callback.message.chat.id,
        card_message_id=callback.message.message_id,
    )
    await callback.message.answer(f"{prompt}\n\nОтмена — /cancel.")
    await callback.answer()


# --- Кнопки карточки ---


@router.callback_query(AdminOrderCallback.filter(F.action == "invoice"))
async def invoice_start(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    order = await get_order(session, callback_data.order_id)
    if order is None or order.status not in INVOICE_FROM:
        await answer_stale(callback, session, order)
        return
    await start_text_input(
        callback,
        state,
        order,
        AdminForm.waiting_amount,
        f"Введите итоговую сумму по заказу №{order.id} в BYN (например, 12.50).",
    )


@router.callback_query(AdminOrderCallback.filter(F.action == "reject"))
async def reject_start(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    order = await get_order(session, callback_data.order_id)
    if order is None or order.status not in REJECT_FROM:
        await answer_stale(callback, session, order)
        return
    await start_text_input(
        callback,
        state,
        order,
        AdminForm.waiting_reject_reason,
        f"Напишите причину отказа по заказу №{order.id} — её получит клиент.",
    )


@router.callback_query(AdminOrderCallback.filter(F.action == "ask"))
async def ask_start(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    order = await get_order(session, callback_data.order_id)
    if order is None:
        await answer_stale(callback, session, order)
        return
    await start_text_input(
        callback,
        state,
        order,
        AdminForm.waiting_question,
        f"Напишите сообщение клиенту по заказу №{order.id}.",
    )


@router.callback_query(AdminOrderCallback.filter(F.action == "paid"))
async def mark_paid(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    session: AsyncSession,
    bot: Bot,
) -> None:
    order = await update_order_status(
        session, callback_data.order_id, OrderStatus.PRINTING, PAID_FROM
    )
    if order is None:
        await answer_stale(callback, session, await get_order(session, callback_data.order_id))
        return
    await refresh_admin_cards(bot, session, order, pressed_card(callback))
    delivered = await notify_client(
        bot, order, f"Оплата по заказу №{order.id} получена. Заказ передан в работу 🖨"
    )
    await callback.answer("Статус: печатается")
    if not delivered:
        await callback.message.answer(CLIENT_UNREACHABLE)


@router.callback_query(AdminOrderCallback.filter(F.action == "ready"))
async def mark_ready(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    session: AsyncSession,
    bot: Bot,
) -> None:
    order = await update_order_status(
        session, callback_data.order_id, OrderStatus.READY, READY_FROM
    )
    if order is None:
        await answer_stale(callback, session, await get_order(session, callback_data.order_id))
        return
    await refresh_admin_cards(bot, session, order, pressed_card(callback))
    text = f"Заказ №{order.id} готов к выдаче ✅"
    if PICKUP_ADDRESS:
        text += f"\nАдрес самовывоза: {html.quote(PICKUP_ADDRESS)}."
    text += "\nО способе получения договоритесь с мастером."
    delivered = await notify_client(bot, order, text)
    await callback.answer("Статус: готов к выдаче")
    if not delivered:
        await callback.message.answer(CLIENT_UNREACHABLE)


# --- Ввод текста мастером ---


@router.message(AdminForm.waiting_amount, F.text)
async def invoice_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(
            f"Не понял сумму. Введите число больше 0 и не больше {MAX_AMOUNT}, "
            "например 12.50. Отмена — /cancel."
        )
        return

    data = await state.get_data()
    await state.clear()
    order = await update_order_status(
        session, data["order_id"], OrderStatus.AWAITING_PAYMENT, INVOICE_FROM, amount=amount
    )
    if order is None:
        await message.answer("Статус заказа уже изменился — счёт не выставлен.")
        return

    if settings.payment_details:
        payment = html.quote(settings.payment_details)
    else:
        logger.warning("PAYMENT_DETAILS не задан в .env — клиент получил счёт без реквизитов")
        payment = "реквизиты мастер пришлёт отдельным сообщением."
    delivered = await notify_client(
        bot,
        order,
        f"Заказ №{order.id}: итоговая сумма {html.bold(f'{order.amount} BYN')}.\n\n"
        f"Реквизиты для оплаты:\n{payment}\n\n"
        "После оплаты мастер возьмёт заказ в работу.",
    )
    await refresh_admin_cards(
        bot, session, order, (data["card_chat_id"], data["card_message_id"])
    )
    if delivered:
        await message.answer(f"Счёт по заказу №{order.id} на {order.amount} BYN отправлен клиенту.")
    else:
        await message.answer(CLIENT_UNREACHABLE)


@router.message(AdminForm.waiting_reject_reason, F.text)
async def reject_reason(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    reason = message.text.strip()
    if len(reason) > MAX_TEXT_LENGTH:
        await message.answer(f"Слишком длинно — сократите до {MAX_TEXT_LENGTH} символов.")
        return

    data = await state.get_data()
    await state.clear()
    order = await update_order_status(
        session, data["order_id"], OrderStatus.REJECTED, REJECT_FROM
    )
    if order is None:
        await message.answer("Статус заказа уже изменился — заказ не отклонён.")
        return

    delivered = await notify_client(
        bot,
        order,
        f"К сожалению, заказ №{order.id} отклонён мастером.\n"
        f"Причина: {html.quote(reason)}\n\n"
        f"Если есть вопросы — нажмите «{CONTACT_MASTER_BUTTON}».",
    )
    await refresh_admin_cards(
        bot, session, order, (data["card_chat_id"], data["card_message_id"])
    )
    await message.answer(
        f"Заказ №{order.id} отклонён, клиент уведомлён." if delivered else CLIENT_UNREACHABLE
    )


@router.message(AdminForm.waiting_question, F.text)
async def send_question(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    question = message.text.strip()
    if len(question) > MAX_TEXT_LENGTH:
        await message.answer(f"Слишком длинно — сократите до {MAX_TEXT_LENGTH} символов.")
        return

    data = await state.get_data()
    await state.clear()
    order = await get_order(session, data["order_id"])
    if order is None:
        await message.answer("Заказ не найден.")
        return

    delivered = await notify_client(
        bot,
        order,
        f"Мастер по заказу №{order.id}:\n\n{html.quote(question)}\n\n"
        f"Ответить можно через кнопку «{CONTACT_MASTER_BUTTON}».",
    )
    await message.answer("Сообщение отправлено клиенту." if delivered else CLIENT_UNREACHABLE)


@router.message(AdminForm.waiting_amount)
@router.message(AdminForm.waiting_reject_reason)
@router.message(AdminForm.waiting_question)
async def admin_text_expected(message: Message) -> None:
    await message.answer("Нужен текст сообщением. Отмена — /cancel.")


# --- Команды ---


@router.message(Command("order"))
async def cmd_order(
    message: Message, command: CommandObject, session: AsyncSession, bot: Bot
) -> None:
    """/order N — прислать карточку заказа заново (старые заказы, потерянные карточки)."""
    args = (command.args or "").strip()
    if not args.isdigit():
        await message.answer("Использование: /order <номер заказа>, например /order 5")
        return
    order = await get_order(session, int(args))
    if order is None:
        await message.answer(f"Заказ №{args} не найден.")
        return
    await send_admin_card(bot, session, message.chat.id, order)
