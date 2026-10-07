from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, PermissionDenied
from bot.jobs.models import ScheduledJob
from bot.schedule.models import CancelReason, Training, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput, TrainingEdit, series_to_input
from tests.factories import add_chat, add_chat_trainer, add_level, add_user

T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=2)


def utc(m, d, h=16):
    return datetime(2026, m, d, h, 0, tzinfo=timezone.utc)


async def weekly(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    svc = ScheduleService(session)
    series = await svc.create_series(
        admin,
        SeriesInput(kind="road", place="Парк", level_ids=[level.id], weekdays={2},
                    start_date=date(2026, 10, 6), start_time=time(19, 0), capacity=5),
        T0,
    )
    return admin, level, svc, series


async def all_trainings(session):
    return list(await session.scalars(select(Training).order_by(Training.starts_at, Training.id)))


async def publish_directly(session, starts):
    t = await session.scalar(select(Training).where(Training.starts_at == starts))
    t.published_at = T0
    await session.commit()
    return t


# ---------- правка серии ----------

async def test_place_change_replaces_published_and_regenerates_unpublished(session):
    admin, level, svc, series = await weekly(session)
    published = await publish_directly(session, utc(10, 13))
    edit = replace(series_to_input(series), place="Стадион")
    result = await svc.update_series(admin, series.id, edit, T1)

    rows = await all_trainings(session)
    live = [t for t in rows if t.status == TrainingStatus.SCHEDULED and t.starts_at > T1]
    assert [t.starts_at for t in live] == [utc(10, 13), utc(10, 20), utc(10, 27), utc(11, 3)]
    assert {t.place for t in live} == {"Стадион"}
    assert published.status == TrainingStatus.CANCELLED and published.cancel_reason == CancelReason.SERIES_EDIT
    assert len(result) == 1 and result[0].old_id == published.id
    new_13 = next(t for t in live if t.starts_at == utc(10, 13))
    assert result[0].new_id == new_13.id
    started = next(t for t in rows if t.starts_at == utc(10, 6))
    assert started.place == "Парк"  # начатая не меняется


async def test_unpublished_trainings_are_deleted_with_their_jobs(session):
    admin, level, svc, series = await weekly(session)
    await svc.update_series(admin, series.id, replace(series_to_input(series), place="Стадион"), T1)
    remaining_ids = {t.id for t in await all_trainings(session)}
    jobs = list(await session.scalars(select(ScheduledJob).where(ScheduledJob.training_id.is_not(None))))
    assert all(j.training_id in remaining_ids for j in jobs)  # нет висящих задач
    # SQLite переиспользует id удалённых строк, поэтому проверяем по содержимому
    assert not [t for t in await all_trainings(session) if t.place == "Парк" and t.starts_at > T1]


async def test_user_cancelled_training_is_not_resurrected_by_series_edit(session):
    admin, level, svc, series = await weekly(session)
    t20 = await session.scalar(select(Training).where(Training.starts_at == utc(10, 20)))
    await svc.cancel(admin, [t20.id], T1)
    await svc.update_series(admin, series.id, replace(series_to_input(series), place="Стадион"), T1)
    on_20 = [t for t in await all_trainings(session) if t.starts_at == utc(10, 20)]
    assert len(on_20) == 1 and on_20[0].status == TrainingStatus.CANCELLED


async def test_user_cancelled_training_is_not_resurrected_by_time_change(session):
    admin, level, svc, series = await weekly(session)
    t20 = await session.scalar(select(Training).where(Training.starts_at == utc(10, 20)))
    await svc.cancel(admin, [t20.id], T1)
    await svc.update_series(admin, series.id, replace(series_to_input(series), start_time=time(18, 0)), T1)
    on_20 = [t for t in await all_trainings(session) if t.starts_at.date() == date(2026, 10, 20)]
    assert [(t.starts_at, t.status) for t in on_20] == [(utc(10, 20), TrainingStatus.CANCELLED)]
    live = [t.starts_at for t in await all_trainings(session) if t.status == TrainingStatus.SCHEDULED and t.starts_at > T1]
    assert live == [utc(10, 13, 15), utc(10, 27, 15), utc(11, 3, 15)]


async def test_user_cancelled_training_is_not_resurrected_by_days_and_time_change(session):
    admin, level, svc, series = await weekly(session)
    t20 = await session.scalar(select(Training).where(Training.starts_at == utc(10, 20)))
    await svc.cancel(admin, [t20.id], T1)
    edit = replace(series_to_input(series), weekdays={2, 4}, start_time=time(18, 0))
    await svc.update_series(admin, series.id, edit, T1)
    on_20 = [t for t in await all_trainings(session) if t.starts_at.date() == date(2026, 10, 20)]
    assert [t.status for t in on_20] == [TrainingStatus.CANCELLED]
    live = {t.starts_at for t in await all_trainings(session) if t.status == TrainingStatus.SCHEDULED and t.starts_at > T1}
    assert utc(10, 22, 15) in live and utc(10, 13, 15) in live


async def test_weekday_change_leaves_published_training_without_a_pair(session):
    admin, level, svc, series = await weekly(session)
    old = await publish_directly(session, utc(10, 13))
    result = await svc.update_series(admin, series.id, replace(series_to_input(series), weekdays={4}), T1)
    assert [(r.old_id, r.new_id) for r in result] == [(old.id, None)]
    thursdays = [t for t in await all_trainings(session) if t.status == "scheduled" and t.starts_at > T1]
    assert all(t.starts_at.isoweekday() == 4 for t in thursdays)


async def test_only_owner_or_admin_edits_a_series(session):
    admin, level, svc, series = await weekly(session)
    chat = await add_chat(session, level)
    other = await add_user(session, 10, "trainer")
    await add_chat_trainer(session, chat, 10)
    with pytest.raises(PermissionDenied):
        await svc.update_series(other, series.id, series_to_input(series), T1)


async def test_trainer_edits_own_series_within_his_levels(session):
    level = await add_level(session)
    other_level = await add_level(session, "Другой", 1)
    chat = await add_chat(session, level)
    trainer = await add_user(session, 10, "trainer")
    await add_chat_trainer(session, chat, 10)
    svc = ScheduleService(session)
    series = await svc.create_series(
        trainer,
        SeriesInput(kind="road", place="Парк", level_ids=[level.id], weekdays={2},
                    start_date=date(2026, 10, 6), start_time=time(19, 0)),
        T0,
    )
    await svc.update_series(trainer, series.id, replace(series_to_input(series), place="Лес"), T1)
    with pytest.raises(PermissionDenied):
        await svc.update_series(trainer, series.id, replace(series_to_input(series), level_ids=[other_level.id]), T1)


async def test_invalid_series_edit_changes_nothing(session):
    admin, level, svc, series = await weekly(session)
    before = [(t.id, t.status) for t in await all_trainings(session)]
    with pytest.raises(InvalidAction):
        await svc.update_series(admin, series.id, replace(series_to_input(series), capacity=0), T1)
    assert [(t.id, t.status) for t in await all_trainings(session)] == before


# ---------- правка одной тренировки ----------

async def test_edit_single_training_does_not_touch_the_series(session):
    admin, level, svc, series = await weekly(session)
    t = await session.scalar(select(Training).where(Training.starts_at == utc(10, 13)))
    await svc.update_training(admin, t.id, TrainingEdit(place="Склон", description="Дождь", capacity=3), T1)
    assert (t.place, t.description, t.capacity) == ("Склон", "Дождь", 3)
    other = await session.scalar(select(Training).where(Training.starts_at == utc(10, 20)))
    assert other.place == "Парк"
    await session.refresh(series)
    assert series.place == "Парк"
    await svc.update_training(admin, t.id, TrainingEdit(clear_capacity=True), T1)
    assert t.capacity is None


async def test_edit_single_training_rules(session):
    admin, level, svc, series = await weekly(session)
    t = await session.scalar(select(Training).where(Training.starts_at == utc(10, 13)))
    member = await add_user(session, 1, "member")
    with pytest.raises(PermissionDenied):
        await svc.update_training(member, t.id, TrainingEdit(place="X"), T1)
    with pytest.raises(InvalidAction):
        await svc.update_training(admin, t.id, TrainingEdit(place="  "), T1)
    with pytest.raises(InvalidAction):
        await svc.update_training(admin, t.id, TrainingEdit(capacity=0), T1)
    with pytest.raises(InvalidAction):
        await svc.update_training(admin, t.id, TrainingEdit(place="X"), utc(10, 13, 17))  # уже началась


async def test_capacity_cannot_drop_below_signed_up_and_count_is_hidden_from_strangers(session):
    from bot.participation.models import Participation

    admin, level, svc, series = await weekly(session)
    t = await session.scalar(select(Training).where(Training.starts_at == utc(10, 13)))
    for uid in (1, 2, 3):
        await add_user(session, uid)
        session.add(Participation(training_id=t.id, user_id=uid, status="going", updated_at=T0))
    await session.commit()
    member = await add_user(session, 7, "member")
    with pytest.raises(PermissionDenied):  # права проверяются раньше, число записавшихся не раскрывается
        await svc.update_training(member, t.id, TrainingEdit(capacity=1), T1)
    with pytest.raises(InvalidAction, match="Уже записано 3"):
        await svc.update_training(admin, t.id, TrainingEdit(capacity=2), T1)
    assert t.capacity == 5
    await svc.update_training(admin, t.id, TrainingEdit(capacity=3), T1)
    assert t.capacity == 3


# ---------- списки ----------

async def test_allowed_levels(session):
    a = await add_level(session, "A", 0)
    b = await add_level(session, "B", 1)
    chat = await add_chat(session, a)
    trainer = await add_user(session, 10, "trainer")
    admin = await add_user(session, 99, "admin")
    await add_chat_trainer(session, chat, 10)
    svc = ScheduleService(session)
    assert [l.id for l in await svc.allowed_levels(trainer)] == [a.id]
    assert [l.id for l in await svc.allowed_levels(admin)] == [a.id, b.id]


async def test_managed_trainings_for_owner_lead_and_admin(session):
    level_a = await add_level(session, "A", 0)
    level_b = await add_level(session, "B", 1)
    chat_a = await add_chat(session, level_a, -1001)
    chat_b = await add_chat(session, level_b, -1002)
    owner = await add_user(session, 10, "trainer")
    lead = await add_user(session, 20, "trainer")
    admin = await add_user(session, 99, "admin")
    await add_chat_trainer(session, chat_a, 20, is_lead=True)
    await add_chat_trainer(session, chat_b, 10, is_lead=True)
    svc = ScheduleService(session)
    mk = lambda levels: SeriesInput(  # noqa: E731
        kind="road", place="Парк", level_ids=[l.id for l in levels],
        start_date=date(2026, 10, 20), start_time=time(19, 0),
    )
    await svc.create_series(admin, replace(mk([level_a]), trainer_id=10), T0)
    await svc.create_series(admin, replace(mk([level_a, level_b]), start_date=date(2026, 10, 21), trainer_id=10), T0)
    assert len(await svc.managed_trainings(owner, T0)) == 2
    assert len(await svc.managed_trainings(lead, T0)) == 1  # лидирует только в A
    assert len(await svc.managed_trainings(admin, T0)) == 2
    member = await add_user(session, 1, "member")
    assert await svc.managed_trainings(member, T0) == []
