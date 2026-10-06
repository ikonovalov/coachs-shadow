from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from bot.inventory.models import InventoryOrder, OrderStatus, ensure_cleat_compat
from bot.inventory.service import InventoryService
from bot.publishing.service import PublishService
from bot.schedule.models import Training
from bot.schedule.service import ScheduleService, SeriesInput, series_to_input
from bot.usecases import (
    cancel_trainings, decline_training, join_training, manual_remove_participant, update_series,
)
from tests.factories import NOW, add_chat, add_item, add_level, add_training, add_user

STARTS = NOW + timedelta(hours=20)


async def ordered(session, messenger, capacity=5):
    admin = await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS, capacity=capacity)
    await PublishService(session, messenger, send_delay=0).publish(t.id, NOW)
    await ensure_cleat_compat(session)
    await add_user(session, 1)
    await join_training(session, messenger, t.id, 1, NOW)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    await InventoryService(session).confirm(t.id, 1, [bike.id, helmet.id], NOW)
    return admin, trainer, t, bike, helmet


async def active_orders(session):
    return list(await session.scalars(select(InventoryOrder).where(InventoryOrder.status == OrderStatus.ACTIVE)))


async def test_decline_cancels_orders_and_notifies(session, messenger):
    _, _, t, bike, helmet = await ordered(session, messenger)
    await decline_training(session, messenger, t.id, 1, NOW)
    assert await active_orders(session) == []
    (text,) = [txt for uid, txt in messenger.notifications if uid == 1 and "инвентар" in txt.lower()]
    assert "Велосипед №1" in text and "Шлем №1" in text


async def test_decline_without_orders_sends_no_inventory_notice(session, messenger):
    admin = await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    await PublishService(session, messenger, send_delay=0).publish(t.id, NOW)
    await add_user(session, 1)
    await join_training(session, messenger, t.id, 1, NOW)
    await decline_training(session, messenger, t.id, 1, NOW)
    assert not any("инвентар" in txt.lower() for _, txt in messenger.notifications)


async def test_manual_remove_also_cancels_orders(session, messenger):
    admin, _, t, *_ = await ordered(session, messenger)
    await manual_remove_participant(session, messenger, admin, t.id, 1, NOW)
    assert await active_orders(session) == []


async def test_trainer_cancel_frees_every_order_and_the_items(session, messenger):
    admin, _, t, bike, helmet = await ordered(session, messenger)
    await cancel_trainings(session, messenger, admin, [t.id], NOW)
    assert await active_orders(session) == []
    other = await add_training(
        session, trainer=admin, levels=list((await session.get(Training, t.id)).levels), starts_at=STARTS,
        published_at=NOW,
    )
    await add_user(session, 2)
    from bot.participation.service import ParticipationService

    await ParticipationService(session).join(other.id, 2, NOW)
    result = await InventoryService(session).confirm(other.id, 2, [bike.id], NOW)
    assert result.conflicts == []  # единица снова свободна


async def test_series_edit_cancels_orders_of_replaced_trainings(session, messenger):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
    T1 = T0 + timedelta(days=2)
    svc = ScheduleService(session)
    series = await svc.create_series(
        admin,
        SeriesInput(kind="intervals", place="Парк", level_ids=[level.id], weekdays={2},
                    start_date=date(2026, 10, 6), start_time=time(19, 0)),
        T0,
    )
    t13 = await session.scalar(select(Training).where(Training.starts_at == datetime(2026, 10, 13, 16, tzinfo=timezone.utc)))
    await PublishService(session, messenger, send_delay=0).publish(t13.id, T1)
    await ensure_cleat_compat(session)
    await add_user(session, 1)
    await join_training(session, messenger, t13.id, 1, T1)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    await InventoryService(session).confirm(t13.id, 1, [helmet.id], T1)
    await update_series(session, messenger, admin, series.id, replace(series_to_input(series), place="Стадион"), T1)
    assert await active_orders(session) == []
