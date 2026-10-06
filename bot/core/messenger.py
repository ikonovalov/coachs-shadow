from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Button:
    text: str
    data: str | None = None  # callback data
    url: str | None = None


Keyboard = list[list[Button]]


class ChatUnavailable(Exception):
    """The bot was removed from the chat or lost the right to write there."""

    def __init__(self, chat_id: int) -> None:
        super().__init__(f"chat {chat_id} is unavailable")
        self.chat_id = chat_id


class Messenger(Protocol):
    async def send(self, chat_id: int, text: str, keyboard: Keyboard | None = None) -> int:
        """Send a message; returns message_id. Raises ChatUnavailable."""

    async def edit_post(self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None = None) -> int:
        """Edit a post; returns the current message_id (new if the old one was deleted).
        An empty keyboard removes the buttons. Raises ChatUnavailable."""

    async def notify(self, user_id: int, text: str, keyboard: Keyboard | None = None) -> bool:
        """DM a user. Returns False if it could not be delivered (never raises for that)."""
