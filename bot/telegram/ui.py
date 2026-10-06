from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
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
