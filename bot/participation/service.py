from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Integer, String, func, insert, literal, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import UTCDateTime
from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.service import ScheduleService
from bot.users.models import User


class JoinOutcome(str, Enum):
    JOINED = "joined"
    ALREADY = "already"
    FULL = "full"
    OVER_LIMIT = "over_limit"


class ParticipationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def join(self, training_id: int, user_id: int, now: datetime) -> JoinOutcome:
        training = await self._open_training(training_id, now)
        return await self._take_seat(training, user_id, now, enforce_capacity=True)

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

    async def manual_add(
        self, actor: User, training_id: int, user_id: int, now: datetime, *, allow_over_limit: bool = False
    ) -> JoinOutcome:
        training = await self._open_training(training_id, now)
        if not await ScheduleService(self.session).can_manage(actor, training):
            raise PermissionDenied("Нет прав на эту тренировку")
        if await self.session.get(User, user_id) is None:
            raise NotFound("Пользователь не найден")
        outcome = await self._take_seat(
            training, user_id, now, enforce_capacity=not allow_over_limit, added_by=actor.telegram_id
        )
        return JoinOutcome.OVER_LIMIT if outcome == JoinOutcome.FULL else outcome

    async def manual_remove(self, actor: User, training_id: int, user_id: int, now: datetime) -> bool:
        training = await self._open_training(training_id, now)
        if not await ScheduleService(self.session).can_manage(actor, training):
            raise PermissionDenied("Нет прав на эту тренировку")
        removed = await self.session.execute(  # conditional: of two concurrent taps only one removes
            update(Participation)
            .where(
                Participation.training_id == training_id,
                Participation.user_id == user_id,
                Participation.status == ParticipationStatus.GOING,
            )
            .values(status=ParticipationStatus.DECLINED, updated_at=now)
        )
        await self.session.commit()
        return removed.rowcount == 1

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

    async def _take_seat(
        self, training: Training, user_id: int, now: datetime, *, enforce_capacity: bool, added_by: int | None = None
    ) -> JoinOutcome:
        """Marks the user as going if a seat is free. The capacity check and the write are ONE statement
        (INSERT ... SELECT ... WHERE count < capacity, or the matching UPDATE), so SQLite runs them under its
        write lock: a separate SELECT count would run lock-free and parallel taps could all see a free seat."""
        is_user = (Participation.training_id == training.id) & (Participation.user_id == user_id)
        going = (
            select(func.count())
            .select_from(Participation)
            .where(Participation.training_id == training.id, Participation.status == ParticipationStatus.GOING)
            .scalar_subquery()
        )
        seat_free = going < training.capacity if enforce_capacity and training.capacity is not None else true()
        for _attempt in range(2):
            current = await self.session.scalar(select(Participation.status).where(is_user))
            if current == ParticipationStatus.GOING:
                return JoinOutcome.ALREADY
            if current is None:
                stmt = insert(Participation).from_select(
                    ["training_id", "user_id", "status", "updated_at", "added_by"],
                    select(
                        literal(training.id, Integer),
                        literal(user_id, BigInteger),
                        literal(ParticipationStatus.GOING, String),
                        literal(now, UTCDateTime),
                        literal(added_by, BigInteger),
                    ).where(seat_free),
                )
            else:
                values = {"status": ParticipationStatus.GOING, "updated_at": now}
                if added_by is not None:
                    values["added_by"] = added_by
                stmt = (
                    update(Participation)
                    .where(is_user, Participation.status != ParticipationStatus.GOING, seat_free)
                    .values(**values)
                    .execution_options(synchronize_session="fetch")
                )
            try:
                written = (await self.session.execute(stmt)).rowcount
                await self.session.commit()
            except IntegrityError:  # параллельный запрос того же пользователя успел вставить строку
                await self.session.rollback()
                continue
            if written:
                return JoinOutcome.JOINED
            if await self.session.scalar(select(Participation.status).where(is_user)) == ParticipationStatus.GOING:
                return JoinOutcome.ALREADY
            return JoinOutcome.FULL
        raise RuntimeError("participation row keeps conflicting")  # недостижимо: после конфликта строка уже есть

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
