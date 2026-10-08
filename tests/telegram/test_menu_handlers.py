from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from bot.core.errors import PermissionDenied
from bot.telegram.handlers import common
from bot.telegram.states import AdminInput
from bot.users.roles import RoleService
from tests.factories import NOW, add_chat, add_chat_trainer, add_level, add_training, add_user
from tests.telegram.fakes import FakeCallback, FakeMessage, callback_data, fsm

MEMBER_MENU = ["menu:schedule", "menu:my", "menu:profile", "menu:close"]
ADMIN_MENU_BUTTONS = ["menu:schedule", "menu:my", "menu:profile", "menu:trainer", "menu:admin", "menu:close"]


async def test_start_shows_menu_without_slash_commands(session):
    member = await add_user(session, 1, "member", name="Аня")
    msg = FakeMessage()
    await common.cmd_start(msg, member)
    text, markup = msg.answers[0]
    assert "Аня" in text and "/schedule" not in text
    assert callback_data(markup) == MEMBER_MENU


async def test_start_menu_shows_admin_button_only_to_admin(session):
    admin = await add_user(session, 99, "admin")
    msg = FakeMessage()
    await common.cmd_start(msg, admin)
    assert callback_data(msg.answers[0][1]) == ADMIN_MENU_BUTTONS


async def test_back_clears_state_and_edits_message(session):
    admin = await add_user(session, 99, "admin")
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:back")
    await common.on_menu_back(cb, admin, state)
    assert await state.get_state() is None
    assert callback_data(cb.message.edits[0][1]) == ADMIN_MENU_BUTTONS
    assert cb.answers == [(None, False)]


async def test_close_deletes_menu_and_clears_state():
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:close")
    await common.on_menu_close(cb, state)
    assert cb.message.deleted
    assert await state.get_state() is None
    assert cb.answers == [(None, False)]


async def test_close_falls_back_to_edit_when_message_cannot_be_deleted():
    cb = FakeCallback("menu:close")
    cb.message.delete_error = TelegramBadRequest(method=None, message="message can't be deleted")
    await common.on_menu_close(cb, fsm())
    assert not cb.message.deleted
    text, markup = cb.message.edits[0]
    assert "/start" in text and markup is None
    assert cb.answers == [(None, False)]


async def test_admin_button_opens_admin_menu_and_clears_state(session):
    admin = await add_user(session, 99, "admin")
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:admin")
    await common.on_menu_admin(cb, admin, state)
    text, markup = cb.message.edits[0]
    assert text == "Меню администратора"
    assert "menu:back" in callback_data(markup) and "ad:park" in callback_data(markup)
    assert await state.get_state() is None


async def test_stale_admin_button_is_refused_for_demoted_admin(session):
    ex_admin = await add_user(session, 99, "member")
    cb = FakeCallback("menu:admin")
    with pytest.raises(PermissionDenied, match="Только для администратора"):
        await common.on_menu_admin(cb, ex_admin, fsm())
    assert cb.message.edits == []


async def test_trainer_button_opens_trainer_menu(session):
    level = await add_level(session)
    chat = await add_chat(session, level)
    trainer = await add_user(session, 10, "trainer")
    await add_chat_trainer(session, chat, 10, is_lead=True)
    cb = FakeCallback("menu:trainer")
    await common.on_menu_trainer(cb, session, trainer, fsm())
    assert cb.message.edits[0][0] == "Меню тренера"
    assert "menu:back" in callback_data(cb.message.edits[0][1])


async def test_trainer_without_chat_gets_alert_text_not_menu(session):
    trainer = await add_user(session, 10, "trainer")
    cb = FakeCallback("menu:trainer")
    with pytest.raises(PermissionDenied, match="не добавлены ни в один чат"):
        await common.on_menu_trainer(cb, session, trainer, fsm())
    assert cb.message.edits == []


async def test_stale_trainer_button_is_refused_for_ex_trainer(session):
    member = await add_user(session, 10, "member")
    cb = FakeCallback("menu:trainer")
    with pytest.raises(PermissionDenied, match="Раздел для тренеров"):
        await common.on_menu_trainer(cb, session, member, fsm())
    assert cb.message.edits == []


async def test_schedule_button_with_no_trainings_says_so(session, messenger):
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:schedule")
    await common.on_menu_schedule(cb, session, messenger, state)
    (text, markup), = cb.message.answers
    assert text == "Ближайших тренировок нет"
    assert callback_data(markup) == ["menu:back"]
    assert cb.answers == [(None, False)]
    assert await state.get_state() is None


async def test_schedule_with_trainings_sends_only_cards(session, messenger, monkeypatch):
    monkeypatch.setattr("bot.telegram.handlers.schedule.utcnow", lambda: NOW)
    trainer = await add_user(session, 2, "trainer")
    level = await add_level(session)
    await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(days=1), published_at=NOW
    )
    cb = FakeCallback("menu:schedule")
    await common.on_menu_schedule(cb, session, messenger, fsm())
    assert len(cb.message.answers) == 1  # только карточка, без завершающего сообщения
    assert "menu:back" not in callback_data(cb.message.answers[0][1])  # карточка — общая с постами в чатах


async def test_my_button_sends_my_records_and_clears_state(session):
    member = await add_user(session, 1, "member")
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:my")
    await common.on_menu_my(cb, session, member, state)
    assert len(cb.message.answers) == 1
    assert callback_data(cb.message.answers[0][1])[-1] == "menu:back"
    assert cb.answers == [(None, False)]
    assert await state.get_state() is None


async def test_profile_button_sends_profile_with_edit_buttons_and_clears_state(session):
    member = await add_user(session, 1, "member")
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:profile")
    await common.on_menu_profile(cb, member, state)
    _, markup = cb.message.answers[0]
    assert "pf:h" in callback_data(markup)
    assert callback_data(markup)[-1] == "menu:back"
    assert cb.answers == [(None, False)]
    assert await state.get_state() is None


async def test_trainer_invite_deep_link_shows_menu_not_slash_command(session, monkeypatch):
    monkeypatch.setattr(common, "utcnow", lambda: NOW)
    admin = await add_user(session, 99, "admin")
    newbie = await add_user(session, 10, "member")
    invite = await RoleService(session).create_invite(admin, NOW)
    msg = FakeMessage()
    await common.cmd_start_link(msg, SimpleNamespace(args=f"trainer_{invite.token}"), session, newbie, fsm())
    text, markup = msg.answers[0]
    assert "/trainer" not in text
    assert "menu:trainer" in callback_data(markup)


async def test_unknown_menu_button_is_just_answered():
    cb = FakeCallback("menu:old")
    await common.on_menu_stale(cb)
    assert cb.answers == [(None, False)]
