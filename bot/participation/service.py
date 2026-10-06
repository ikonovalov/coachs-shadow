from datetime import datetime
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction, NotFound
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Training, TrainingStatus
from bot.users.models import User


class JoinOutcome(str, Enum):
    JOINED = "joined"
    ALREADY = "already"
    FULL = "full"


class ParticipationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def join(self, training_id: int, user_id: int, now: datetime) -> JoinOutcome:
        training = await self._open_training(training_id, now)
        row = await self.session.get(Participation, (training_id, user_id))
        if row is not None and row.status == ParticipationStatus.GOING:
            return JoinOutcome.ALREADY
        if training.capacity is not None and await self.going_count(training_id) >= training.capacity:
            return JoinOutcome.FULL
        if row is None:
            self.session.add(
                Participation(training_id=training_id, user_id=user_id, status=ParticipationStatus.GOING, updated_at=now)
            )
        else:
            row.status = ParticipationStatus.GOING
            row.updated_at = now
        await self.session.commit()
        return JoinOutcome.JOINED

    async def decline(self, training_id: int, user_id: int, now: datetime) -> str | None:
        """Returns the previous status ("going", "declined") or None if there was no record."""
        await self._open_training(training_id, now)
        row = await self.session.get(Participation, (training_id, user_id))
        previous = row.status if row is not None else None
        if row is None:
            self.session.add(
                Participation(training_id=training_id, user_id=user_id, status=ParticipationStatus.DECLINED, updated_at=now)
            )
        elif row.status != ParticipationStatus.DECLINED:
            row.status = ParticipationStatus.DECLINED
            row.updated_at = now
        await self.session.commit()
        return previous

    async def going_count(self, training_id: int) -> int:
        return await self.session.scalar(
            select(func.count())
            .select_from(Participation)
            .where(Participation.training_id == training_id, Participation.status == ParticipationStatus.GOING)
        )

    async def list_participants(self, training_id: int) -> tuple[list[User], list[User]]:
        rows = await self.session.execute(
            select(Participation.status, User)
            .join(User, User.telegram_id == Participation.user_id)
            .where(Participation.training_id == training_id)
            .order_by(Participation.updated_at, Participation.user_id)
        )
        going: list[User] = []
        declined: list[User] = []
        for status, user in rows:
            (going if status == ParticipationStatus.GOING else declined).append(user)
        return going, declined

    async def _open_training(self, training_id: int, now: datetime) -> Training:
        # FOR UPDATE serialises concurrent sign-ups on PostgreSQL (ignored by SQLite).
        training = (
            await self.session.execute(select(Training).where(Training.id == training_id).with_for_update())
        ).scalar_one_or_none()
        if training is None:
            raise NotFound("Тренировка не найдена")
        if training.status == TrainingStatus.CANCELLED:
            raise InvalidAction("Тренировка отменена")
        if training.published_at is None:
            raise InvalidAction("Запись ещё не открыта")
        if training.status != TrainingStatus.SCHEDULED or training.starts_at <= now:
            raise InvalidAction("Тренировка уже началась")
        return training
