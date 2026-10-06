from bot.telegram.handlers.chats import on_remove_confirmed
from tests.factories import add_chat, add_chat_trainer, add_level, add_user


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append(text)


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


async def test_lead_removing_himself_lands_on_the_chat_list(session):
    chat = await add_chat(session, await add_level(session), -1001)
    lead = await add_user(session, 10, "trainer")
    await add_chat_trainer(session, chat, 10, is_lead=True)
    cb = FakeCallback(f"ch:rmy:{chat.chat_id}:10")
    await on_remove_confirmed(cb, session, lead)  # no PermissionDenied after a successful removal
    assert cb.message.edits == ["Нет чатов под вашим управлением"]
    assert cb.answers == ["Убран"]
