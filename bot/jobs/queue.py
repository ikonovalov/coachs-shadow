from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.jobs.models import ScheduledJob


class JobKind:
    GENERATE = "generate"
    PUBLISH = "publish"
    REMIND_2H = "remind_2h"
    CLOSE = "close"


async def enqueue(
    session: AsyncSession, kind: str, run_at: datetime, training_id: int | None = None
) -> ScheduledJob:
    job = ScheduledJob(kind=kind, run_at=run_at, training_id=training_id)
    session.add(job)
    await session.flush()
    return job


async def ensure_generate_job(session: AsyncSession, run_at: datetime) -> None:
    pending = await session.scalar(
        select(ScheduledJob.id)
        .where(ScheduledJob.kind == JobKind.GENERATE, ScheduledJob.done_at.is_(None))
        .limit(1)
    )
    if pending is None:
        await enqueue(session, JobKind.GENERATE, run_at)


async def cancel_for_training(session: AsyncSession, training_id: int, now: datetime) -> None:
    await session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.training_id == training_id, ScheduledJob.done_at.is_(None))
        .values(done_at=now)
    )


async def due_job_ids(session: AsyncSession, now: datetime, limit: int = 100) -> list[int]:
    rows = await session.scalars(
        select(ScheduledJob.id)
        .where(ScheduledJob.done_at.is_(None), ScheduledJob.run_at <= now)
        .order_by(ScheduledJob.run_at, ScheduledJob.id)
        .limit(limit)
    )
    return list(rows)
