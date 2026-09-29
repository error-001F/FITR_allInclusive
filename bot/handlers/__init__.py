from aiogram import Router

from bot.handlers import common


def get_routers() -> list[Router]:
    return [common.router]
