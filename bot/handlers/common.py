from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    name = message.from_user.full_name if message.from_user else "друг"
    await message.answer(f"Привет, {html.bold(html.quote(name))}! Список команд: /help")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Доступные команды:\n"
        "/start — начать работу\n"
        "/help — эта справка"
    )
