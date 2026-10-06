from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.core.messenger import ChatUnavailable, Keyboard
from bot.telegram.keyboards import to_markup


class AiogramMessenger:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(self, chat_id: int, text: str, keyboard: Keyboard | None = None) -> int:
        try:
            message = await self.bot.send_message(chat_id, text, reply_markup=to_markup(keyboard))
        except (TelegramForbiddenError, TelegramBadRequest) as exc:  # kicked / chat not found
            raise ChatUnavailable(chat_id) from exc
        return message.message_id

    async def edit_post(self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None = None) -> int:
        try:
            await self.bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=to_markup(keyboard)
            )
        except TelegramBadRequest as exc:
            reason = str(exc)
            if "message is not modified" in reason:
                return message_id
            if "message to edit not found" in reason or "can't be edited" in reason:
                return await self.send(chat_id, text, keyboard)  # post was deleted by hand
            raise ChatUnavailable(chat_id) from exc
        except TelegramForbiddenError as exc:
            raise ChatUnavailable(chat_id) from exc
        return message_id

    async def notify(self, user_id: int, text: str, keyboard: Keyboard | None = None) -> bool:
        try:
            await self.bot.send_message(user_id, text, reply_markup=to_markup(keyboard))
        except (TelegramForbiddenError, TelegramBadRequest):
            return False  # user never pressed /start or blocked the bot
        return True
