from dataclasses import dataclass

from bot.core.messenger import ChatUnavailable, Keyboard


@dataclass
class Message:
    chat_id: int
    message_id: int
    text: str
    keyboard: Keyboard | None


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.edits: list[Message] = []
        self.notifications: list[tuple[int, str]] = []
        self.unreachable: set[int] = set()
        self.unavailable_chats: set[int] = set()
        self.deleted_posts: set[tuple[int, int]] = set()
        self._next_id = 100

    async def send(self, chat_id, text, keyboard=None) -> int:
        if chat_id in self.unavailable_chats:
            raise ChatUnavailable(chat_id)
        self._next_id += 1
        self.sent.append(Message(chat_id, self._next_id, text, keyboard))
        return self._next_id

    async def edit_post(self, chat_id, message_id, text, keyboard=None) -> int:
        if chat_id in self.unavailable_chats:
            raise ChatUnavailable(chat_id)
        if (chat_id, message_id) in self.deleted_posts:
            return await self.send(chat_id, text, keyboard)
        self.edits.append(Message(chat_id, message_id, text, keyboard))
        return message_id

    async def notify(self, user_id, text, keyboard=None) -> bool:
        if user_id in self.unreachable:
            return False
        self.notifications.append((user_id, text))
        return True
