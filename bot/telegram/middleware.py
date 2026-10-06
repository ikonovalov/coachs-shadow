from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.core.db import utcnow
from bot.core.messenger import MembershipChecker
from bot.users.access import DENIED_TEXT, AccessService
from bot.users.service import UserService


class DbSessionMiddleware(BaseMiddleware):
    """Opens a DB session per update and loads/creates the acting user."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], admin_ids: list[int]) -> None:
        self.session_factory = session_factory
        self.admin_ids = admin_ids

    async def __call__(self, handler, event, data):
        async with self.session_factory() as session:
            data["session"] = session
            tg_user = data.get("event_from_user")
            if tg_user is not None and not tg_user.is_bot:
                chat = data.get("event_chat")
                data["user"] = await UserService(session).upsert_from_telegram(
                    tg_user.id,
                    tg_user.full_name,
                    is_private=chat is not None and chat.type == "private",
                    admin_ids=self.admin_ids,
                )
            return await handler(event, data)


class AccessMiddleware(BaseMiddleware):
    """Внутренний middleware: в личке пускает только участников клуба. Кнопки в группах — доказательство членства."""

    def __init__(self, checker: MembershipChecker, admin_ids: list[int]) -> None:
        self.checker = checker
        self.admin_ids = admin_ids
        self.cache: dict = {}

    @staticmethod
    def _exempt(event) -> bool:
        text = getattr(event, "text", None) or ""
        return text.startswith("/whoami") or text.startswith("/start trainer_")

    async def __call__(self, handler, event, data):
        chat = data.get("event_chat")
        user = data.get("user")
        if chat is None or user is None:
            return await handler(event, data)
        service = AccessService(data["session"], self.checker, self.admin_ids, self.cache)
        if chat.type != "private":
            if isinstance(event, CallbackQuery):
                service.mark_member(user.telegram_id, utcnow())
            return await handler(event, data)
        if self._exempt(event):
            return await handler(event, data)
        if await service.can_use(user.telegram_id, utcnow()):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer(DENIED_TEXT, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(DENIED_TEXT)
