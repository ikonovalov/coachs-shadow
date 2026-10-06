from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base

DEFAULTS: dict[str, str] = {
    "publish_window_h": "24",
    "reminder_h": "2",
    "duration_min": "90",
    "inventory_deadline_h": "",
    "club_timezone": "Europe/Moscow",
}


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


async def get_setting(session: AsyncSession, key: str) -> str:
    row = await session.get(Setting, key)
    return row.value if row is not None else DEFAULTS[key]


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.commit()


async def set_setting_if_absent(session: AsyncSession, key: str, value: str) -> None:
    if await session.get(Setting, key) is None:
        await set_setting(session, key, value)
