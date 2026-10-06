from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
