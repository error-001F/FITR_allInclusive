"""Проверка, что присланный клиентом файл модели действительно STL или STEP.

Расширение ничего не гарантирует: photo.jpg, переименованный в model.stl, его пройдёт.
Поэтому скачиваем файл и смотрим на его начало (сигнатуру):
- STEP (.step, .stp) — текстовый файл, первая строка «ISO-10303-21;»;
- STL текстовый — начинается со слова «solid»;
- STL двоичный — сигнатуры нет: 80 байт заголовка, затем число треугольников (4 байта),
  затем по 50 байт на треугольник. Узнаём его по тому, что размер файла сходится с этим числом.
"""

import asyncio
import io
import logging
import struct
from pathlib import Path

from aiohttp import ClientError
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Document

logger = logging.getLogger(__name__)

# Bot API отдаёт боту файлы только до 20 МБ — более крупные принимаем без проверки.
MAX_CHECK_SIZE = 20 * 1024 * 1024

STL_HEADER_SIZE = 84  # 80 байт заголовка + 4 байта числа треугольников
STL_TRIANGLE_SIZE = 50

STEP_EXTENSIONS = {".step", ".stp"}


def is_step(data: bytes) -> bool:
    # Некоторые редакторы ставят в начало BOM или пустые строки — пропускаем их.
    return data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"ISO-10303-21;")


def is_stl(data: bytes) -> bool:
    if len(data) >= STL_HEADER_SIZE:
        (triangles,) = struct.unpack_from("<I", data, 80)
        if triangles > 0 and len(data) == STL_HEADER_SIZE + triangles * STL_TRIANGLE_SIZE:
            return True  # двоичный STL
    return data.lstrip().lower().startswith(b"solid")  # текстовый STL


def matches_extension(file_name: str, data: bytes) -> bool:
    if Path(file_name).suffix.lower() in STEP_EXTENSIONS:
        return is_step(data)
    return is_stl(data)


async def check_model_file(bot: Bot, document: Document) -> bool:
    """False — файл точно не того формата, что указан в расширении.

    Если проверить не получилось (файл больше 20 МБ, сбой сети), файл принимаем: мастер
    всё равно откроет его сам, а клиент не должен застрять на шаге из-за ошибки бота.
    """
    if document.file_size is not None and document.file_size > MAX_CHECK_SIZE:
        return True
    buffer = io.BytesIO()
    try:
        await bot.download(document, destination=buffer)
    except (TelegramAPIError, ClientError, asyncio.TimeoutError) as error:
        logger.warning("Файл %s не проверен: %s", document.file_name, error)
        return True
    return matches_extension(document.file_name or "", buffer.getvalue())
