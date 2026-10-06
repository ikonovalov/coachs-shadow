from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bot.core.errors import NotFound, PermissionDenied
from bot.schedule.models import Chat
from bot.telegram.handlers import binding
from tests.factories import add_level, add_user
from tests.telegram.fakes import FakeCallback, FakeMessage, callback_data

GROUP = dict(chat_type="supergroup", chat_id=-1001, title="Новички клуба")


def command(args=None):
    return SimpleNamespace(args=args)


async def bound_level_id(session, chat_id=-1001):
    chat = await session.scalar(select(Chat).where(Chat.chat_id == chat_id))
    return None if chat is None else chat.level_id


async def test_bind_level_without_args_lists_levels_as_buttons(session):
    adm = await add_user(session, 99, "admin")
    a = await add_level(session, "Новички", position=0)
    b = await add_level(session, "Про", position=1)
    msg = FakeMessage(**GROUP)
    await binding.cmd_bind_level(msg, command(), session, adm)
    _, markup = msg.answers[0]
    assert callback_data(markup) == [f"bind:{a.id}", f"bind:{b.id}"]
    assert await bound_level_id(session) is None


async def test_bind_level_without_levels_gives_a_hint(session):
    adm = await add_user(session, 99, "admin")
    msg = FakeMessage(**GROUP)
    await binding.cmd_bind_level(msg, command(), session, adm)
    assert msg.answers == [(binding.NO_LEVELS_HINT, None)]


async def test_bind_level_without_args_by_non_admin_is_refused(session):
    member = await add_user(session, 1, "member")
    await add_level(session)
    msg = FakeMessage(**GROUP)
    with pytest.raises(PermissionDenied):
        await binding.cmd_bind_level(msg, command(), session, member)
    assert msg.answers == []


async def test_bind_level_with_name_works_as_before(session):
    adm = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    msg = FakeMessage(**GROUP)
    await binding.cmd_bind_level(msg, command("  Новички "), session, adm)
    assert msg.answers[0][0] == "Чат привязан к уровню «Новички»"
    assert await bound_level_id(session) == level.id


async def test_bind_button_binds_chat_and_edits_the_list(session):
    adm = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    cb = FakeCallback(f"bind:{level.id}", FakeMessage(**GROUP))
    await binding.on_bind(cb, session, adm)
    assert await bound_level_id(session) == level.id
    assert cb.message.edits == [("Чат привязан к уровню «Новички»", None)]
    assert cb.answers == [(None, False)]


async def test_bind_button_pressed_again_just_rebinds(session):
    adm = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    for _ in range(2):
        await binding.on_bind(FakeCallback(f"bind:{level.id}", FakeMessage(**GROUP)), session, adm)
    assert await bound_level_id(session) == level.id


async def test_bind_button_by_non_admin_does_not_bind(session):
    member = await add_user(session, 1, "member")
    level = await add_level(session, "Новички")
    cb = FakeCallback(f"bind:{level.id}", FakeMessage(**GROUP))
    with pytest.raises(PermissionDenied):
        await binding.on_bind(cb, session, member)
    assert await bound_level_id(session) is None
    assert cb.message.edits == []


async def test_bind_button_for_deleted_level_is_not_found(session):
    adm = await add_user(session, 99, "admin")
    cb = FakeCallback("bind:777", FakeMessage(**GROUP))
    with pytest.raises(NotFound, match="Уровень не найден"):
        await binding.on_bind(cb, session, adm)
    assert await bound_level_id(session) is None


async def test_bind_button_with_garbage_id_is_not_found(session):
    adm = await add_user(session, 99, "admin")
    cb = FakeCallback("bind:abc", FakeMessage(**GROUP))
    with pytest.raises(NotFound):
        await binding.on_bind(cb, session, adm)
