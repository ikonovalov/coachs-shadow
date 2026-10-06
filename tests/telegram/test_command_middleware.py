from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from bot.telegram.middleware import clear_state_on_command
from bot.telegram.states import EditField, NewTraining


def make_state() -> FSMContext:
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=5, user_id=5))


async def run(text, state):
    seen = []

    async def handler(event, data):
        seen.append(await data["state"].get_state())
        return "handled"

    event = Message.model_construct(text=text)
    result = await clear_state_on_command(handler, event, {"state": state})
    return result, seen


async def test_command_clears_any_form_state_and_falls_through():
    state = make_state()
    await state.set_state(EditField.value)
    await state.update_data(target="training", id=1, field="place")
    result, seen = await run("/schedule", state)
    assert result == "handled" and seen == [None]  # handler ran, already without the form state
    assert await state.get_data() == {}


async def test_plain_text_keeps_the_state():
    state = make_state()
    await state.set_state(NewTraining.place)
    result, seen = await run("Парк", state)
    assert seen == [NewTraining.place.state]


async def test_messages_without_text_or_state_pass_through():
    result, seen = await run(None, make_state())
    assert result == "handled"

    async def handler(event, data):
        return "ok"

    assert await clear_state_on_command(handler, Message.model_construct(text="/start"), {}) == "ok"


async def test_dispatcher_level_registration_runs_before_child_routers():
    """Wired like bot.main: the handler of a command in a child router already sees the form dropped."""
    import datetime

    from aiogram import Bot, Dispatcher, Router
    from aiogram.filters import Command
    from aiogram.types import Chat, Update, User

    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(clear_state_on_command)
    child = Router()
    seen = []

    @child.message(Command("whoami"))
    async def whoami(message, state):
        seen.append(await state.get_state())

    dp.include_router(child)
    bot = Bot("42:TEST")
    try:
        ctx = dp.fsm.get_context(bot, chat_id=5, user_id=5)
        await ctx.set_state(EditField.value)
        msg = Message(message_id=1, date=datetime.datetime.now(), chat=Chat(id=5, type="private"),
                      from_user=User(id=5, is_bot=False, first_name="A"), text="/whoami")
        await dp.feed_update(bot, Update(update_id=1, message=msg))
    finally:
        await bot.session.close()
    assert seen == [None]
