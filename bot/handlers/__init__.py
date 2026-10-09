from aiogram import Router

from bot.handlers import admin, common, orders


def get_routers() -> list[Router]:
    # Порядок важен: общий /cancel из orders должен сработать раньше, чем
    # админский «ввод суммы» примет его за неверное число.
    return [common.router, orders.router, admin.router]
