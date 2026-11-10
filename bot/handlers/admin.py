"""Действия мастера с заказом: счёт, отказ, уточнение, смена статусов."""
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aiogram import Bot, F, Router, html
from aiogram.filters import BaseFilter, Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from bot.catalog import (
    CASH,
    COLORS,
    INFILL_SURCHARGES,
    LAYER_PRICES,
    MATERIALS,
    MAX_WEIGHT_GRAMS,
    PICKUP_ADDRESS,
    PRINT_EXTENSIONS,
    PROMO_FREE,
    PROMO_HALF,
    PROMO_TITLES,
    SELF_PICKUP_METHOD,
    apply_promo,
    PriceCalc,
    calculate_print_price,
)
from bot.config import Settings
from bot.database.models import Order, OrderStatus, OrderType
from bot.keyboards.admin import (
    ADMIN_CANCEL_CALLBACK,
    ADMIN_SKIP_FILE_CALLBACK,
    AdminOrderCallback,
    AdminPrintCallback,
    admin_order_kb,
    issue_confirm_kb,
    model_file_kb,
    orders_list_kb,
    price_calc_kb,
)
from bot.keyboards.menu import CONTACT_MASTER_BUTTON, MY_ORDERS_BUTTON
from bot.keyboards.orders import color_kb, infill_kb, layer_kb, material_kb
from bot.keyboards.payment import pay_kb
from bot.services.activity import activity_text
from bot.services.chat import count_messages
from bot.services.formatting import format_weight, order_list_line
from bot.services.history import describe_order, log_event
from bot.services.maintenance import renumber_after_delete
from bot.services.notifications import (
    close_admin_cards,
    notify_client,
    refresh_admin_cards,
    send_admin_card,
)
from bot.services.payment import payment_message
from bot.services.promo import release_promo
from bot.services.orders import (
    count_orders,
    delete_order,
    get_order,
    list_orders,
    set_pickup_address,
    update_order_status,
    update_print_params,
)
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
MIN_PICKUP_ADDRESS_LENGTH = 5
MAX_PICKUP_ADDRESS_LENGTH = 255  # длина колонки pickup_point

# Из каких статусов допустимо каждое действие.
INVOICE_FROM = {OrderStatus.NEW, OrderStatus.AWAITING_PAYMENT}
# «Оплата получена» — и по чеку, и без него (клиент заплатил, но чек не прислал).
PAID_FROM = {OrderStatus.AWAITING_PAYMENT, OrderStatus.PAYMENT_CHECK}
PAY_REJECT_FROM = {OrderStatus.PAYMENT_CHECK}
PRINT_FROM = {OrderStatus.PAID}
READY_FROM = {OrderStatus.PRINTING}
REJECT_FROM = {
    OrderStatus.NEW,
    OrderStatus.AWAITING_PAYMENT,
    OrderStatus.PAYMENT_CHECK,
    OrderStatus.PAID,
    OrderStatus.PRINTING,
}
# Заказы «в работе» для /orders без аргументов.
# Готовые к выдаче тоже «в работе»: их ещё нужно выдать (кнопка 📬 прямо в списке).
ACTIVE_STATUSES = REJECT_FROM | {OrderStatus.READY}
# Сколько заказов показывать в /orders: 30 строк укладываются в лимит Telegram (4096 символов).
ORDERS_LIST_LIMIT = 30
# Параметры печати можно менять только до оплаты.
PARAMS_FROM = {OrderStatus.NEW, OrderStatus.AWAITING_PAYMENT}

CLIENT_UNREACHABLE = (
    "⚠️ Клиент не получил сообщение (возможно, заблокировал бота). "
    "Свяжитесь с ним по телефону из карточки."
)


