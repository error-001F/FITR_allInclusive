from aiogram import Router

from bot.handlers import admin, chat, common, orders


def get_routers() -> list[Router]:
    # Порядок важен:
    # - вход в переписку из меню — раньше заказов, иначе шаг «Напишите ФИО» принял бы
    #   кнопку меню за имя;
    # - общий /cancel из orders — раньше переписки и админки, иначе «принять сообщение»
    #   или «ввод суммы» перехватили бы его.
    return [common.router, chat.entry_router, orders.router, chat.router, admin.router]
