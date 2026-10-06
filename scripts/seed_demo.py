"""Usage: python -m scripts.seed_demo  (after `alembic upgrade head`, with .env filled in)."""
import asyncio
from datetime import time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from bot.bootstrap import bootstrap
from bot.core.config import Config
from bot.core.db import make_engine, make_session_factory, utcnow
from bot.core.settings import get_setting
from bot.schedule.models import Level
from bot.schedule.service import ScheduleService, SeriesInput
from bot.users.models import User


async def main() -> None:
    cfg = Config()
    engine = make_engine(cfg.database_url)
    factory = make_session_factory(engine)
    now = utcnow()
    await bootstrap(factory, cfg, now)
    async with factory() as session:
        admin = await session.get(User, cfg.admin_ids[0])
        level = await session.scalar(select(Level).where(Level.name == "Новички"))
        if level is None:
            level = await ScheduleService(session).add_level(admin, "Новички")
        tz = ZoneInfo(await get_setting(session, "club_timezone"))
        tomorrow = (now.astimezone(tz) + timedelta(days=1)).date()

        await ScheduleService(session).create_series(
            admin,
            SeriesInput(kind="intervals", place="Парк", level_ids=[level.id], start_date=tomorrow,
                        start_time=time(19, 0), capacity=10, description="Демо-тренировка"),
            now,
        )
    await engine.dispose()
    print("Seeded. Bind a group with /bind_level Новички; the post appears within 30 s (starts < 24 h ahead).")


if __name__ == "__main__":
    asyncio.run(main())