def parse_amount(text: str, allow_zero: bool = False) -> Decimal | None:
    """«12.5», «12,50», «12 BYN» → Decimal('12.50'); некорректный ввод → None.

    allow_zero — для заказа с сертификатом на бесплатную печать сумма может быть 0.
    """
    cleaned = text.lower().replace("byn", "").replace(",", ".").replace(" ", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    # is_finite отсекает NaN и Infinity, которые Decimal тоже умеет разбирать.
    if not amount.is_finite() or amount < 0 or amount > MAX_AMOUNT:
        return None
    if amount == 0 and not allow_zero:
        return None
    return amount.quantize(Decimal("0.01"))


def parse_weight(text: str) -> Decimal | None:
    """«124», «124,5», «124 г» → Decimal('124.0'); некорректный ввод → None."""
    cleaned = text.lower()
    for unit in ("граммов", "грамм", "гр", "г"):
        cleaned = cleaned.replace(unit, "")
    cleaned = cleaned.replace(",", ".").replace(" ", "")
    try:
        weight = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not weight.is_finite() or weight <= 0 or weight > MAX_WEIGHT_GRAMS:
        return None
    return weight.quantize(Decimal("0.1"))


def has_print_params(order: Order) -> bool:
    """Можно ли посчитать цену: слой и заполнение заданы и есть в текущем прайсе."""
    return order.layer_height in LAYER_PRICES and order.infill in INFILL_SURCHARGES


def price_calc_text(order: Order, calc: PriceCalc, total: Decimal) -> str:
    """total — итог к оплате (с учётом промокода)."""
    lines = [
        html.bold(f"Расчёт по заказу №{order.id}:"),
        f"{format_weight(calc.weight)} г × {calc.price_per_gram} BYN/г "
        f"(слой {order.layer_height} мм) = {calc.print_cost} BYN",
        f"Заполнение {order.infill}%: +{calc.surcharge} BYN",
    ]
    if calc.total == calc.exact:
        lines.append(html.bold(f"Итого: {calc.total} BYN"))
    else:
        lines.append(html.bold(f"Итого: {calc.exact} → {calc.total} BYN") + " (округление до 10 коп. вверх)")
    if order.promo_kind and total != calc.total:
        lines.append(
            html.bold(f"Промокод {PROMO_TITLES.get(order.promo_kind, order.promo_kind)}: "
                      f"{calc.total} → {total} BYN")
        )
    if order.order_type == OrderType.DESIGN:
        lines.append("\nЭто только печать — стоимость разработки добавьте через «✏️ Своя сумма».")
    return "\n".join(lines)


def promo_hint(order: Order, manual: bool) -> str:
    """Строка-напоминание мастеру о промокоде при вводе веса или суммы."""
    if order.promo_kind == PROMO_HALF:
        if manual:
            return "\n\n🎟 Промокод: скидка 50%. Введите ПОЛНУЮ сумму — бот сам разделит её пополам."
        return "\n\n🎟 Промокод: скидка 50% — бот учтёт её в расчёте."
    if order.promo_kind == PROMO_FREE:
        if manual:
            return (
                f"\n\nСертификат {PROMO_TITLES[PROMO_FREE]} — учтите это в сумме сами "
                "(можно ввести 0)."
            )
        return f"\n\nСертификат {PROMO_TITLES[PROMO_FREE]} — бот учтёт это в расчёте."
    return ""


def master_name(user: User) -> str:
    """Кто из мастеров выполнил действие — для журнала истории."""
    return f"@{user.username}" if user.username else user.full_name


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
    if has_print_params(order):
        # Слой и заполнение известны — не хватает только веса, его даёт слайсер.
        await start_text_input(
            callback,
            state,
            order,
            AdminForm.waiting_weight,
            f"Введите вес модели из слайсера по заказу №{order.id}, г (например, 124) — "
            f"бот рассчитает сумму.{promo_hint(order, manual=False)}",
        )
    else:
        await start_text_input(
            callback,
            state,
            order,
            AdminForm.waiting_amount,
            f"Введите итоговую сумму по заказу №{order.id} в BYN (например, 12.50)."
            f"{promo_hint(order, manual=True)}",
        )


@router.callback_query(AdminForm.confirming_amount, AdminOrderCallback.filter(F.action == "invoice_ok"))
async def invoice_ok(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    data = await state.get_data()
    if data.get("order_id") != callback_data.order_id:
        await callback.answer("Эта кнопка уже неактуальна.")
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await send_invoice(
        callback.message, state, session, bot, settings, callback.from_user, Decimal(data["calc_total"])
    )
    await callback.answer()


@router.callback_query(
    AdminForm.confirming_amount,
    AdminOrderCallback.filter(F.action.in_({"invoice_manual", "invoice_weight"})),
)
async def invoice_change(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    if data.get("order_id") != callback_data.order_id:
        await callback.answer("Эта кнопка уже неактуальна.")
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    if callback_data.action == "invoice_manual":
        await state.set_state(AdminForm.waiting_amount)
        order = await get_order(session, callback_data.order_id)
        hint = promo_hint(order, manual=True) if order else ""
        prompt = f"Введите свою сумму по заказу №{callback_data.order_id} в BYN (например, 45).{hint}"
    else:
        await state.set_state(AdminForm.waiting_weight)
        prompt = f"Введите другой вес по заказу №{callback_data.order_id}, г."
    await callback.message.answer(f"{prompt}\n\nОтмена — /cancel.")
    await callback.answer()


@router.callback_query(
    AdminOrderCallback.filter(F.action.in_({"invoice_ok", "invoice_manual", "invoice_weight"}))
)
async def invoice_stale(callback: CallbackQuery) -> None:
    # Кнопка расчёта нажата не на своём шаге: счёт уже выставлен, отменён или начат заново.
    await callback.answer("Эта кнопка уже неактуальна.")


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


@router.callback_query(AdminOrderCallback.filter(F.action == "pickup"))
async def pickup_start(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    order = await get_order(session, callback_data.order_id)
    if (
        order is None
        or order.delivery_method != SELF_PICKUP_METHOD
        or order.status == OrderStatus.REJECTED
    ):
        await answer_stale(callback, session, order)
        return
    current = f"сейчас: {html.quote(order.pickup_point)}" if order.pickup_point else "сейчас не указан"
    await start_text_input(
        callback,
        state,
        order,
        AdminForm.waiting_pickup_address,
        f"Напишите адрес самовывоза для заказа №{order.id} ({current}).\n"
        "Он появится в карточке заказа и у клиента в «📦 Мои заказы».",
    )


@router.callback_query(AdminOrderCallback.filter(F.action == "paid"))
async def mark_paid(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    session: AsyncSession,
    bot: Bot,
) -> None:
    order = await update_order_status(session, callback_data.order_id, OrderStatus.PAID, PAID_FROM)
    if order is None:
        await answer_stale(callback, session, await get_order(session, callback_data.order_id))
        return
    log_event(order, f"оплата подтверждена — мастер {master_name(callback.from_user)}")
    await refresh_admin_cards(bot, session, order, pressed_card(callback))
    delivered = await notify_client(
        bot,
        order,
        f"Оплата по заказу №{order.id} подтверждена ✅ Скоро мастер возьмёт заказ в работу.",
    )
    await callback.answer("Статус: оплачен")
    if not delivered:
        await callback.message.answer(CLIENT_UNREACHABLE)


@router.callback_query(AdminOrderCallback.filter(F.action == "pay_reject"))
async def payment_not_found(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    session: AsyncSession,
    bot: Bot,
) -> None:
    order = await update_order_status(
        session, callback_data.order_id, OrderStatus.AWAITING_PAYMENT, PAY_REJECT_FROM
    )
    if order is None:
        await answer_stale(callback, session, await get_order(session, callback_data.order_id))
        return
    log_event(order, f"оплата не найдена, снова ждёт оплаты — мастер {master_name(callback.from_user)}")
    await refresh_admin_cards(bot, session, order, pressed_card(callback))
    delivered = await notify_client(
        bot,
        order,
        f"Мастер не нашёл оплату по заказу №{order.id}. Проверьте платёж и отправьте "
        f"чек ещё раз. Если есть вопросы — нажмите «{CONTACT_MASTER_BUTTON}».",
        reply_markup=pay_kb(order),
    )
    await callback.answer("Статус: ожидает оплаты")
    if not delivered:
        await callback.message.answer(CLIENT_UNREACHABLE)


@router.callback_query(AdminOrderCallback.filter(F.action == "print"))
async def start_printing(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    session: AsyncSession,
    bot: Bot,
) -> None:
    order = await update_order_status(
        session, callback_data.order_id, OrderStatus.PRINTING, PRINT_FROM
    )
    if order is None:
        await answer_stale(callback, session, await get_order(session, callback_data.order_id))
        return
    log_event(order, f"передан в печать — мастер {master_name(callback.from_user)}")
    await refresh_admin_cards(bot, session, order, pressed_card(callback))
    delivered = await notify_client(bot, order, f"Заказ №{order.id} передан в печать 🖨")
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
    log_event(order, f"готов к выдаче — мастер {master_name(callback.from_user)}")
    await refresh_admin_cards(bot, session, order, pressed_card(callback))
    text = f"Заказ №{order.id} готов к выдаче ✅"
    # Адрес, который мастер вписал в заказ, важнее общего «договорного».
    if order.delivery_method == SELF_PICKUP_METHOD and order.pickup_point:
        text += f"\nАдрес самовывоза: {html.quote(order.pickup_point)}."
    elif PICKUP_ADDRESS:
        text += f"\nАдрес самовывоза: {html.quote(PICKUP_ADDRESS)}."
    if order.payment_method == CASH and order.amount:
        text += f"\nОплата при получении: {html.bold(f'{order.amount} BYN')} наличными."
    text += "\nО способе получения договоритесь с мастером."
    delivered = await notify_client(bot, order, text)
    await callback.answer("Статус: готов к выдаче")
    if not delivered:
        await callback.message.answer(CLIENT_UNREACHABLE)


# --- Ввод текста мастером ---


# ~F.text.startswith("/"): команды (/order, /orders) на шагах ввода не считаются вводом.
@router.message(AdminForm.waiting_weight, F.text, ~F.text.startswith("/"))
async def invoice_weight(message: Message, state: FSMContext, session: AsyncSession) -> None:
    weight = parse_weight(message.text)
    if weight is None:
        await message.answer(
            f"Не понял вес. Введите число граммов больше 0 и не больше {MAX_WEIGHT_GRAMS}, "
            "например 124. Отмена — /cancel."
        )
        return

    data = await state.get_data()
    order = await get_order(session, data["order_id"])
    if order is None or order.status not in INVOICE_FROM:
        await state.clear()
        await message.answer("Статус заказа уже изменился — счёт не выставлен.")
        return
    if not has_print_params(order):
        # Параметры печати могли убрать или поменять, пока мастер смотрел в слайсер.
        await state.set_state(AdminForm.waiting_amount)
        await message.answer(
            "У заказа нет параметров печати для расчёта — введите сумму в BYN. Отмена — /cancel."
        )
        return

    calc = calculate_print_price(weight, order.layer_height, order.infill)
    total = apply_promo(order.promo_kind, calc.total, calc)
    await state.update_data(weight=str(weight), calc_total=str(total), full_total=str(calc.total))
    await state.set_state(AdminForm.confirming_amount)
    await message.answer(price_calc_text(order, calc, total), reply_markup=price_calc_kb(order, total))


@router.message(AdminForm.waiting_amount, F.text, ~F.text.startswith("/"))
async def invoice_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    data = await state.get_data()
    order = await get_order(session, data["order_id"])
    promo = order.promo_kind if order else None
    amount = parse_amount(message.text, allow_zero=promo == PROMO_FREE)
    if amount is None:
        await message.answer(
            f"Не понял сумму. Введите число больше 0 и не больше {MAX_AMOUNT}, "
            "например 12.50. Отмена — /cancel."
        )
        return
    # Скидку 50% бот применяет к полной сумме сам; бесплатную печать мастер учёл в сумме.
    await state.update_data(full_total=str(amount))
    if promo == PROMO_HALF:
        amount = apply_promo(promo, amount)
    await send_invoice(message, state, session, bot, settings, message.from_user, amount)


async def send_invoice(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
    master: User,
    amount: Decimal,
) -> None:
    """Выставляет счёт: сумма и реквизиты клиенту, карточки, журнал.

    Оплата картой или по IBAN — «Ожидает оплаты» до чека клиента. Наличными — этапа
    оплаты нет: заказ сразу уходит в печать, клиент платит при получении.
    """
    data = await state.get_data()
    await state.clear()
    weight = Decimal(data["weight"]) if data.get("weight") else None
    current = await get_order(session, data["order_id"])
    cash = current is not None and current.payment_method == CASH
    # Бесплатно по сертификату — платить нечего: как с наличными, сразу в печать.
    free = amount == 0
    order = await update_order_status(
        session,
        data["order_id"],
        OrderStatus.PRINTING if cash or free else OrderStatus.AWAITING_PAYMENT,
        INVOICE_FROM,
        amount=amount,
        # Вес храним, если мастер его вводил (даже если сумму поставил свою).
        weight_grams=weight,
    )
    if order is None:
        await message.answer("Статус заказа уже изменился — счёт не выставлен.")
        return

    # Сумма «по расчёту», только если она совпадает с формулой для текущих параметров заказа.
    calc = None
    if weight is not None and has_print_params(order):
        calc = calculate_print_price(weight, order.layer_height, order.infill)
        if apply_promo(order.promo_kind, calc.total, calc) != amount:
            calc = None
    if calc:
        how = (
            f"расчёт: {format_weight(weight)} г, слой {order.layer_height} мм, "
            f"заполнение {order.infill}%"
        )
    else:
        how = "сумма мастера"
    full = Decimal(data["full_total"]) if data.get("full_total") else amount
    promo_title = PROMO_TITLES.get(order.promo_kind or "", "")
    if order.promo_kind and full != amount:
        how += f"; промокод {order.promo_code}: {full} → {amount} BYN"
    if free:
        paid_how = "бесплатно по сертификату, сразу в печать"
    elif cash:
        paid_how = "оплата наличными при получении, сразу в печать"
    else:
        paid_how = "ждёт оплаты"
    log_event(
        order,
        f"счёт выставлен: {order.amount} BYN ({how}), {paid_how} — мастер {master_name(master)}",
    )

    calc_line = ""
    if calc:
        calc_line = f"\nРасчёт: {format_weight(weight)} г × {calc.price_per_gram} BYN/г"
        if calc.surcharge:
            calc_line += f" + {calc.surcharge} BYN за заполнение"
    if order.promo_kind and full != amount:
        calc_line += f"\nПромокод: {promo_title} (без скидки {full} BYN)"
    if free:
        delivered = await notify_client(
            bot,
            order,
            f"Мастер проверил заказ №{order.id}: по сертификату он {html.bold('бесплатный')} "
            f"({promo_title}).{calc_line}\n\nЗаказ передан в печать 🖨",
        )
    elif cash:
        delivered = await notify_client(
            bot,
            order,
            f"Мастер проверил заказ №{order.id}: итоговая сумма "
            f"{html.bold(f'{order.amount} BYN')}.{calc_line}\n\n"
            "Оплата наличными при получении. Заказ передан в печать 🖨",
        )
    else:
        requisites, kb = payment_message(order, settings)
        delivered = await notify_client(
            bot,
            order,
            f"Мастер проверил заказ №{order.id} и выставил счёт.{calc_line}\n\n{requisites}",
            reply_markup=kb,
        )
    await refresh_admin_cards(
        bot, session, order, (data["card_chat_id"], data["card_message_id"])
    )
    if not delivered:
        await message.answer(CLIENT_UNREACHABLE)
    elif free:
        await message.answer(f"Заказ №{order.id} бесплатный по сертификату и передан в печать.")
    elif cash:
        await message.answer(
            f"Заказ №{order.id} подтверждён на {order.amount} BYN: оплата наличными "
            "при получении, заказ в печати."
        )
    else:
        await message.answer(f"Счёт по заказу №{order.id} на {order.amount} BYN отправлен клиенту.")


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
    log_event(order, f"отклонён: {reason} — мастер {master_name(message.from_user)}")
    promo_note = ""
    if order.promo_code:
        # Клиент сертификатом не воспользовался — код снова действует.
        await release_promo(session, order.promo_code)
        log_event(order, f"промокод {order.promo_code} снова действует (заказ отклонён)")
        promo_note = "\nПромокод с сертификата снова действует — его можно ввести в новом заказе."

    delivered = await notify_client(
        bot,
        order,
        f"К сожалению, заказ №{order.id} отклонён мастером.\n"
        f"Причина: {html.quote(reason)}\n{promo_note}\n"
        f"Если есть вопросы — нажмите «{CONTACT_MASTER_BUTTON}».",
    )
    await refresh_admin_cards(
        bot, session, order, (data["card_chat_id"], data["card_message_id"])
    )
    await message.answer(
        f"Заказ №{order.id} отклонён, клиент уведомлён." if delivered else CLIENT_UNREACHABLE
    )


@router.message(AdminForm.waiting_pickup_address, F.text, ~F.text.startswith("/"))
async def pickup_address(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    address = " ".join(message.text.split())
    if len(address) < MIN_PICKUP_ADDRESS_LENGTH:
        await message.answer("Слишком коротко — напишите адрес полностью. Отмена — /cancel.")
        return
    if len(address) > MAX_PICKUP_ADDRESS_LENGTH:
        await message.answer(f"Слишком длинно — сократите до {MAX_PICKUP_ADDRESS_LENGTH} символов.")
        return

    data = await state.get_data()
    await state.clear()
    order = await set_pickup_address(session, data["order_id"], address)
    if order is None:
        await message.answer("Заказ отклонён или клиент сменил доставку — адрес не сохранён.")
        return
    log_event(order, f"адрес самовывоза: {address} — мастер {master_name(message.from_user)}")
    await refresh_admin_cards(
        bot, session, order, (data["card_chat_id"], data["card_message_id"])
    )
    await message.answer(f"Адрес самовывоза по заказу №{order.id} сохранён.")


@router.message(AdminForm.waiting_amount, ~F.text.startswith("/"))
@router.message(AdminForm.waiting_weight, ~F.text.startswith("/"))
@router.message(AdminForm.waiting_reject_reason)
@router.message(AdminForm.waiting_pickup_address, ~F.text.startswith("/"))
async def admin_text_expected(message: Message) -> None:
    await message.answer("Нужен текст сообщением. Отмена — /cancel.")


@router.message(AdminForm.confirming_amount, ~F.text.startswith("/"))
async def confirming_amount_expected(message: Message) -> None:
    await message.answer("Нажмите кнопку под расчётом: выставить, своя сумма или другой вес. Отмена — /cancel.")


# --- Параметры печати (этап 5б): мастер дополняет или корректирует заказ ---


def params_progress(order_id: int, data: dict) -> str:
    """Заголовок с уже выбранными параметрами — показывается на каждом шаге."""
    lines = [html.bold(f"Параметры печати для заказа №{order_id}")]
    if "material" in data:
        lines.append(f"Материал: {data['material']}")
    if "layer_height" in data:
        lines.append(f"Толщина слоя: {data['layer_height']} мм")
    if "infill" in data:
        lines.append(f"Заполнение: {data['infill']}%")
    if "color" in data:
        lines.append(f"Цвет: {data['color']}")
    return "\n".join(lines)


async def params_next_step(
    callback: CallbackQuery, state: FSMContext, prompt: str, reply_markup
) -> None:
    """Редактирует то же сообщение: выбранное на данный момент + следующий вопрос."""
    data = await state.get_data()
    await callback.message.edit_text(
        f"{params_progress(data['order_id'], data)}\n\n{prompt}", reply_markup=reply_markup
    )
    await callback.answer()


async def save_params(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    file_id: str | None = None,
    file_name: str | None = None,
) -> None:
    """Последний шаг: всё выбранное сохраняется одним действием."""
    data = await state.get_data()
    await state.clear()
    order = await update_print_params(
        session,
        data["order_id"],
        PARAMS_FROM,
        material=data["material"],
        layer_height=data["layer_height"],
        infill=data["infill"],
        color=data["color"],
        file_id=file_id,
        file_name=file_name,
    )
    if order is None:
        await message.answer("Статус заказа уже изменился — параметры не сохранены.")
        return
    log_event(order, f"мастер изменил параметры: {describe_order(order)}")

    await refresh_admin_cards(
        bot, session, order, (data["card_chat_id"], data["card_message_id"])
    )
    params = f"{order.material}, {order.layer_height} мм, заполнение {order.infill}%, {order.color}"
    new_file = f"\nФайл модели: {html.quote(file_name)}" if file_id and file_name else ""
    delivered = await notify_client(
        bot,
        order,
        f"Мастер дополнил заказ №{order.id}.\nПечать: {params}{new_file}\n\n"
        f"Подробности — в разделе «{MY_ORDERS_BUTTON}».",
    )
    text = f"Параметры заказа №{order.id} сохранены."
    await message.answer(text if delivered else f"{text}\n\n{CLIENT_UNREACHABLE}")


@router.callback_query(AdminOrderCallback.filter(F.action == "params"))
async def params_start(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    order = await get_order(session, callback_data.order_id)
    if order is None or order.status not in PARAMS_FROM:
        await answer_stale(callback, session, order)
        return
    # clear(): убираем данные прошлых действий мастера (ввод суммы и т.п.).
    await state.clear()
    await state.set_state(AdminForm.choosing_material)
    await state.update_data(
        order_id=order.id,
        card_chat_id=callback.message.chat.id,
        card_message_id=callback.message.message_id,
        has_file=order.file_id is not None,
    )
    await callback.message.answer(
        f"{params_progress(order.id, {})}\n\nВыберите материал:",
        reply_markup=material_kb(AdminPrintCallback, ADMIN_CANCEL_CALLBACK),
    )
    await callback.answer()


@router.callback_query(
    AdminForm.choosing_material, AdminPrintCallback.filter(F.field == "material")
)
async def params_material(
    callback: CallbackQuery, callback_data: AdminPrintCallback, state: FSMContext
) -> None:
    # Значение из кнопки проверяем по каталогу: callback_data можно подделать.
    if callback_data.value not in MATERIALS:
        await callback.answer("Такого варианта нет.", show_alert=True)
        return
    await state.update_data(material=callback_data.value)
    await state.set_state(AdminForm.choosing_layer)
    await params_next_step(
        callback, state, "Выберите толщину слоя:",
        layer_kb(AdminPrintCallback, ADMIN_CANCEL_CALLBACK),
    )


@router.callback_query(AdminForm.choosing_layer, AdminPrintCallback.filter(F.field == "layer"))
async def params_layer(
    callback: CallbackQuery, callback_data: AdminPrintCallback, state: FSMContext
) -> None:
    if callback_data.value not in LAYER_PRICES:
        await callback.answer("Такого варианта нет.", show_alert=True)
        return
    await state.update_data(layer_height=callback_data.value)
    await state.set_state(AdminForm.choosing_infill)
    await params_next_step(
        callback, state, "Выберите плотность заполнения:",
        infill_kb(AdminPrintCallback, ADMIN_CANCEL_CALLBACK),
    )


@router.callback_query(
    AdminForm.choosing_infill, AdminPrintCallback.filter(F.field == "infill")
)
async def params_infill(
    callback: CallbackQuery, callback_data: AdminPrintCallback, state: FSMContext
) -> None:
    value = callback_data.value
    if not value.isdigit() or int(value) not in INFILL_SURCHARGES:
        await callback.answer("Такого варианта нет.", show_alert=True)
        return
    await state.update_data(infill=int(value))
    await state.set_state(AdminForm.choosing_color)
    await params_next_step(
        callback, state, "Выберите цвет:", color_kb(AdminPrintCallback, ADMIN_CANCEL_CALLBACK)
    )


@router.callback_query(AdminForm.choosing_color, AdminPrintCallback.filter(F.field == "color"))
async def params_color(
    callback: CallbackQuery, callback_data: AdminPrintCallback, state: FSMContext
) -> None:
    if callback_data.value not in COLORS:
        await callback.answer("Такого варианта нет.", show_alert=True)
        return
    await state.update_data(color=callback_data.value)
    await state.set_state(AdminForm.waiting_model_file)
    has_file = (await state.get_data())["has_file"]
    extensions = ", ".join(PRINT_EXTENSIONS)
    alternative = "или оставьте текущий файл" if has_file else "или пропустите, если файла пока нет"
    await params_next_step(
        callback,
        state,
        f"Пришлите файл модели ({extensions}) документом — {alternative}.",
        model_file_kb(has_file),
    )


@router.message(AdminForm.waiting_model_file, F.document)
async def params_file(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    file_name = message.document.file_name or ""
    if Path(file_name).suffix.lower() not in PRINT_EXTENSIONS:
        await message.answer(f"Нужен файл модели с расширением {', '.join(PRINT_EXTENSIONS)}.")
        return
    await save_params(message, state, session, bot, message.document.file_id, file_name)


@router.callback_query(AdminForm.waiting_model_file, F.data == ADMIN_SKIP_FILE_CALLBACK)
async def params_skip_file(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await save_params(callback.message, state, session, bot)
    await callback.answer()


@router.message(AdminForm.waiting_model_file)
async def params_file_expected(message: Message) -> None:
    await message.answer(
        "Пришлите файл модели документом или нажмите кнопку под сообщением выше. "
        "Отмена — /cancel."
    )


@router.message(
    StateFilter(
        AdminForm.choosing_material,
        AdminForm.choosing_layer,
        AdminForm.choosing_infill,
        AdminForm.choosing_color,
    )
)
async def params_use_buttons(message: Message) -> None:
    await message.answer("Выберите вариант кнопкой под сообщением выше. Отмена — /cancel.")


@router.callback_query(F.data == ADMIN_CANCEL_CALLBACK)
async def params_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Изменение параметров отменено.")
    await callback.answer()


@router.callback_query(AdminPrintCallback.filter())
@router.callback_query(F.data == ADMIN_SKIP_FILE_CALLBACK)
async def params_stale(callback: CallbackQuery) -> None:
    # Кнопка параметров нажата не на своём шаге (или после сохранения/отмены).
    await callback.answer("Эта кнопка уже неактуальна.")


# --- Выдача: заказ удаляется сразу, номера сдвигаются ---


@router.callback_query(AdminOrderCallback.filter(F.action == "issue"))
async def issue_start(
    callback: CallbackQuery, callback_data: AdminOrderCallback, session: AsyncSession
) -> None:
    order = await get_order(session, callback_data.order_id)
    if order is None or order.status != OrderStatus.READY:
        await answer_stale(callback, session, order)
        return
    # Удаление необратимо — сначала спрашиваем подтверждение прямо на карточке.
    await callback.message.edit_reply_markup(reply_markup=issue_confirm_kb(order))
    await callback.answer("Подтвердите выдачу — заказ будет удалён")


@router.callback_query(AdminOrderCallback.filter(F.action == "issue_back"))
async def issue_back(
    callback: CallbackQuery, callback_data: AdminOrderCallback, session: AsyncSession
) -> None:
    order = await get_order(session, callback_data.order_id)
    if order is None:
        await answer_stale(callback, session, order)
        return
    await callback.message.edit_reply_markup(reply_markup=admin_order_kb(order))
    await callback.answer()


@router.callback_query(AdminOrderCallback.filter(F.action == "issue_confirm"))
async def issue_confirm(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
    fsm_storage: BaseStorage,
) -> None:
    order = await get_order(session, callback_data.order_id)
    if order is None or order.status != OrderStatus.READY:
        await answer_stale(callback, session, order)
        return

    log_event(order, f"выдан клиенту, удалён из базы — мастер {master_name(callback.from_user)}")
    # Карточки закрываем до удаления: потом номер может достаться другому заказу.
    await close_admin_cards(
        bot, session, order, f"📬 Заказ №{order.id} выдан клиенту и удалён.", pressed_card(callback)
    )
    await delete_order(session, order.id)
    await callback.answer("Заказ выдан и удалён")
    delivered = await notify_client(
        bot, order, f"Заказ №{order.id} выдан. Спасибо, что обратились к нам! 🙌"
    )
    if not delivered:
        await callback.message.answer(CLIENT_UNREACHABLE)
    await renumber_after_delete(bot, session, fsm_storage, settings, {order.id})


@router.callback_query(AdminOrderCallback.filter(F.action == "issue_ask"))
async def issue_from_list(
    callback: CallbackQuery, callback_data: AdminOrderCallback, session: AsyncSession, bot: Bot
) -> None:
    """[📬 Выдать №N] из /orders: присылаем свежую карточку сразу с подтверждением.

    Клавиатуру самого списка не трогаем — в ней кнопки других заказов. Присланная карточка —
    обычная (запоминается в admin_cards), поэтому дальше работают issue_confirm / issue_back.
    """
    order = await get_order(session, callback_data.order_id)
    if order is None or order.status != OrderStatus.READY:
        # Номер в старом списке мог уже достаться другому заказу — ничего не делаем.
        await callback.answer(
            "Заказ уже выдан или его статус изменился — обновите список: /orders", show_alert=True
        )
        return
    await send_admin_card(
        bot, session, callback.message.chat.id, order,
        title="📬 Подтвердите выдачу — заказ будет удалён из базы.",
        reply_markup=issue_confirm_kb(order), with_files=False,
    )
    await callback.answer()


# --- Список заказов ---


@router.message(Command("orders"))
async def cmd_orders(message: Message, command: CommandObject, session: AsyncSession) -> None:
    """/orders — активные заказы, /orders all — все. Новые сверху."""
    arg = (command.args or "").strip().lower()
    if arg not in ("", "all"):
        await message.answer("Использование: /orders — активные заказы, /orders all — все заказы.")
        return
    statuses = None if arg == "all" else ACTIVE_STATUSES
    orders = await list_orders(session, statuses, limit=ORDERS_LIST_LIMIT)
    chat_counts = await count_messages(session, [order.id for order in orders])
    if not orders:
        await message.answer("Активных заказов нет." if statuses else "Заказов нет.")
        return

    total = await count_orders(session, statuses)
    title = "Активные заказы" if statuses else "Все заказы"
    lines = "\n".join(order_list_line(order) for order in orders)
    shown = f"\n\nПоказаны последние {len(orders)} из {total}." if total > len(orders) else ""
    await message.answer(
        f"{html.bold(title)} (новые сверху):\n\n{lines}{shown}\n\n"
        "📋 — карточка заказа, 📬 — выдать готовый заказ, 💬 — переписка с клиентом.",
        reply_markup=orders_list_kb(orders, chat_counts),
    )


@router.callback_query(AdminOrderCallback.filter(F.action == "open"))
async def open_order(
    callback: CallbackQuery,
    callback_data: AdminOrderCallback,
    session: AsyncSession,
    bot: Bot,
) -> None:
    order = await get_order(session, callback_data.order_id)
    if order is None:
        await callback.answer("Заказ не найден — обновите список: /orders", show_alert=True)
        return
    await send_admin_card(bot, session, callback.message.chat.id, order)
    await callback.answer()


# --- Команды ---


@router.message(Command("activity"))
async def cmd_activity(message: Message, session: AsyncSession) -> None:
    """/activity — сколько уникальных людей нажимали /start за неделю, по дням."""
    await message.answer(await activity_text(session))


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
