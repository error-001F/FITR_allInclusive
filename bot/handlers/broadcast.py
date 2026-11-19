"""Рассылка мастера всем, кто заходил в бота: /broadcast → сообщение → предпросмотр → отправка."""
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.handlers.admin import IsAdmin
from bot.services.broadcast import broadcast_running, list_recipients, start_broadcast
from bot.states import BroadcastForm

router = Router(name="broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

ALREADY_RUNNING = "Рассылка уже идёт — дождитесь отчёта о ней."


class BroadcastCallback(CallbackData, prefix="bcast"):
    action: str  # send | cancel


def confirm_kb(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"✅ Отправить ({count} чел.)",
                callback_data=BroadcastCallback(action="send").pack(),
            )],
            [InlineKeyboardButton(
                text="❌ Отмена", callback_data=BroadcastCallback(action="cancel").pack()
            )],
        ]
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings
) -> None:
    if broadcast_running():
        await message.answer(ALREADY_RUNNING)
        return
    count = len(await list_recipients(session, settings.admin_ids))
    if not count:
        await message.answer("Пока некому отправлять: в боте ещё нет клиентов.")
        return
    await state.clear()
    await state.set_state(BroadcastForm.waiting_message)
    await message.answer(
        f"📣 Рассылка. Получателей: {count}.\n\n"
        "Пришлите ОДНО сообщение, которое нужно разослать: текст (форматирование сохранится), "
        "фото или видео с подписью, файл. Альбом из нескольких фото не поддерживается.\n\n"
        "Перед отправкой покажу, как его увидят клиенты. Отмена — /cancel."
    )


@router.message(BroadcastForm.waiting_message, ~F.text.startswith("/"))
async def broadcast_message(
    message: Message, state: FSMContext, session: AsyncSession, settings: Settings, bot: Bot
) -> None:
    try:
        # Предпросмотр — ровно то, что получат клиенты.
        await bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    except TelegramBadRequest:
        await message.answer("Такое сообщение разослать нельзя. Пришлите текст, фото, видео или файл.")
        return
    count = len(await list_recipients(session, settings.admin_ids))
    await state.set_state(BroadcastForm.confirming)
    await state.update_data(
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        media_group_id=message.media_group_id,
    )
    note = "\n⚠️ Это альбом — разошлётся только первое фото." if message.media_group_id else ""
    await message.answer(
        f"👆 Так сообщение увидят клиенты. Отправить всем ({count} чел.)?{note}",
        reply_markup=confirm_kb(count),
    )


@router.message(BroadcastForm.confirming, ~F.text.startswith("/"))
async def broadcast_confirm_expected(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if message.media_group_id and message.media_group_id == data.get("media_group_id"):
        return  # остальные фото того же альбома — молча пропускаем
    await message.answer("Нажмите «✅ Отправить» или «❌ Отмена» под предпросмотром. Отмена — /cancel.")


@router.callback_query(BroadcastForm.confirming, BroadcastCallback.filter(F.action == "send"))
async def broadcast_send(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
    session_pool: async_sessionmaker[AsyncSession],
) -> None:
    if broadcast_running():
        await callback.answer(ALREADY_RUNNING, show_alert=True)
        return
    data = await state.get_data()
    await state.clear()
    recipients = await list_recipients(session, settings.admin_ids)
    await callback.message.edit_text(
        f"📣 Рассылка началась: {len(recipients)} чел. Когда закончится — пришлю отчёт."
    )
    start_broadcast(
        bot, session_pool, recipients, data["from_chat_id"], data["message_id"],
        report_chat_id=callback.message.chat.id,
    )
    await callback.answer()


@router.callback_query(BroadcastForm.confirming, BroadcastCallback.filter(F.action == "cancel"))
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.")
    await callback.answer()


@router.callback_query(BroadcastCallback.filter())
async def broadcast_stale(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка уже неактуальна.")
