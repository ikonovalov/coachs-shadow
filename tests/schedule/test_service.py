from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput
from tests.factories import NOW, add_chat, add_chat_trainer, add_level, add_training, add_user


def inp(level_ids, **kw):
    base = dict(
        kind="intervals", place="Парк", level_ids=level_ids,
        start_date=date(2026, 10, 20), start_time=time(19, 0),
    )
    base.update(kw)
    return SeriesInput(**base)


async def setup_admin_and_level(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    return admin, level


async def trainings(session):
    return list(await session.scalars(select(Training).order_by(Training.starts_at)))


# ---------- levels and chats ----------

async def test_only_admin_adds_levels_and_binds_chats(session):
    admin = await add_user(session, 99, "admin")
    member = await add_user(session, 1, "member")
    svc = ScheduleService(session)
    with pytest.raises(PermissionDenied):
        await svc.add_level(member, "Новички")
    level = await svc.add_level(admin, "Новички")
    chat = await svc.bind_chat(admin, -1001, "Новички клуба", "Новички")
    assert chat.level_id == level.id
    with pytest.raises(NotFound):
        await svc.bind_chat(admin, -1002, "x", "Нет такого")


async def test_rebinding_a_chat_changes_its_level(session):
    admin = await add_user(session, 99, "admin")
    svc = ScheduleService(session)
    await svc.add_level(admin, "A")
    b = await svc.add_level(admin, "B")
    await svc.bind_chat(admin, -1001, "чат", "A")
    chat = await svc.bind_chat(admin, -1001, "чат", "B")
    assert chat.level_id == b.id


# ---------- creating series ----------

async def test_one_off_creates_one_training_with_jobs(session):
    admin, level = await setup_admin_and_level(session)
    await ScheduleService(session).create_series(admin, inp([level.id]), NOW)
    (t,) = await trainings(session)
    assert t.starts_at == datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    assert t.duration_min == 90
    jobs = {j.kind: j for j in await session.scalars(select(ScheduledJob).where(ScheduledJob.training_id == t.id))}
    assert jobs[JobKind.PUBLISH].run_at == t.starts_at - timedelta(hours=24)  # exactly 24 h ahead
    assert jobs[JobKind.REMIND_2H].run_at == t.starts_at - timedelta(hours=2)
    assert jobs[JobKind.CLOSE].run_at == t.starts_at


async def test_training_less_than_24h_ahead_is_published_immediately(session):
    admin, level = await setup_admin_and_level(session)
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)  # starts in 6 h
    await ScheduleService(session).create_series(admin, inp([level.id]), now)
    (t,) = await trainings(session)
    publish = await session.scalar(
        select(ScheduledJob).where(ScheduledJob.training_id == t.id, ScheduledJob.kind == JobKind.PUBLISH)
    )
    assert publish.run_at == now


async def test_weekly_series_generates_four_weeks(session):
    admin, level = await setup_admin_and_level(session)
    now = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)  # Tuesday
    await ScheduleService(session).create_series(
        admin, inp([level.id], weekdays={2, 4}, start_date=date(2026, 10, 6)), now
    )
    assert len(await trainings(session)) == 8  # Oct 6,8,13,15,20,22,27,29


async def test_generate_twice_creates_no_duplicates_and_adds_new_days(session):
    admin, level = await setup_admin_and_level(session)
    now = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
    svc = ScheduleService(session)
    await svc.create_series(admin, inp([level.id], weekdays={2, 4}, start_date=date(2026, 10, 6)), now)
    assert await svc.generate(now) == []
    created = await svc.generate(now + timedelta(days=1))
    assert len(created) == 1  # Tue Nov 3 entered the 4-week window
    assert len(await trainings(session)) == 9


async def test_cancelled_training_is_not_resurrected_by_generation(session):
    admin, level = await setup_admin_and_level(session)
    svc = ScheduleService(session)
    await svc.create_series(admin, inp([level.id]), NOW)
    (t,) = await trainings(session)
    await svc.cancel(admin, [t.id], NOW)
    await svc.generate(NOW + timedelta(hours=1))
    rows = await trainings(session)
    assert len(rows) == 1 and rows[0].status == TrainingStatus.CANCELLED


async def test_series_validation(session):
    admin, level = await setup_admin_and_level(session)
    svc = ScheduleService(session)
    for bad in (
        inp([]),
        inp([level.id], place="  "),
        inp([level.id], kind="yoga"),
        inp([level.id], capacity=0),
        inp([level.id], weekdays={0, 8}),
        inp([level.id], end_date=date(2026, 10, 1)),
        inp([level.id], start_date=date(2026, 10, 1)),  # one-off in the past
    ):
        with pytest.raises(InvalidAction):
            await svc.create_series(admin, bad, NOW)
    with pytest.raises(NotFound):
        await svc.create_series(admin, inp([level.id, 12345]), NOW)
    assert await trainings(session) == []


