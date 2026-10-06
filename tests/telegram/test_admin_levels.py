import pytest

from bot.core.errors import InvalidAction, PermissionDenied
from bot.schedule.service import ScheduleService
from bot.telegram.handlers import admin
from bot.telegram.states import AdminInput
from tests.factories import add_level, add_user
from tests.telegram.fakes import FakeCallback, FakeMessage, callback_data, fsm


async def test_add_level_button_asks_for_name_with_cancel_button(session):
    adm = await add_user(session, 99, "admin")
    state = fsm()
    cb = FakeCallback("ad:addlvl")
    await admin.on_add_level(cb, state, adm)
    assert await state.get_state() == AdminInput.value.state
    text, markup = cb.message.answers[0]
    assert text == "Название нового уровня:"
    assert callback_data(markup) == ["ad:cancel"]


async def test_name_creates_level_and_returns_admin_menu(session):
    adm = await add_user(session, 99, "admin")
    state = fsm()
    await admin.on_add_level(FakeCallback("ad:addlvl"), state, adm)
    msg = FakeMessage(text="Новички")
    await admin.on_admin_input(msg, state, session, adm)
    assert [lvl.name for lvl in await ScheduleService(session).list_levels()] == ["Новички"]
    text, markup = msg.answers[0]
    assert text == "Уровень «Новички» добавлен"
    assert "menu:back" in callback_data(markup)
    assert await state.get_state() is None


@pytest.mark.parametrize("bad", ["   ", "х" * 51])
async def test_bad_name_keeps_dialog_open_for_retry(session, bad):
    adm = await add_user(session, 99, "admin")
    state = fsm()
    await admin.on_add_level(FakeCallback("ad:addlvl"), state, adm)
    with pytest.raises(InvalidAction):
        await admin.on_admin_input(FakeMessage(text=bad), state, session, adm)
    assert await state.get_state() == AdminInput.value.state
    msg = FakeMessage(text="Новички")
    await admin.on_admin_input(msg, state, session, adm)
    assert msg.answers[0][0] == "Уровень «Новички» добавлен"


async def test_duplicate_name_keeps_dialog_open(session):
    adm = await add_user(session, 99, "admin")
    await add_level(session, "Новички")
    state = fsm()
    await admin.on_add_level(FakeCallback("ad:addlvl"), state, adm)
    with pytest.raises(InvalidAction, match="уже есть"):
        await admin.on_admin_input(FakeMessage(text="Новички"), state, session, adm)
    assert await state.get_state() == AdminInput.value.state


async def test_cancel_drops_state_and_returns_admin_menu(session):
    adm = await add_user(session, 99, "admin")
    state = fsm()
    await admin.on_add_level(FakeCallback("ad:addlvl"), state, adm)
    cb = FakeCallback("ad:cancel")
    await admin.on_add_level_cancel(cb, state, adm)
    assert await state.get_state() is None
    assert await state.get_data() == {}
    assert cb.message.edits[0][0] == "Меню администратора"
    assert cb.answers == [(None, False)]


async def test_cancel_by_non_admin_is_refused(session):
    member = await add_user(session, 1, "member")
    cb = FakeCallback("ad:cancel")
    with pytest.raises(PermissionDenied):
        await admin.on_add_level_cancel(cb, fsm(), member)
    assert cb.message.edits == []


async def test_levels_screen_lists_levels_in_service_order_with_new_level_button(session):
    adm = await add_user(session, 99, "admin")
    await add_level(session, "Б", position=1)
    await add_level(session, "А", position=0)
    cb = FakeCallback("ad:levels")
    await admin.on_levels(cb, session, adm)
    text, markup = cb.message.edits[0]
    assert text == "Уровни:\n• А\n• Б"
    assert [b.text for row in markup.inline_keyboard for b in row][0] == "➕ Новый уровень"
    assert callback_data(markup) == ["ad:addlvl", "ad:menu"]
