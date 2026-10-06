"""Парк инвентаря на уровне обработчиков: фейковые Message/CallbackQuery, настоящий FSMContext."""
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.core.errors import InvalidAction, PermissionDenied
from bot.inventory.service import InventoryService
from bot.telegram.handlers import park as h
from bot.telegram.states import ParkInput
from tests.factories import add_item, add_user
from tests.telegram.test_inventory_handlers import FakeCallback, FakeMessage, buttons


@pytest.fixture
def state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


async def items(session):
    return await InventoryService(session).list_items()


async def test_admin_adds_item_from_one_line(session, state):
    admin = await add_user(session, 1, "admin")
    await h.on_kind(FakeCallback("ad:pkk:bike"), state, admin)
    assert await state.get_state() == ParkInput.line.state
    msg = FakeMessage(text="Велосипед №7; m; 165; 178")
    await h.on_line(msg, state, session, admin)
    [item] = await items(session)
    assert (item.label, item.frame, item.height_min, item.height_max) == ("Велосипед №7", "M", 165, 178)
    assert msg.answers[-1][0] == "Добавлено: Велосипед №7 · рама M, рост 165–178 ✅"
    assert await state.get_state() is None


async def test_garbage_line_keeps_state_and_adds_nothing(session, state):
    admin = await add_user(session, 1, "admin")
    await h.on_kind(FakeCallback("ad:pkk:shoes"), state, admin)
    with pytest.raises(InvalidAction, match="Формат"):
        await h.on_line(FakeMessage(text="какая-то ерунда"), state, session, admin)
    assert await state.get_state() == ParkInput.line.state
    assert await state.get_data() == {"kind": "shoes"}
    assert await items(session) == []
    await h.on_line(FakeMessage(text="Туфли №5; 42; spd"), state, session, admin)  # повтор после подсказки
    assert [i.label for i in await items(session)] == ["Туфли №5"]


async def test_member_cannot_add_or_toggle(session, state):
    member = await add_user(session, 2)
    item = await add_item(session, "helmet", "Шлем №2", helmet_size="M")
    with pytest.raises(PermissionDenied):
        await h.on_kind(FakeCallback("ad:pkk:helmet"), state, member)
    # даже если состояние ввода как-то оказалось у участника, добавление проверяет права в сервисе
    await state.set_state(ParkInput.line)
    await state.update_data(kind="helmet")
    with pytest.raises(PermissionDenied):
        await h.on_line(FakeMessage(text="Шлем №3; L"), state, session, member)
    with pytest.raises(PermissionDenied):
        await h.on_toggle(FakeCallback(f"ad:pk:{item.id}:0"), session, member)
    assert [(i.label, i.active) for i in await items(session)] == [("Шлем №2", True)]


async def test_toggle_sets_target_state_and_repeated_tap_is_harmless(session, state):
    admin = await add_user(session, 1, "admin")
    item = await add_item(session, "pedals", "Педали №3", pedal_type="spd")
    cb = FakeCallback("ad:park")
    await h.on_park(cb, session, admin, state)
    assert cb.message.edits[-1].startswith("Парк инвентаря")
    for _ in range(2):  # двойной тап по «вывести» не возвращает единицу обратно
        cb = FakeCallback(f"ad:pk:{item.id}:0")
        await h.on_toggle(cb, session, admin)
    await session.refresh(item)
    assert item.active is False
    assert cb.answers[-1]["text"] == "Выведено из парка"
    text, kb = await h.park_screen(session)
    assert f"ad:pk:{item.id}:1" in buttons(kb)
