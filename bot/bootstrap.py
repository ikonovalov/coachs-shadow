from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.core.config import Config
from bot.core.settings import set_setting, set_setting_if_absent
from bot.inventory.models import ensure_cleat_compat
from bot.jobs.queue import ensure_generate_job
from bot.users.service import UserService


async def bootstrap(session_factory: async_sessionmaker[AsyncSession], cfg: Config, now: datetime) -> None:
    async with session_factory() as session:
        await UserService(session).sync_admins(cfg.admin_ids)
        await set_setting_if_absent(session, "club_timezone", cfg.club_timezone)
        await ensure_generate_job(session, now)
        await ensure_cleat_compat(session)
        await set_setting(session, "billing_enabled", "1" if cfg.billing_enabled else "0")
        await session.commit()
