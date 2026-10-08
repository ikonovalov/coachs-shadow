from zoneinfo import ZoneInfo

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base
from bot.core.errors import InvalidAction

DEFAULTS: dict[str, str] = {
    "publish_window_h": "24",
    "reminder_h": "2",
    "duration_min": "90",
    "inventory_deadline_h": "",
    "club_timezone": "Europe/Moscow",
    "billing_enabled": "0",
}


INVENTORY_DEADLINE_MAX_H = 720  # 30 суток: больше — заведомо опечатка (и timedelta переполнилась бы)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


async def get_setting(session: AsyncSession, key: str) -> str:
    row = await session.get(Setting, key)
    return row.value if row is not None else DEFAULTS[key]


async def billing_enabled(session: AsyncSession) -> bool:
    """Включена ли оплата. Значение пишет bootstrap из Config при каждом старте."""
    return await get_setting(session, "billing_enabled") == "1"


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


def validate_setting(key: str, value: str) -> str:
    value = value.strip()
    if key in ("publish_window_h", "reminder_h", "duration_min"):
        if not value.isascii() or not value.isdigit() or int(value) < 1:
            raise InvalidAction("Нужно целое число больше нуля")
    elif key == "inventory_deadline_h":
        if value.lower() in ("-", "нет"):
            value = ""  # без ограничения
        elif value and not (
            value.isascii() and value.isdigit() and int(value) <= INVENTORY_DEADLINE_MAX_H
        ):
            raise InvalidAction(
                f"Нужно целое число часов от 0 до {INVENTORY_DEADLINE_MAX_H} или «-» (без ограничения); "
                "0 — заказ открыт до старта"
            )
    elif key == "club_timezone":
        try:
            ZoneInfo(value)
        except Exception:
            raise InvalidAction("Неизвестный часовой пояс (пример: Europe/Moscow)") from None
    else:
        raise KeyError(key)
    return value
