from aiogram import Router

from bot.handlers import admin, chat, common, edit, orders, payment


def get_routers() -> list[Router]:
    # Порядок важен:
    # - вход в переписку из меню — раньше заказов, иначе шаг «Напишите ФИО» принял бы
    #   кнопку меню за имя;
    # - общий /cancel из orders — раньше редактирования, оплаты, переписки и админки,
    #   иначе «новое ФИО», «принять чек», «принять сообщение» или «ввод суммы»
    #   перехватили бы его.
    return [
        common.router,
        chat.entry_router,
        orders.router,
        edit.router,
        payment.router,
        chat.router,
        admin.router,
    ]
