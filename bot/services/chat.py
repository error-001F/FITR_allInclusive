"""Переписка клиента и мастера: история по заказу и доставка сообщений."""
import logging

from aiogram import Bot, html
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import ChatMessage, Order
from bot.services.formatting import local_time
from bot.services.notifications import DELIVERY_ERRORS

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 30  # сколько последних сообщений показывать в истории
MAX_TEXT_LENGTH = 1000  # длина текстового сообщения
MAX_CAPTION_LENGTH = 800  # подпись к фото/файлу: лимит Telegram 1024 минус наш заголовок
HISTORY_LINE_LENGTH = 300  # длинные сообщения в истории обрезаем, чтобы влезть в 4096 символов

KIND_LABELS = {"photo": "[фото]", "document": "[файл]"}


def message_content(message: Message) -> tuple[str, str | None, str | None] | None:
    """(вид, текст, file_id) для текста, фото или файла; None — такой тип не принимаем."""
    if message.text:
        return "text", message.text, None
    if message.photo:
        # Telegram присылает фото в нескольких размерах, последний — самый большой.
        return "photo", message.caption, message.photo[-1].file_id
    if message.document:
        return "document", message.caption or message.document.file_name, message.document.file_id
    return None


def content_error(kind: str, text: str | None) -> str | None:
    """Текст ошибки, если сообщение слишком длинное, иначе None."""
    limit = MAX_TEXT_LENGTH if kind == "text" else MAX_CAPTION_LENGTH
    if text and len(text) > limit:
        return f"Слишком длинно — сократите до {limit} символов."
    return None


def content_summary(kind: str, text: str | None) -> str:
    """Короткое описание для журнала: «[фото] подпись» или сам текст."""
    label = KIND_LABELS.get(kind, "")
    return " ".join(part for part in (label, text or "") if part) or label


async def deliver(
    bot: Bot,
    chat_id: int,
    kind: str,
    text: str | None,
    file_id: str | None,
    header: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Одно сообщение: заголовок + содержимое + кнопки. False — получатель недоступен."""
    body = f"{header}\n\n{html.quote(text)}" if text else header
    try:
        if kind == "photo":
            await bot.send_photo(chat_id, file_id, caption=body, reply_markup=reply_markup)
        elif kind == "document":
            await bot.send_document(chat_id, file_id, caption=body, reply_markup=reply_markup)
        else:
            await bot.send_message(chat_id, body, reply_markup=reply_markup)
    except DELIVERY_ERRORS as error:
        logger.warning("Сообщение переписки не доставлено в чат %s: %s", chat_id, error)
        return False
    return True


async def save_message(
    session: AsyncSession,
    *,
    order_id: int,
    author_role: str,
    author_id: int,
    author_name: str,
    kind: str,
    text: str | None,
    file_id: str | None,
) -> None:
    session.add(
        ChatMessage(
            order_id=order_id,
            author_role=author_role,
            author_id=author_id,
            author_name=author_name,
            kind=kind,
            text=text,
            file_id=file_id,
        )
    )
    await session.commit()


async def list_messages(session: AsyncSession, order_id: int, limit: int) -> list[ChatMessage]:
    """Последние limit сообщений заказа в хронологическом порядке."""
    result = await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.order_id == order_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    )
    return list(reversed(list(result)))


async def count_messages(session: AsyncSession, order_ids: list[int]) -> dict[int, int]:
    """{номер заказа: сколько сообщений} — для подписей кнопок в /orders."""
    if not order_ids:
        return {}
    rows = await session.execute(
        select(ChatMessage.order_id, func.count())
        .where(ChatMessage.order_id.in_(order_ids))
        .group_by(ChatMessage.order_id)
    )
    return dict(rows.all())


async def list_attachments(session: AsyncSession, order_id: int) -> list[ChatMessage]:
    result = await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.order_id == order_id, ChatMessage.file_id.is_not(None))
        .order_by(ChatMessage.id)
    )
    return list(result)


def history_text(order: Order, messages: list[ChatMessage], total: int) -> str:
    lines = [
        html.bold(f"💬 Переписка по заказу №{order.id} ({html.quote(order.customer_name)})"),
        "",
    ]
    if not messages:
        lines.append("Сообщений пока нет.")
    for message in messages:
        author = "Клиент" if message.author_role == "client" else html.quote(message.author_name)
        text = message.text or ""
        if len(text) > HISTORY_LINE_LENGTH:
            text = text[:HISTORY_LINE_LENGTH].rstrip() + "…"
        content = " ".join(part for part in (KIND_LABELS.get(message.kind, ""), html.quote(text)) if part)
        lines.append(
            f"{local_time(message.created_at):%d.%m %H:%M} {html.bold(author)}: {content}"
        )
    if total > len(messages):
        lines.append(f"\nПоказаны последние {len(messages)} из {total}.")
    return "\n".join(lines)
