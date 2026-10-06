"""Operations that span several features. Telegram handlers call these, never the services directly
for state-changing actions that must also update posts."""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import Messenger
from bot.participation.models import ParticipationStatus
from bot.participation.service import JoinOutcome, ParticipationService
from bot.publishing.service import PublishService
from bot.schedule.models import Training
from bot.schedule.service import ScheduleService
from bot.users.models import User


async def join_training(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int, now: datetime
) -> JoinOutcome:
    outcome = await ParticipationService(session).join(training_id, user_id, now)
    if outcome == JoinOutcome.JOINED:
        await PublishService(session, messenger).refresh_posts(training_id)
    return outcome


async def decline_training(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int, now: datetime
) -> str | None:
    previous = await ParticipationService(session).decline(training_id, user_id, now)
    if previous == ParticipationStatus.GOING:
        await PublishService(session, messenger).refresh_posts(training_id)
    return previous


async def cancel_trainings(
    session: AsyncSession, messenger: Messenger, actor: User, training_ids: list[int], now: datetime
) -> list[Training]:
    cancelled = await ScheduleService(session).cancel(actor, training_ids, now)
    publisher = PublishService(session, messenger)
    for training in cancelled:
        await publisher.announce_cancelled(training.id)
    return cancelled
