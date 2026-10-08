import pytest
from sqlalchemy import select

from bot.billing.models import Charge, ChargeStatus
from bot.billing.service import BillingService
from bot.core.errors import InvalidAction, PermissionDenied
from bot.telegram.handlers import attendance
from tests.billing.helpers import done_training, ensure_user
from tests.factories import NOW
from tests.telegram.fakes import FakeCallback, callback_data


async def setup(session):
    t = await done_training(session, going=[1, 2])
    await BillingService(session).charge_training(t.id, NOW)
    return t, await ensure_user(session, 10, "trainer")


async def test_open_shows_roster_with_toggle_buttons(session):
    t, trainer = await setup(session)
    cb = FakeCallback(f"att:o:{t.id}")
    await attendance.on_open(cb, session, trainer)
    text, markup = cb.message.edits[-1]
    assert callback_data(markup) == [f"att:t:{t.id}:1:0", f"att:t:{t.id}:2:0", f"att:add:{t.id}", "att:done"]
    assert markup.inline_keyboard[0][0].text.startswith("✅ ")


async def test_toggle_marks_absent_and_redraws(session, messenger):
    t, trainer = await setup(session)
    cb = FakeCallback(f"att:t:{t.id}:1:0")
    await attendance.on_toggle(cb, session, messenger, trainer)
    markup = cb.message.edits[-1][1]
    assert markup.inline_keyboard[0][0].text.startswith("❌ ")
    assert callback_data(markup)[0] == f"att:t:{t.id}:1:1"
    assert (await session.scalars(select(Charge).where(Charge.user_id == 1))).first().status == ChargeStatus.VOID
    assert any(uid == 1 and "не засчитано" in text for uid, text in messenger.notifications)


async def test_repeated_tap_with_stale_target_is_harmless(session, messenger):
    t, trainer = await setup(session)
    await attendance.on_toggle(FakeCallback(f"att:t:{t.id}:1:0"), session, messenger, trainer)
    sent = len(messenger.notifications)
    await attendance.on_toggle(FakeCallback(f"att:t:{t.id}:1:0"), session, messenger, trainer)  # тот же целевой статус
    assert len(messenger.notifications) == sent


async def test_foreign_trainer_cannot_open_or_toggle(session, messenger):
    t, _ = await setup(session)
    other = await ensure_user(session, 11, "trainer")
    with pytest.raises(PermissionDenied):
        await attendance.on_open(FakeCallback(f"att:o:{t.id}"), session, other)
    with pytest.raises(PermissionDenied):
        await attendance.on_toggle(FakeCallback(f"att:t:{t.id}:1:0"), session, messenger, other)


async def test_done_closes_the_screen(session):
    cb = FakeCallback("att:done")
    await attendance.on_done(cb)
    assert "сохранена" in cb.message.edits[-1][0]


async def test_walk_in_pick_marks_present(session, messenger):
    t, trainer = await setup(session)
    await ensure_user(session, 5)
    cb = FakeCallback("pk:5")
    await attendance.picked_walk_in(cb, session, trainer, messenger, {"tid": t.id}, 5)
    assert cb.message.answers[-1][1].inline_keyboard[2][0].callback_data == "att:t:%d:5:0" % t.id
