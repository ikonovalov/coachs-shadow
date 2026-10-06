from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from bot.bootstrap import bootstrap
from bot.core.settings import get_setting
from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind, enqueue
from bot.jobs.runner import run_due_jobs
from bot.participation.service import ParticipationService
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput
from bot.users.models import User
from tests.factories import add_chat, add_level, add_user

T0 = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)  # created here
STARTS = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)  # Tue 19:00 MSK


async def setup(session_factory):
    async with session_factory() as s:
        admin = await add_user(s, 99, "admin")
        level = await add_level(s)
        await add_chat(s, level, -1001)
        await ScheduleService(s).create_series(
            admin,
            SeriesInput(kind="intervals", place="Парк", level_ids=[level.id],
                        start_date=date(2026, 10, 20), start_time=time(19, 0)),
            T0,
        )


async def get_training(session_factory):
    async with session_factory() as s:
        return await s.scalar(select(Training))


async def test_publish_job_runs_when_due_and_only_once(session_factory, messenger):
    await setup(session_factory)
    await run_due_jobs(session_factory, messenger, T0)  # publish is due at STARTS-24h = T0+6h: not yet
    assert messenger.sent == []
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=24))
    assert len(messenger.sent) == 1
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=23))
    assert len(messenger.sent) == 1  # idempotent


async def test_full_lifecycle_reminder_and_close(session_factory, messenger):
    await setup(session_factory)
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=24))
    t = await get_training(session_factory)
    async with session_factory() as s:
        await add_user(s, 1)
        await ParticipationService(s).join(t.id, 1, STARTS - timedelta(hours=10))
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=2))
    assert [uid for uid, _ in messenger.notifications] == [1]
    await run_due_jobs(session_factory, messenger, STARTS)
    assert (await get_training(session_factory)).status == TrainingStatus.DONE


async def test_catch_up_after_downtime_runs_everything_in_order(session_factory, messenger):
    await setup(session_factory)
    # the bot was down until after the start: publish is skipped (started), close is applied
    await run_due_jobs(session_factory, messenger, STARTS + timedelta(minutes=10))
    assert messenger.sent == []
    assert (await get_training(session_factory)).status == TrainingStatus.DONE
    async with session_factory() as s:
        pending = list(await s.scalars(select(ScheduledJob).where(ScheduledJob.done_at.is_(None))))
    assert pending == []


async def test_failing_job_is_retried_next_tick(session_factory, messenger):
    await setup(session_factory)
    original = messenger.send
    calls = {"n": 0}

    async def flaky(chat_id, text, keyboard=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network down")
        return await original(chat_id, text, keyboard)

    messenger.send = flaky
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=24))
    assert messenger.sent == []
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=23, minutes=59))
    assert len(messenger.sent) == 1


async def test_generate_job_reschedules_itself(session_factory, messenger):
    async with session_factory() as s:
        await enqueue(s, JobKind.GENERATE, T0)
        await s.commit()
    assert await run_due_jobs(session_factory, messenger, T0) == 1
    async with session_factory() as s:
        jobs = list(await s.scalars(select(ScheduledJob).order_by(ScheduledJob.id)))
    assert [j.done_at is not None for j in jobs] == [True, False]
    assert jobs[1].run_at == T0 + timedelta(hours=24)


async def test_bootstrap_is_idempotent(session_factory):
    cfg = SimpleNamespace(admin_ids=[7], club_timezone="Asia/Yekaterinburg")
    await bootstrap(session_factory, cfg, T0)
    await bootstrap(session_factory, cfg, T0)
    async with session_factory() as s:
        assert (await s.get(User, 7)).role == "admin"
        jobs = list(await s.scalars(select(ScheduledJob).where(ScheduledJob.kind == JobKind.GENERATE)))
        assert await get_setting(s, "club_timezone") == "Asia/Yekaterinburg"
    assert len(jobs) == 1