async def test_trainer_can_create_only_for_levels_of_own_chats(session):
    level_a = await add_level(session, "A")
    level_b = await add_level(session, "B", 1)
    chat_a = await add_chat(session, level_a, -1001)
    trainer = await add_user(session, 10, "trainer")
    await add_chat_trainer(session, chat_a, 10)
    svc = ScheduleService(session)
    await svc.create_series(trainer, inp([level_a.id]), NOW)
    with pytest.raises(PermissionDenied):
        await svc.create_series(trainer, inp([level_a.id, level_b.id], start_date=date(2026, 10, 21)), NOW)


async def test_trainer_without_chats_gets_explanatory_error(session):
    level = await add_level(session)
    trainer = await add_user(session, 10, "trainer")
    with pytest.raises(PermissionDenied, match="не добавлены ни в один чат"):
        await ScheduleService(session).create_series(trainer, inp([level.id]), NOW)


async def test_member_cannot_create(session):
    level = await add_level(session)
    member = await add_user(session, 1, "member")
    with pytest.raises(PermissionDenied):
        await ScheduleService(session).create_series(member, inp([level.id]), NOW)


# ---------- cancel ----------

async def test_cancel_sets_status_and_removes_pending_jobs(session):
    admin, level = await setup_admin_and_level(session)
    svc = ScheduleService(session)
    await svc.create_series(admin, inp([level.id]), NOW)
    (t,) = await trainings(session)
    await svc.cancel(admin, [t.id], NOW)
    await session.refresh(t)
    assert t.status == TrainingStatus.CANCELLED
    pending = list(
        await session.scalars(
            select(ScheduledJob).where(ScheduledJob.training_id == t.id, ScheduledJob.done_at.is_(None))
        )
    )
    assert pending == []


async def test_cancel_is_all_or_nothing(session):
    admin, level = await setup_admin_and_level(session)
    future = await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=1))
    started = await add_training(session, trainer=admin, levels=[level], starts_at=NOW - timedelta(hours=1))
    svc = ScheduleService(session)
    with pytest.raises(InvalidAction):
        await svc.cancel(admin, [future.id, started.id], NOW)
    await session.refresh(future)
    assert future.status == TrainingStatus.SCHEDULED


async def test_cancelling_twice_is_rejected(session):
    admin, level = await setup_admin_and_level(session)
    t = await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=1))
    svc = ScheduleService(session)
    await svc.cancel(admin, [t.id], NOW)
    with pytest.raises(InvalidAction):
        await svc.cancel(admin, [t.id], NOW)


async def test_trainer_cancels_own_but_not_foreign(session):
    level = await add_level(session)
    me = await add_user(session, 10, "trainer")
    other = await add_user(session, 11, "trainer")
    mine = await add_training(session, trainer=me, levels=[level], starts_at=NOW + timedelta(days=1))
    theirs = await add_training(session, trainer=other, levels=[level], starts_at=NOW + timedelta(days=2))
    svc = ScheduleService(session)
    await svc.cancel(me, [mine.id], NOW)
    with pytest.raises(PermissionDenied):
        await svc.cancel(me, [theirs.id], NOW)


async def test_lead_must_lead_every_target_chat(session):
    level_a = await add_level(session, "A")
    level_b = await add_level(session, "B", 1)
    chat_a = await add_chat(session, level_a, -1001)
    chat_b = await add_chat(session, level_b, -1002)
    owner = await add_user(session, 10, "trainer")
    lead = await add_user(session, 20, "trainer")
    await add_chat_trainer(session, chat_a, 20, is_lead=True)  # leads A only
    await add_chat_trainer(session, chat_b, 10, is_lead=True)
    only_a = await add_training(session, trainer=owner, levels=[level_a], starts_at=NOW + timedelta(days=1))
    a_and_b = await add_training(session, trainer=owner, levels=[level_a, level_b], starts_at=NOW + timedelta(days=2))
    svc = ScheduleService(session)
    with pytest.raises(PermissionDenied):
        await svc.cancel(lead, [a_and_b.id], NOW)
    await svc.cancel(lead, [only_a.id], NOW)


async def test_upcoming_lists_only_future_scheduled_within_horizon(session):
    admin, level = await setup_admin_and_level(session)
    soon = await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=1))
    await add_training(session, trainer=admin, levels=[level], starts_at=NOW - timedelta(days=1))
    await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=60))
    cancelled = await add_training(
        session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=2), status=TrainingStatus.CANCELLED
    )
    got = await ScheduleService(session).upcoming(NOW)
    assert [t.id for t in got] == [soon.id]
    assert cancelled.id not in [t.id for t in got]
