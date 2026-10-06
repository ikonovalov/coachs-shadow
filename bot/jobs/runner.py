import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.core.db import utcnow
from bot.core.messenger import Messenger
from bot.jobs.handlers import handle_job
from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind, due_job_ids, ensure_generate_job

log = logging.getLogger(__name__)


async def run_due_jobs(
    session_factory: async_sessionmaker[AsyncSession], messenger: Messenger, now: datetime
) -> int:
    async with session_factory() as session:
        job_ids = await due_job_ids(session, now)
    completed = 0
    for job_id in job_ids:
        async with session_factory() as session:
            job = await session.get(ScheduledJob, job_id)
            if job is None or job.done_at is not None:
                continue
            try:
                await handle_job(session, messenger, job, now)
                job.done_at = now
                await session.flush()
                if job.kind == JobKind.GENERATE:
                    await ensure_generate_job(session, now + timedelta(hours=24))
                await session.commit()
                completed += 1
            except Exception:
                log.exception("job %s (%s) failed; will retry", job_id, job.kind)
                await session.rollback()
    return completed


async def scheduler_loop(
    session_factory: async_sessionmaker[AsyncSession],
    messenger: Messenger,
    interval: float = 30.0,
    now_fn: Callable[[], datetime] = utcnow,
) -> None:
    while True:
        try:
            await run_due_jobs(session_factory, messenger, now_fn())
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)
