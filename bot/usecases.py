"""Operations that span several features. Telegram handlers call these, never the services directly
for state-changing actions that must also update posts."""
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction
from bot.core.messenger import Messenger
from bot.participation.models import ParticipationStatus
from bot.participation.service import JoinOutcome, ParticipationService
from bot.publishing.service import PublishService
from bot.schedule.models import Training
from bot.schedule.service import ReplacedTraining, ScheduleService, SeriesInput, TrainingEdit
from bot.users.models import User

log = logging.getLogger(__name__)


async def join_training(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int, now: datetime
) -> JoinOutcome:
    outcome = await ParticipationService(session).join(training_id, user_id, now)
    log.info("join training=%s user=%s -> %s", training_id, user_id, outcome)
    if outcome == JoinOutcome.JOINED:
        await PublishService(session, messenger).refresh_posts(training_id)
    return outcome


async def decline_training(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int, now: datetime
) -> str | None:
    previous = await ParticipationService(session).decline(training_id, user_id, now)
    log.info("decline training=%s user=%s (was %s)", training_id, user_id, previous)
    if previous == ParticipationStatus.GOING:
        await PublishService(session, messenger).refresh_posts(training_id)
    return previous


async def cancel_trainings(
    session: AsyncSession, messenger: Messenger, actor: User, training_ids: list[int], now: datetime
) -> list[Training]:
    cancelled = await ScheduleService(session).cancel(actor, training_ids, now)
    log.info("cancelled trainings %s by user=%s", [t.id for t in cancelled], actor.telegram_id)
    publisher = PublishService(session, messenger)
    for training in cancelled:
        await publisher.announce_cancelled(training.id)
    return cancelled


async def update_series(
    session: AsyncSession, messenger: Messenger, actor: User, series_id: int, data: SeriesInput, now: datetime
) -> list[ReplacedTraining]:
    replaced = await ScheduleService(session).update_series(actor, series_id, data, now)
    publisher = PublishService(session, messenger)
    for item in replaced:
        if item.new_id is not None:
            await publisher.publish(item.new_id, now)  # сразу, чтобы в уведомлении была ссылка на пост
        await publisher.announce_replaced(item.old_id, item.new_id)
    return replaced


async def update_training(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, edit: TrainingEdit, now: datetime
) -> Training:
    if edit.capacity is not None:
        going = await ParticipationService(session).going_count(training_id)
        if edit.capacity < going:
            raise InvalidAction(f"Уже записано {going} — лимит не может быть меньше")
    training = await ScheduleService(session).update_training(actor, training_id, edit, now)
    await PublishService(session, messenger).refresh_posts(training_id)
    return training


async def manual_add_participant(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, user_id: int, now: datetime,
    allow_over_limit: bool = False,
) -> JoinOutcome:
    outcome = await ParticipationService(session).manual_add(
        actor, training_id, user_id, now, allow_over_limit=allow_over_limit
    )
    log.info("manual add training=%s user=%s by=%s -> %s", training_id, user_id, actor.telegram_id, outcome)
    if outcome == JoinOutcome.JOINED:
        publisher = PublishService(session, messenger)
        await publisher.refresh_posts(training_id)
        await publisher.notify_manual_change(training_id, user_id, added=True)
    return outcome


async def manual_remove_participant(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, user_id: int, now: datetime
) -> bool:
    removed = await ParticipationService(session).manual_remove(actor, training_id, user_id, now)
    log.info("manual remove training=%s user=%s by=%s -> %s", training_id, user_id, actor.telegram_id, removed)
    if removed:
        publisher = PublishService(session, messenger)
        await publisher.refresh_posts(training_id)
        await publisher.notify_manual_change(training_id, user_id, added=False)
    return removed
