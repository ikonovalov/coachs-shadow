import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText

from bot.telegram.states import NewTraining
from bot.telegram.ui import markup, restore_form, safe_edit, short_title, take_form
from tests.factories import add_level, add_training, add_user


def test_markup_builds_callback_buttons():
    m = markup([("A", "a:1"), ("B", "b:2")], [("C", "c:3")])
    assert [[b.callback_data for b in row] for row in m.inline_keyboard] == [["a:1", "b:2"], ["c:3"]]
    assert m.inline_keyboard[1][0].text == "C"


async def test_short_title(session):
    trainer = await add_user(session, 1, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    )
    assert short_title(t, ZoneInfo("Europe/Moscow")) == "вторник, 20.10 19:00–20:30 · Шоссе"


# ---------- FSM claim / safe edit ----------

def fsm() -> FSMContext:
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=5, user_id=5))


async def test_take_form_gives_data_to_one_of_two_concurrent_taps():
    state = fsm()
    await state.set_state(NewTraining.confirm)
    await state.update_data(place="Парк")
    first, second = await asyncio.gather(take_form(state, NewTraining.confirm), take_form(state, NewTraining.confirm))
    assert sorted([first, second], key=bool) == [None, {"place": "Парк"}]
    assert await state.get_state() is None


async def test_take_form_checks_required_key_and_restore_puts_it_back():
    state = fsm()
    assert await take_form(state, key="sel") is None
    await state.update_data(sel=[1, 2])
    data = await take_form(state, key="sel")
    assert data == {"sel": [1, 2]} and await state.get_data() == {}
    await restore_form(state, NewTraining.confirm, data)
    assert await state.get_state() == NewTraining.confirm.state and await state.get_data() == {"sel": [1, 2]}


class EditingMessage:
    def __init__(self, error: str | None = None):
        self.error = error
        self.edits = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append(text)
        if self.error:
            raise TelegramBadRequest(method=EditMessageText(text=text), message=self.error)


async def test_safe_edit_ignores_only_not_modified():
    await safe_edit(EditingMessage("Bad Request: message is not modified: same content"), "x")
    ok = EditingMessage()
    await safe_edit(ok, "y", reply_markup=None)
    assert ok.edits == ["y"]
    with pytest.raises(TelegramBadRequest):
        await safe_edit(EditingMessage("Bad Request: message to edit not found"), "z")
