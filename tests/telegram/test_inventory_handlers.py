"""Диалог заказа инвентаря на уровне обработчиков: фейковые Message/CallbackQuery, настоящий FSMContext."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from bot.core.errors import InvalidAction, PermissionDenied
from bot.inventory.models import InventoryOrder, OrderStatus, ensure_cleat_compat
from bot.inventory.service import InventoryService
from bot.participation.service import ParticipationService
from bot.telegram.handlers import inventory as h
from bot.telegram.states import InventoryFlow
from tests.factories import NOW, add_item, add_level, add_training, add_user


class FakeMessage:
    def __init__(self, chat_type="private", text=None):
        self.chat = SimpleNamespace(type=chat_type, id=1)
        self.text = text
        self.answers: list[tuple[str, object]] = []
        self.edits: list[str] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append(text)


class FakeBot:
    async def me(self):
        return SimpleNamespace(username="club_bot")


class FakeCallback:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or FakeMessage()
        self.bot = FakeBot()
        self.answers: list[dict] = []

    async def answer(self, text=None, show_alert=False, url=None):
        self.answers.append({"text": text, "alert": show_alert, "url": url})


def buttons(reply_markup) -> list[str]:
    return [b.callback_data for row in reply_markup.inline_keyboard for b in row]


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(h, "utcnow", lambda: NOW)


@pytest.fixture
def state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


async def world(session, going=True, **profile):
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20), published_at=NOW
    )
    await ensure_cleat_compat(session)
    user = await add_user(session, 1)
    for key, value in profile.items():
        setattr(user, key, value)
    await session.commit()
    if going:
        await ParticipationService(session).join(t.id, 1, NOW)
    return t, user


async def tap(data, state, session, user, handler, message=None):
    cb = FakeCallback(data, message)
    await handler(cb, state, session, user)
    return cb


async def active_orders(session, user_id):
    return list(await session.scalars(
        select(InventoryOrder).where(InventoryOrder.user_id == user_id, InventoryOrder.status == OrderStatus.ACTIVE)
    ))


async def test_not_going_rider_is_offered_to_sign_up_first(session, state):
    t, user = await world(session, going=False)
    msg = FakeMessage()
    await h.begin_inventory(msg, state, session, user, t.id)
    text, kb = msg.answers[-1]
    assert text == "Сначала запишитесь на тренировку"
    assert buttons(kb) == [f"join:{t.id}"]
    assert await state.get_state() is None


async def test_group_button_sends_the_rider_to_private_chat(session, state):
    t, user = await world(session)
    cb = FakeCallback(f"inv:{t.id}", FakeMessage(chat_type="supergroup"))
    await h.on_inventory_button(cb, state, session, user)
    assert cb.answers[-1]["url"] == f"https://t.me/club_bot?start=inv_{t.id}"
    assert await state.get_state() is None


async def test_own_shoes_incompatible_with_pedals_reasks_pedals_then_orders(session, state):
    t, user = await world(session)
    spd = await add_item(session, "pedals", "Педали SPD", pedal_type="spd")
    await add_item(session, "pedals", "Педали Look", pedal_type="look")
    msg = FakeMessage()
    await h.begin_inventory(msg, state, session, user, t.id)
    await h.on_kind_toggle(FakeCallback("iv:k:pedals", msg), state)
    await tap("iv:kdone", state, session, user, h.on_kinds_done, msg)
    assert msg.answers[-1][0] == "Какие педали?"
    await tap("iv:p:look", state, session, user, h.on_pedal, msg)
    assert msg.answers[-1][0] == "Туфли свои или из парка?"
    await tap("iv:s:own", state, session, user, h.on_shoes_source, msg)
    await tap("iv:c:spd", state, session, user, h.on_cleat, msg)
    assert user.cleat_type == "spd"  # сохранено в профиль
    assert msg.answers[-2][0] == "Ваши туфли (SPD) не подходят к педалям Look. Выберите педали заново."
    assert msg.answers[-1][0] == "Какие педали?"
    await tap("iv:p:spd", state, session, user, h.on_pedal, msg)
    assert await state.get_state() == InventoryFlow.review.state
    assert "Педали SPD" in msg.answers[-1][0]
    cb = await tap("iv:ok", state, session, user, h.on_confirm, msg)
    assert [o.item_id for o in await active_orders(session, 1)] == [spd.id]
    assert msg.edits[-1].startswith("✅ Заказано: Педали SPD")
    assert await state.get_state() is None
    # повторный тап по уже обработанной кнопке ничего не заказывает
    again = await tap("iv:ok", state, session, user, h.on_confirm, msg)
    assert again.answers[-1]["alert"] is True
    assert cb.answers[-1]["text"] == "Заказано"


async def test_taken_unit_is_replaced_only_for_its_kind_and_stays_compatible_with_confirmed_pedals(session, state):
    t, user = await world(session, foot_eu=42)
    pedals = await add_item(session, "pedals", "Педали SPD", pedal_type="spd")
    shoes_a = await add_item(session, "shoes", "Туфли A", shoe_eu=42, cleat_type="spd")
    await add_item(session, "shoes", "Туфли Look", shoe_eu=42, cleat_type="look")  # размер точный, шипы не те
    shoes_c = await add_item(session, "shoes", "Туфли C", shoe_eu=43, cleat_type="spd")
    other = await add_user(session, 2)
    await ParticipationService(session).join(t.id, 2, NOW)

    msg = FakeMessage()
    await h.begin_inventory(msg, state, session, user, t.id)
    await h.on_kind_toggle(FakeCallback("iv:k:pedals", msg), state)
    await h.on_kind_toggle(FakeCallback("iv:k:shoes", msg), state)
    await tap("iv:kdone", state, session, user, h.on_kinds_done, msg)
    await tap("iv:p:spd", state, session, user, h.on_pedal, msg)
    assert await state.get_state() == InventoryFlow.review.state  # туфли из парка выбраны — вопрос не нужен
    assert "Туфли A" in msg.answers[-1][0]

    await InventoryService(session).confirm(t.id, other.telegram_id, [shoes_a.id], NOW)  # кто-то успел раньше
    await tap("iv:ok", state, session, user, h.on_confirm, msg)
    assert [o.item_id for o in await active_orders(session, 1)] == [pedals.id]  # педали остались за участником
    review = msg.answers[-1][0]
    assert review.startswith("Эту единицу только что забрали")
    assert "Туфли C" in review and "Туфли Look" not in review  # замена совместима с уже взятыми педалями SPD
    assert "Уже заказано: Педали SPD" in review

    await tap("iv:ok", state, session, user, h.on_confirm, msg)
    assert sorted(o.item_id for o in await active_orders(session, 1)) == sorted([pedals.id, shoes_c.id])
    assert msg.edits[-1].startswith("✅ Заказано: Педали SPD, Туфли C")


async def test_profile_text_garbage_keeps_the_question_and_non_text_is_reprompted(session, state):
    t, user = await world(session)
    await add_item(session, "bike", "Вел M", frame="M", height_min=165, height_max=180)
    msg = FakeMessage()
    await h.begin_inventory(msg, state, session, user, t.id)
    await h.on_kind_toggle(FakeCallback("iv:k:bike", msg), state)
    await tap("iv:kdone", state, session, user, h.on_kinds_done, msg)
    assert await state.get_state() == InventoryFlow.profile.state

    with pytest.raises(InvalidAction):  # ответ пользователю формирует on_domain_error
        await h.on_profile_text(FakeMessage(text="высокий"), state, session, user)
    assert await state.get_state() == InventoryFlow.profile.state
    sticker = FakeMessage(text=None)
    await h.on_profile_not_text(sticker, state)
    assert sticker.answers[-1][0] == "Ваш рост в сантиметрах, например 175:"

    reply = FakeMessage(text="175")
    await h.on_profile_text(reply, state, session, user)
    assert user.height_cm == 175
    assert await state.get_state() == InventoryFlow.review.state
    assert "Вел M" in reply.answers[-1][0]


async def test_cancel_after_partial_order_reports_what_was_ordered(session, state):
    await state.set_state(InventoryFlow.review)
    await state.update_data(confirmed=["Педали SPD"], not_found=[])
    msg = FakeMessage()
    await h.on_cancel(FakeCallback("iv:no", msg), state)
    assert msg.edits[-1].startswith("✅ Заказано: Педали SPD")
    assert await state.get_data() == {}


async def test_trainer_summary_checks_rights_in_the_service(session, state):
    t, user = await world(session)
    trainer = await session.get(type(user), 10)
    cb = FakeCallback(f"tr:inv:{t.id}")
    await h.on_trainer_summary(cb, session, trainer)
    assert cb.message.edits
    with pytest.raises(PermissionDenied):
        await h.on_trainer_summary(FakeCallback(f"tr:inv:{t.id}"), session, user)


async def test_stale_cancel_after_confirm_does_not_claim_the_order_is_cancelled(session, state):
    t, user = await world(session)
    spd = await add_item(session, "pedals", "Педали SPD", pedal_type="spd")
    kinds_msg, msg = FakeMessage(), FakeMessage()
    await h.begin_inventory(kinds_msg, state, session, user, t.id)
    await h.on_kind_toggle(FakeCallback("iv:k:pedals", kinds_msg), state)
    await tap("iv:kdone", state, session, user, h.on_kinds_done, msg)
    await tap("iv:p:spd", state, session, user, h.on_pedal, msg)
    await tap("iv:s:own", state, session, user, h.on_shoes_source, msg)
    await tap("iv:c:spd", state, session, user, h.on_cleat, msg)
    await tap("iv:ok", state, session, user, h.on_confirm, msg)
    assert [o.item_id for o in await active_orders(session, 1)] == [spd.id]

    old = FakeCallback("iv:no", kinds_msg)  # «Отмена» на экране видов из того же, уже завершённого диалога
    await h.on_cancel(old, state)
    assert kinds_msg.edits == [h.KINDS_TEXT]  # только перерисовка при выборе вида, без «отменён»
    assert old.answers[-1]["alert"] is True and "/my_bookings" in old.answers[-1]["text"]
    assert [o.item_id for o in await active_orders(session, 1)] == [spd.id]


async def test_shoes_ordered_after_pedals_are_compatible_with_the_earlier_pedals(session, state):
    t, user = await world(session, foot_eu=42)
    pedals = await add_item(session, "pedals", "Педали SPD", pedal_type="spd")
    await add_item(session, "shoes", "Туфли Look", shoe_eu=42, cleat_type="look")  # точный размер, шипы не те
    spd_shoes = await add_item(session, "shoes", "Туфли SPD", shoe_eu=43, cleat_type="spd")
    await InventoryService(session).confirm(t.id, 1, [pedals.id], NOW)  # заказ из прошлого диалога

    msg = FakeMessage()
    await h.begin_inventory(msg, state, session, user, t.id)
    await h.on_kind_toggle(FakeCallback("iv:k:shoes", msg), state)
    await tap("iv:kdone", state, session, user, h.on_kinds_done, msg)
    review = msg.answers[-1][0]
    assert "Туфли SPD" in review and "Туфли Look" not in review
    await tap("iv:ok", state, session, user, h.on_confirm, msg)
    assert sorted(o.item_id for o in await active_orders(session, 1)) == sorted([pedals.id, spd_shoes.id])


async def test_pedals_ordered_after_park_shoes_are_checked_against_them(session, state):
    t, user = await world(session, foot_eu=42)
    shoes = await add_item(session, "shoes", "Туфли SPD", shoe_eu=42, cleat_type="spd")
    await add_item(session, "pedals", "Педали Look", pedal_type="look")
    await InventoryService(session).confirm(t.id, 1, [shoes.id], NOW)

    msg = FakeMessage()
    await h.begin_inventory(msg, state, session, user, t.id)
    await h.on_kind_toggle(FakeCallback("iv:k:pedals", msg), state)
    await tap("iv:kdone", state, session, user, h.on_kinds_done, msg)
    await tap("iv:p:look", state, session, user, h.on_pedal, msg)  # вопрос «свои или из парка» не задаётся
    assert msg.answers[-2][0].endswith("не подходят к педалям Look. Выберите педали заново.")
    assert msg.answers[-1][0] == "Какие педали?"
