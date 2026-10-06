from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.permissions import Role, can_create_for_levels, can_manage_training, require_admin
from bot.core.settings import get_setting
from bot.jobs.queue import JobKind, cancel_for_training, enqueue
from bot.schedule.models import Chat, Level, Training, TrainingKind, TrainingSeries, TrainingStatus
from bot.schedule.queries import lead_chat_ids, target_chat_ids, trainer_level_ids
from bot.schedule.recurrence import occurrences
from bot.users.models import User

GENERATION_WEEKS = 4


@dataclass
class SeriesInput:
    kind: str
    place: str
    level_ids: list[int]
    start_date: date
    start_time: time
    weekdays: set[int] = field(default_factory=set)  # ISO 1..7; empty = one-off
    end_date: date | None = None
    capacity: int | None = None
    description: str = ""
    duration_min: int | None = None
    trainer_id: int | None = None  # only an admin may set someone else


class ScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- levels and chats (admin) ----

    async def add_level(self, actor: User, name: str, position: int = 0) -> Level:
        require_admin(actor)
        name = name.strip()
        if not name:
            raise InvalidAction("Название уровня не может быть пустым")
        level = Level(name=name, position=position)
        self.session.add(level)
        await self.session.commit()
        return level

    async def bind_chat(self, actor: User, chat_id: int, title: str, level_name: str) -> Chat:
        require_admin(actor)
        level = await self.session.scalar(select(Level).where(Level.name == level_name.strip()))
        if level is None:
            raise NotFound(f"Уровень «{level_name.strip()}» не найден")
        chat = await self.session.get(Chat, chat_id)
        if chat is None:
            chat = Chat(chat_id=chat_id, title=title, level_id=level.id)
            self.session.add(chat)
        else:
            chat.title = title
            chat.level_id = level.id
        await self.session.commit()
        return chat

    # ---- series ----

    async def create_series(self, actor: User, data: SeriesInput, now: datetime) -> TrainingSeries:
        self._validate(data)
        await self._check_can_create(actor, set(data.level_ids))
        trainer_id = data.trainer_id or actor.telegram_id
        if trainer_id != actor.telegram_id:
            require_admin(actor)
        if await self.session.get(User, trainer_id) is None:
            raise NotFound("Тренер не найден")
        levels = list(await self.session.scalars(select(Level).where(Level.id.in_(data.level_ids))))
        if len(levels) != len(set(data.level_ids)):
            raise NotFound("Уровень не найден")

        tz = await self._tz()
        if not data.weekdays:
            first = datetime.combine(data.start_date, data.start_time, tzinfo=tz)
            if first <= now:
                raise InvalidAction("Тренировка должна быть в будущем")
        duration = data.duration_min or int(await get_setting(self.session, "duration_min"))

        series = TrainingSeries(
            trainer_id=trainer_id,
            place=data.place.strip(),
            kind=data.kind,
            description=data.description,
            capacity=data.capacity,
            weekdays=",".join(str(d) for d in sorted(data.weekdays)),
            start_date=data.start_date,
            start_time=data.start_time,
            end_date=data.end_date,
            duration_min=duration,
            levels=levels,
        )
        self.session.add(series)
        await self.session.flush()
        await self._generate_for_series(series, now, tz)
        await self.session.commit()
        return series

    async def generate(self, now: datetime) -> list[Training]:
        tz = await self._tz()
        today = now.astimezone(tz).date()
        all_series = await self.session.scalars(
            select(TrainingSeries).where(or_(TrainingSeries.end_date.is_(None), TrainingSeries.end_date >= today))
        )
        created: list[Training] = []
        for series in list(all_series):
            created += await self._generate_for_series(series, now, tz)
        await self.session.commit()
        return created

    async def upcoming(self, now: datetime, limit: int = 10) -> list[Training]:
        rows = await self.session.scalars(
            select(Training)
            .where(
                Training.status == TrainingStatus.SCHEDULED,
                Training.starts_at > now,
                Training.starts_at < now + timedelta(weeks=GENERATION_WEEKS),
            )
            .order_by(Training.starts_at)
            .limit(limit)
        )
        return list(rows)

    # ---- cancel ----

    async def can_manage(self, actor: User, training: Training) -> bool:
        if actor.role == Role.ADMIN:
            return True
        return can_manage_training(
            actor,
            training.trainer_id,
            set(await target_chat_ids(self.session, training)),
            await lead_chat_ids(self.session, actor.telegram_id),
        )

    async def cancel(self, actor: User, training_ids: list[int], now: datetime) -> list[Training]:
        """Cancel several trainings atomically: validate all first, then mutate."""
        if not training_ids:
            raise InvalidAction("Выберите тренировки для отмены")
        trainings: list[Training] = []
        for training_id in dict.fromkeys(training_ids):
            training = await self.session.get(Training, training_id)
            if training is None:
                raise NotFound("Тренировка не найдена")
            if training.status != TrainingStatus.SCHEDULED:
                raise InvalidAction("Тренировка уже отменена или прошла")
            if training.starts_at <= now:
                raise InvalidAction("Нельзя отменить начатую или прошедшую тренировку")
            if not await self.can_manage(actor, training):
                raise PermissionDenied("Нет прав на эту тренировку")
            trainings.append(training)
        for training in trainings:
            training.status = TrainingStatus.CANCELLED
            await cancel_for_training(self.session, training.id, now)
        await self.session.commit()
        return trainings

    # ---- internals ----

    async def _tz(self) -> ZoneInfo:
        return ZoneInfo(await get_setting(self.session, "club_timezone"))

    @staticmethod
    def _validate(data: SeriesInput) -> None:
        if not data.place.strip():
            raise InvalidAction("Укажите место тренировки")
        if data.kind not in TrainingKind.ALL:
            raise InvalidAction("Неизвестный тип тренировки")
        if not data.level_ids:
            raise InvalidAction("Выберите хотя бы один уровень")
        if not data.weekdays <= {1, 2, 3, 4, 5, 6, 7}:
            raise InvalidAction("Дни недели должны быть от 1 до 7")
        if data.capacity is not None and data.capacity < 1:
            raise InvalidAction("Лимит мест должен быть больше нуля")
        if data.duration_min is not None and data.duration_min < 1:
            raise InvalidAction("Длительность должна быть больше нуля")
        if data.end_date is not None and data.end_date < data.start_date:
            raise InvalidAction("Дата окончания раньше даты начала")

    async def _check_can_create(self, actor: User, level_ids: set[int]) -> None:
        if actor.role == Role.ADMIN:
            return
        if actor.role != Role.TRAINER:
            raise PermissionDenied("Создавать тренировки могут только тренеры")
        own = await trainer_level_ids(self.session, actor.telegram_id)
        if not own:
            raise PermissionDenied("Вы тренер, но пока не добавлены ни в один чат. Обратитесь к администратору")
        if not can_create_for_levels(actor, level_ids, own):
            raise PermissionDenied("Можно создавать тренировки только для уровней своих чатов")

    async def _generate_for_series(self, series: TrainingSeries, now: datetime, tz: ZoneInfo) -> list[Training]:
        horizon = now + timedelta(weeks=GENERATION_WEEKS)
        starts = occurrences(
            weekdays={int(x) for x in series.weekdays.split(",") if x},
            start_date=series.start_date,
            start_time=series.start_time,
            end_date=series.end_date,
            tz=tz,
            window_start=now,
            window_end=horizon,
        )
        existing = set(
            await self.session.scalars(
                select(Training.starts_at).where(
                    Training.series_id == series.id, Training.starts_at >= now, Training.starts_at < horizon
                )
            )
        )
        created: list[Training] = []
        for starts_at in starts:
            if starts_at in existing:
                continue
            training = Training(
                series_id=series.id,
                starts_at=starts_at,
                duration_min=series.duration_min,
                trainer_id=series.trainer_id,
                place=series.place,
                kind=series.kind,
                description=series.description,
                capacity=series.capacity,
                status=TrainingStatus.SCHEDULED,
                levels=list(series.levels),
            )
            self.session.add(training)
            created.append(training)
        await self.session.flush()
        publish_h = int(await get_setting(self.session, "publish_window_h"))
        remind_h = int(await get_setting(self.session, "reminder_h"))
        for training in created:
            await enqueue(
                self.session, JobKind.PUBLISH, max(training.starts_at - timedelta(hours=publish_h), now), training.id
            )
            remind_at = training.starts_at - timedelta(hours=remind_h)
            if remind_at > now:
                await enqueue(self.session, JobKind.REMIND_2H, remind_at, training.id)
            await enqueue(self.session, JobKind.CLOSE, training.starts_at, training.id)
        return created
