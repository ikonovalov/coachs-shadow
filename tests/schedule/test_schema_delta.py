from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from bot.schedule.models import CancelReason, Training, TrainingStatus
from bot.schedule.service import ScheduleService
from tests.factories import NOW, add_level, add_training, add_user


def clone(t: Training) -> Training:
    return Training(
        series_id=t.series_id, starts_at=t.starts_at, duration_min=t.duration_min, trainer_id=t.trainer_id,
        place=t.place, kind=t.kind, capacity=t.capacity, status=TrainingStatus.SCHEDULED, levels=list(t.levels),
    )


async def make(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    return admin, await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=1))


async def test_duplicate_series_and_start_is_rejected(session):
    _, t = await make(session)
    session.add(clone(t))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_series_edit_cancelled_training_does_not_block_its_replacement(session):
    _, t = await make(session)
    t.status = TrainingStatus.CANCELLED
    t.cancel_reason = CancelReason.SERIES_EDIT
    await session.commit()
    session.add(clone(t))
    await session.commit()  # no error


async def test_user_cancelled_training_still_blocks_a_duplicate(session):
    _, t = await make(session)
    t.status = TrainingStatus.CANCELLED
    t.cancel_reason = CancelReason.USER
    await session.commit()
    session.add(clone(t))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_cancel_records_user_reason(session):
    admin, t = await make(session)
    await ScheduleService(session).cancel(admin, [t.id], NOW)
    assert t.cancel_reason == CancelReason.USER
