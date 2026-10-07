import pytest
from aiogram.filters import CommandObject

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.schedule.service import ScheduleService
from bot.telegram.handlers import admin
from bot.telegram.states import AdminInput
from tests.factories import add_chat, add_level, add_user
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


async def test_levels_screen_lists_levels_as_cross_buttons_in_service_order(session):
    adm = await add_user(session, 99, "admin")
    b = await add_level(session, "Б", position=1)
    a = await add_level(session, "А", position=0)
    cb = FakeCallback("ad:levels")
    await admin.on_levels(cb, session, adm)
    text, markup = cb.message.edits[0]
    assert text == "Уровни (нажмите, чтобы удалить):"
    assert [btn.text for row in markup.inline_keyboard for btn in row] == ["❌ А", "❌ Б", "➕ Новый уровень", "⬅️ Меню"]
    assert callback_data(markup) == [f"ad:dl:{a.id}", f"ad:dl:{b.id}", "ad:addlvl", "ad:menu"]


async def test_levels_screen_without_levels_has_only_add_and_menu(session):
    adm = await add_user(session, 99, "admin")
    cb = FakeCallback("ad:levels")
    await admin.on_levels(cb, session, adm)
    text, markup = cb.message.edits[0]
    assert text == "Уровней пока нет"
    assert callback_data(markup) == ["ad:addlvl", "ad:menu"]


async def test_delete_confirm_screen_asks_before_deleting(session):
    adm = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    cb = FakeCallback(f"ad:dl:{level.id}")
    await admin.on_delete_level_confirm(cb, session, adm)
    text, markup = cb.message.edits[0]
    assert "Новички" in text
    assert callback_data(markup) == [f"ad:dly:{level.id}", "ad:levels"]
    assert len(await ScheduleService(session).list_levels()) == 1


async def test_confirmed_delete_removes_level_and_shows_levels_screen(session):
    adm = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    await add_level(session, "Про", position=1)
    cb = FakeCallback(f"ad:dly:{level.id}")
    await admin.on_delete_level_confirmed(cb, session, adm)
    assert [lvl.name for lvl in await ScheduleService(session).list_levels()] == ["Про"]
    text, markup = cb.message.edits[0]
    assert text == "Уровни (нажмите, чтобы удалить):"
    assert [btn.text for row in markup.inline_keyboard for btn in row][0] == "❌ Про"
    assert cb.answers == [("Уровень удалён", False)]


async def test_confirmed_delete_of_used_level_is_refused_and_keeps_level(session):
    adm = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    await add_chat(session, level)
    with pytest.raises(InvalidAction, match="используется"):
        await admin.on_delete_level_confirmed(FakeCallback(f"ad:dly:{level.id}"), session, adm)
    assert len(await ScheduleService(session).list_levels()) == 1


async def test_delete_screens_refuse_non_admin(session):
    member = await add_user(session, 1, "member")
    level = await add_level(session)
    for handler, data in (
        (admin.on_delete_level_confirm, f"ad:dl:{level.id}"),
        (admin.on_delete_level_confirmed, f"ad:dly:{level.id}"),
    ):
        cb = FakeCallback(data)
        with pytest.raises(PermissionDenied):
            await handler(cb, session, member)
        assert cb.message.edits == []
    assert len(await ScheduleService(session).list_levels()) == 1


async def test_delete_level_command_deletes_by_name(session):
    adm = await add_user(session, 99, "admin")
    await add_level(session, "Новички")
    msg = FakeMessage()
    await admin.cmd_delete_level(msg, CommandObject(prefix="/", command="delete_level", args=" Новички "), session, adm)
    assert await ScheduleService(session).list_levels() == []
    assert msg.answers[0][0] == "Уровень «Новички» удалён"


async def test_delete_level_command_requires_existing_name(session):
    adm = await add_user(session, 99, "admin")
    with pytest.raises(InvalidAction, match="Использование"):
        await admin.cmd_delete_level(FakeMessage(), CommandObject(prefix="/", command="delete_level"), session, adm)
    with pytest.raises(NotFound):
        await admin.cmd_delete_level(
            FakeMessage(), CommandObject(prefix="/", command="delete_level", args="Нет"), session, adm
        )
