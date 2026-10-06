from types import SimpleNamespace

from aiogram.types import CallbackQuery

from bot.telegram.middleware import AccessMiddleware
from tests.factories import add_chat, add_level
from tests.fakes import FakeMembership


async def run(mw, session, chat_id, event):
    calls = []

    async def handler(e, d):
        calls.append(1)

    data = {
        "event_chat": SimpleNamespace(id=chat_id, type="supergroup"),
        "user": SimpleNamespace(telegram_id=5),
        "session": session,
    }
    await mw(handler, event, data)
    return calls


def cb_event():
    return CallbackQuery.model_construct(id="1", data="x")


async def test_button_in_bound_chat_proves_membership(session):
    await add_chat(session, await add_level(session), -1)
    mw = AccessMiddleware(FakeMembership(), [])
    assert await run(mw, session, -1, cb_event()) == [1]
    assert mw.cache[5][0] is True


async def test_button_in_unbound_chat_does_not(session):
    await add_chat(session, await add_level(session), -1)
    mw = AccessMiddleware(FakeMembership(), [])
    assert await run(mw, session, -999, cb_event()) == [1]
    assert 5 not in mw.cache
