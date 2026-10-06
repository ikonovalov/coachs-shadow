from datetime import timedelta

from sqlalchemy import select

from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind, cancel_for_training, due_job_ids, enqueue, ensure_generate_job
from tests.factories import NOW, add_level, add_training, add_user


async def test_due_jobs_are_ordered_and_exclude_done_and_future(session):
    a = await enqueue(session, JobKind.GENERATE, NOW - timedelta(hours=2))
    b = await enqueue(session, JobKind.GENERATE, NOW - timedelta(hours=3))
    await enqueue(session, JobKind.GENERATE, NOW + timedelta(hours=1))
    done = await enqueue(session, JobKind.GENERATE, NOW - timedelta(hours=5))
    done.done_at = NOW
    await session.commit()
    assert await due_job_ids(session, NOW) == [b.id, a.id]


async def test_ensure_generate_job_is_idempotent(session):
    await ensure_generate_job(session, NOW)
    await ensure_generate_job(session, NOW)
    await session.commit()
    rows = list(await session.scalars(select(ScheduledJob)))
    assert len(rows) == 1


async def test_cancel_for_training_marks_only_its_pending_jobs(session):
    trainer = await add_user(session, 1, "trainer")
    level = await add_level(session)
    t1 = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(days=1))
    t2 = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(days=2))
    j1 = await enqueue(session, JobKind.PUBLISH, NOW, t1.id)
    j2 = await enqueue(session, JobKind.PUBLISH, NOW, t2.id)
    await cancel_for_training(session, t1.id, NOW)
    await session.commit()
    await session.refresh(j1)
    await session.refresh(j2)
    assert j1.done_at == NOW
    assert j2.done_at is None
