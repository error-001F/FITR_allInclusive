from aiogram import Router

from bot.handlers import common, orders


def get_routers() -> list[Router]:
    return [common.router, orders.router]
