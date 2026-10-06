import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

log = logging.getLogger(__name__)


class AiogramMembership:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def is_member(self, chat_id: int, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
        except TelegramAPIError:
            log.warning("get_chat_member failed for chat %s", chat_id, exc_info=True)
            return False  # бота нет в чате или чат недоступен
        if member.status in ("creator", "administrator", "member"):
            return True
        return member.status == "restricted" and bool(getattr(member, "is_member", False))
