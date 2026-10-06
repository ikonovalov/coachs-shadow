from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.settings import get_setting
from bot.publishing.render import KIND_NAMES, format_when
from bot.schedule.models import Training


def markup(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows]
    )


async def club_tz(session: AsyncSession) -> ZoneInfo:
    return ZoneInfo(await get_setting(session, "club_timezone"))


async def local_today(session: AsyncSession) -> date:
    return datetime.now(await club_tz(session)).date()


def short_title(training: Training, tz: ZoneInfo) -> str:
    return f"{format_when(training.starts_at, training.duration_min, tz)} · {KIND_NAMES.get(training.kind, training.kind)}"


async def safe_edit(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """edit_text that tolerates a repeated tap re-rendering the same screen ("message is not modified")."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def take_form(state: FSMContext, expected: State | None = None, *, key: str | None = None) -> dict | None:
    """Claims a pending confirmation: returns its data and clears the state, or None if it is no longer there
    (wrong state / missing key). Check and clear run without I/O in between (MemoryStorage), so of two concurrent
    taps on a confirm button only one gets the data. Call it BEFORE the service; on an error use restore_form."""
    if expected is not None and await state.get_state() != expected.state:
        return None
    data = await state.get_data()
    if key is not None and key not in data:
        return None
    await state.clear()
    return data


async def restore_form(state: FSMContext, expected: State | None, data: dict) -> None:
    """Puts a claimed form back so the user can fix the input and retry."""
    await state.set_state(expected)
    await state.set_data(data)
