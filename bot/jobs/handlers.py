from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import Messenger
from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind
from bot.publishing.service import PublishService
from bot.schedule.service import ScheduleService
from bot.usecases import close_training, send_reminders


async def handle_job(session: AsyncSession, messenger: Messenger, job: ScheduledJob, now: datetime) -> None:
    """Handlers are idempotent: they re-check state, so a retry or a late run is safe."""
    if job.kind == JobKind.GENERATE:
        await ScheduleService(session).generate(now)
    elif job.kind == JobKind.PUBLISH:
        await PublishService(session, messenger).publish(job.training_id, now)
    elif job.kind == JobKind.REMIND_2H:
        await send_reminders(session, messenger, job.training_id, now)
    elif job.kind == JobKind.CLOSE:
        await close_training(session, messenger, job.training_id, now)
    else:
        raise ValueError(f"unknown job kind {job.kind!r}")
