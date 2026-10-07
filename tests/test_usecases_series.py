from datetime import date, time

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, PermissionDenied
from bot.inventory.models import InventoryOrder, OrderStatus, ensure_cleat_compat
from bot.inventory.service import InventoryService
from bot.publishing.service import PublishService
from bot.schedule.models import Training, TrainingSeries, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput
from bot.usecases import end_series, join_training
from tests.factories import NOW, add_chat, add_item, add_level, add_user


async def published_series(session, messenger):
    """Серия по вторникам: 4 тренировки (20.10, 27.10, 3.11, 10.11); первая опубликована, в ней идёт участник 1."""
    admin = await add_user(session, 99, "admin")
    await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    series = await ScheduleService(session).create_series(
        admin,
        SeriesInput(
            kind="road", place="Парк", level_ids=[level.id], start_date=date(2026, 10, 20),
            start_time=time(19, 0), weekdays={2}, trainer_id=10,
        ),
        NOW,
    )
    first = (await session.scalars(select(Training).order_by(Training.starts_at))).first()
    await PublishService(session, messenger, send_delay=0).publish(first.id, NOW)
    await ensure_cleat_compat(session)
    await add_user(session, 1)
    await join_training(session, messenger, first.id, 1, NOW)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    await InventoryService(session).confirm(first.id, 1, [bike.id], NOW)
    return admin, series, first


async def test_end_series_announces_cancels_orders_and_closes_the_series(session, messenger):
    admin, series, first = await published_series(session, messenger)
    cancelled = await end_series(session, messenger, admin, series.id, NOW)
    assert len(cancelled) == 4
    assert {t.status for t in await session.scalars(select(Training))} == {TrainingStatus.CANCELLED}
    await session.refresh(series)
    assert series.end_date == date(2026, 10, 18)
    active = list(await session.scalars(select(InventoryOrder).where(InventoryOrder.status == OrderStatus.ACTIVE)))
    assert active == []
    assert "Отменена" in messenger.edits[-1].text
    assert [uid for uid, text in messenger.notifications if "отменена" in text] == [1]  # участнику одно уведомление


async def test_end_series_by_stranger_changes_nothing(session, messenger):
    _, series, first = await published_series(session, messenger)
    stranger = await add_user(session, 2, "member")
    edits = len(messenger.edits)
    with pytest.raises(PermissionDenied):
        await end_series(session, messenger, stranger, series.id, NOW)
    await session.refresh(first)
    await session.refresh(series)
    assert first.status == TrainingStatus.SCHEDULED
    assert series.end_date is None
    assert len(messenger.edits) == edits


async def test_end_series_twice_announces_once(session, messenger):
    admin, series, _ = await published_series(session, messenger)
    await end_series(session, messenger, admin, series.id, NOW)
    notes = len(messenger.notifications)
    with pytest.raises(InvalidAction):
        await end_series(session, messenger, admin, series.id, NOW)
    assert len(messenger.notifications) == notes
