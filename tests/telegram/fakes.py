"""Фейки для тестов меню: в отличие от test_inventory_handlers хранят chat.title и разметку правок."""
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


class FakeMessage:
    def __init__(self, chat_type="private", chat_id=5, title="Чат", text=None):
        self.chat = SimpleNamespace(type=chat_type, id=chat_id, title=title)
        self.text = text
        self.answers = []  # (text, reply_markup)
        self.edits = []  # (text, reply_markup)
        self.deleted = False
        self.delete_error = None  # исключение, которое должен бросить delete()

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def delete(self):
        if self.delete_error:
            raise self.delete_error
        self.deleted = True

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or FakeMessage()
        self.answers = []  # (text, show_alert)

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def fsm(user_id=5) -> FSMContext:
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))


def callback_data(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]
