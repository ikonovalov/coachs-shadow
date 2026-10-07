from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.jobs.models import ScheduledJob
from bot.schedule.models import CancelReason, Training, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput
from tests.factories import add_level, add_user

T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)  # вторник, 13:00 в Москве


async def make_series(session, *, weekdays=(2, 4), start=date(2026, 10, 6), trainer_id=None, now=T0):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    series = await ScheduleService(session).create_series(
        admin,
        SeriesInput(
            kind="road", place="Парк", level_ids=[level.id], start_date=start,
            start_time=time(19, 0), weekdays=set(weekdays), trainer_id=trainer_id,
        ),
        now,
    )
    return admin, series


async def all_trainings(session):
    return list(await session.scalars(select(Training).order_by(Training.starts_at)))


async def test_end_series_cancels_future_trainings_and_stops_generation(session):
    admin, series = await make_series(session)
    svc = ScheduleService(session)
    cancelled = await svc.end_series(admin, series.id, T0)
    assert len(cancelled) == 8  # вт и чт на 4 недели вперёд
    rows = await all_trainings(session)
    assert {t.status for t in rows} == {TrainingStatus.CANCELLED}
    assert {t.cancel_reason for t in rows} == {CancelReason.USER}
    await session.refresh(series)
    assert series.end_date == date(2026, 10, 5)
    assert await svc.generate(T0 + timedelta(days=1)) == []
    assert len(await all_trainings(session)) == 8


async def test_end_series_removes_pending_jobs(session):
    admin, series = await make_series(session)
    await ScheduleService(session).end_series(admin, series.id, T0)
    pending = list(await session.scalars(select(ScheduledJob).where(ScheduledJob.done_at.is_(None))))
    assert pending == []


async def test_end_series_keeps_past_trainings(session):
    admin, series = await make_series(session)
    now = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)  # среда: 6, 8 и 13 октября уже прошли
    cancelled = await ScheduleService(session).end_series(admin, series.id, now)
    assert len(cancelled) == 5  # 15, 20, 22, 27, 29 октября
    rows = await all_trainings(session)
    past = [t for t in rows if t.starts_at <= now]
    assert len(past) == 3 and {t.status for t in past} == {TrainingStatus.SCHEDULED}
    await session.refresh(series)
    assert series.end_date == date(2026, 10, 13)


async def test_end_series_by_owner_but_not_by_other_trainer(session):
    await add_user(session, 10, "trainer")
    other = await add_user(session, 11, "trainer")
    admin, series = await make_series(session, trainer_id=10)
    svc = ScheduleService(session)
    with pytest.raises(PermissionDenied):
        await svc.end_series(other, series.id, T0)
    await session.refresh(series)
    assert series.end_date is None
    assert {t.status for t in await all_trainings(session)} == {TrainingStatus.SCHEDULED}
    owner = await session.get(type(other), 10)
    assert len(await svc.end_series(owner, series.id, T0)) == 8


async def test_end_series_twice_and_unknown_and_one_off(session):
    admin, series = await make_series(session)
    svc = ScheduleService(session)
    await svc.end_series(admin, series.id, T0)
    with pytest.raises(InvalidAction, match="уже завершена"):
        await svc.end_series(admin, series.id, T0)
    with pytest.raises(NotFound):
        await svc.end_series(admin, 12345, T0)
    level = await add_level(session, "Другой", 1)
    one_off = await svc.create_series(
        admin,
        SeriesInput(kind="road", place="Парк", level_ids=[level.id],
                    start_date=date(2026, 10, 20), start_time=time(19, 0)),
        T0,
    )
    with pytest.raises(InvalidAction, match="разовая"):
        await svc.end_series(admin, one_off.id, T0)


async def test_end_series_without_future_trainings_still_ends_it(session):
    admin, series = await make_series(session)
    later = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)  # генератор до сих пор не заходил в ноябрь
    cancelled = await ScheduleService(session).end_series(admin, series.id, later)
    assert cancelled == []
    await session.refresh(series)
    assert series.end_date == date(2026, 11, 19)


async def test_end_series_before_first_training(session):
    admin, series = await make_series(session, weekdays=(2,), start=date(2026, 10, 27))
    svc = ScheduleService(session)
    cancelled = await svc.end_series(admin, series.id, T0)
    assert len(cancelled) == 1  # единственная сгенерированная: 27 октября
    await session.refresh(series)
    got = series
    assert got.end_date == date(2026, 10, 5) and got.end_date < got.start_date
    assert await svc.generate(T0 + timedelta(days=2)) == []


async def test_end_series_uses_club_timezone_for_today(session):
    admin, series = await make_series(session)
    late = datetime(2026, 10, 6, 22, 30, tzinfo=timezone.utc)  # в Москве уже 7 октября, 01:30
    await ScheduleService(session).end_series(admin, series.id, late)
    await session.refresh(series)
    assert series.end_date == date(2026, 10, 6)


async def test_active_series_scope_and_filters(session):
    await add_user(session, 10, "trainer")
    mine = await add_user(session, 11, "trainer")
    admin, series_a = await make_series(session, trainer_id=10)
    svc = ScheduleService(session)
    level = await add_level(session, "Другой", 1)
    series_b = await svc.create_series(
        admin,
        SeriesInput(kind="road", place="Стадион", level_ids=[level.id], start_date=date(2026, 10, 6),
                    start_time=time(19, 0), weekdays={3}, trainer_id=11),
        T0,
    )
    await svc.create_series(  # разовая в список не попадает
        admin,
        SeriesInput(kind="road", place="Разовая", level_ids=[level.id],
                    start_date=date(2026, 10, 20), start_time=time(19, 0), trainer_id=11),
        T0,
    )
    assert [s.id for s in await svc.active_series(admin, T0)] == [series_a.id, series_b.id]
    assert [s.id for s in await svc.active_series(mine, T0)] == [series_b.id]
    member = await add_user(session, 1, "member")
    assert await svc.active_series(member, T0) == []
    await svc.end_series(admin, series_a.id, T0)
    assert [s.id for s in await svc.active_series(admin, T0)] == [series_b.id]


async def test_future_training_count(session):
    admin, series = await make_series(session)
    svc = ScheduleService(session)
    assert await svc.future_training_count(series.id, T0) == 8
    assert await svc.future_training_count(series.id, datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)) == 5
    await svc.end_series(admin, series.id, T0)
    assert await svc.future_training_count(series.id, T0) == 0
