import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import MembershipChecker
from bot.schedule.models import Chat

log = logging.getLogger(__name__)

DENIED_TEXT = "Бот доступен участникам клуба"


class AccessService:
    def __init__(
        self,
        session: AsyncSession,
        checker: MembershipChecker,
        admin_ids: list[int],
        cache: dict[int, tuple[bool, datetime]],
        ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        self.session = session
        self.checker = checker
        self.admin_ids = admin_ids
        self.cache = cache
        self.ttl = ttl

    def mark_member(self, user_id: int, now: datetime) -> None:
        """A button press inside a bound chat proves membership."""
        self.cache[user_id] = (True, now)

    async def can_use(self, user_id: int, now: datetime) -> bool:
        if user_id in self.admin_ids:
            return True
        hit = self.cache.get(user_id)
        if hit is not None and now - hit[1] < self.ttl:
            return hit[0]
        allowed = False
        for chat_id in list(await self.session.scalars(select(Chat.chat_id))):
            try:
                if await self.checker.is_member(chat_id, user_id):
                    allowed = True
                    break
            except Exception:
                log.warning("membership check failed for chat %s", chat_id, exc_info=True)
        self.cache[user_id] = (allowed, now)
        return allowed
