from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.permissions import Role, can_create_for_levels, can_manage_training, require_admin
from bot.core.settings import get_setting
from bot.jobs.queue import JobKind, cancel_for_training, delete_for_training, enqueue
from bot.schedule.models import (
    CancelReason,
    Chat,
    Level,
    Training,
    TrainingKind,
    TrainingSeries,
    TrainingStatus,
    series_level,
    training_level,
)
from bot.schedule.queries import can_manage, going_count, lead_chat_ids, target_chat_ids, trainer_level_ids
from bot.schedule.recurrence import occurrences
from bot.users.models import User

GENERATION_WEEKS = 4
SCHEDULE_VIEW_DAYS = 7  # окно кнопки «Расписание»; не зависит от горизонта генерации
MAX_LEVEL_NAME = 50


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


@dataclass
class TrainingEdit:
    place: str | None = None
    description: str | None = None
    capacity: int | None = None
    clear_capacity: bool = False


@dataclass(frozen=True)
class ReplacedTraining:
    old_id: int
    new_id: int | None  # None: пары нет, участникам уйдёт обычное уведомление об отмене


def series_to_input(series: TrainingSeries) -> SeriesInput:
    return SeriesInput(
        kind=series.kind,
        place=series.place,
        level_ids=[lvl.id for lvl in series.levels],
        start_date=series.start_date,
        start_time=series.start_time,
        weekdays={int(x) for x in series.weekdays.split(",") if x},
        end_date=series.end_date,
        capacity=series.capacity,
        description=series.description,
        duration_min=series.duration_min,
        trainer_id=series.trainer_id,
    )


def is_active_series(series: TrainingSeries, today: date) -> bool:
    """Активная серия — повторяющаяся и не закончившаяся к местной сегодняшней дате."""
    return bool(series.weekdays) and (series.end_date is None or series.end_date >= today)


class ScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- levels and chats (admin) ----

    async def add_level(self, actor: User, name: str, position: int = 0) -> Level:
        require_admin(actor)
        name = name.strip()
        if not name:
            raise InvalidAction("Название уровня не может быть пустым")
        if len(name) > MAX_LEVEL_NAME:
            raise InvalidAction(f"Название уровня длиннее {MAX_LEVEL_NAME} символов")
        if await self.session.scalar(select(Level.id).where(Level.name == name)) is not None:
            raise InvalidAction(f"Уровень «{name}» уже есть")
        level = Level(name=name, position=position)
        self.session.add(level)
        await self.session.commit()
        return level

    async def list_levels(self) -> list[Level]:
        return list(await self.session.scalars(select(Level).order_by(Level.position, Level.name)))

    async def get_level(self, level_id: int) -> Level:
        level = await self.session.get(Level, level_id)
        if level is None:
            raise NotFound("Уровень не найден")
        return level

    async def delete_level(self, actor: User, level_id: int) -> Level:
        """Удаляет только неиспользуемый уровень: без чатов, серий и тренировок (в т.ч. прошедших)."""
        require_admin(actor)
        level = await self.get_level(level_id)

        async def count(column) -> int:
            return await self.session.scalar(select(func.count()).where(column == level_id)) or 0

        chats, series, trainings = (
            await count(Chat.level_id), await count(series_level.c.level_id), await count(training_level.c.level_id)
        )
        if chats or series or trainings:
            raise InvalidAction(
                f"Уровень «{level.name}» используется (чатов: {chats}, серий: {series}, тренировок: {trainings}) — "
                "удалить нельзя"
            )
        await self.session.delete(level)
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
        levels = await self._load_levels(data.level_ids)

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
                Training.starts_at < now + timedelta(days=SCHEDULE_VIEW_DAYS),
            )
            .order_by(Training.starts_at)
            .limit(limit)
        )
        return list(rows)

    # ---- cancel ----

    async def can_manage(self, actor: User, training: Training) -> bool:
        return await can_manage(self.session, actor, training)

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
        await self._cancel_rows(trainings, now)
        await self.session.commit()
        return trainings

    async def _cancel_rows(self, trainings: list[Training], now: datetime) -> None:
        """Переводит тренировки в CANCELLED (причина USER) и гасит их задачи. Без commit: его делает вызывающий."""
        for training in trainings:
            # conditional UPDATE: of two concurrent cancels (double tap) only one flips the status
            flipped = await self.session.execute(
                update(Training)
                .where(Training.id == training.id, Training.status == TrainingStatus.SCHEDULED)
                .values(status=TrainingStatus.CANCELLED, cancel_reason=CancelReason.USER)
            )
            if flipped.rowcount != 1:
                await self.session.rollback()
                raise InvalidAction("Тренировка уже отменена или прошла")
            training.status, training.cancel_reason = TrainingStatus.CANCELLED, CancelReason.USER  # in-memory copy
            await cancel_for_training(self.session, training.id, now)

    async def allowed_levels(self, actor: User) -> list[Level]:
        query = select(Level).order_by(Level.position, Level.id)
        if actor.role != Role.ADMIN:
            ids = await trainer_level_ids(self.session, actor.telegram_id)
            query = query.where(Level.id.in_(ids))
        return list(await self.session.scalars(query))

    async def managed_trainings(self, actor: User, now: datetime, limit: int = 20) -> list[Training]:
        if actor.role not in (Role.ADMIN, Role.TRAINER):
            return []
        candidates = await self.session.scalars(
            select(Training)
            .where(Training.status == TrainingStatus.SCHEDULED, Training.starts_at > now)
            .order_by(Training.starts_at)
            .limit(300)
        )
        lead = set() if actor.role == Role.ADMIN else await lead_chat_ids(self.session, actor.telegram_id)
        result: list[Training] = []
        for training in candidates:
            if actor.role == Role.ADMIN or training.trainer_id == actor.telegram_id:
                allowed = True
            else:
                allowed = can_manage_training(
                    actor, training.trainer_id, set(await target_chat_ids(self.session, training)), lead
                )
            if allowed:
                result.append(training)
                if len(result) >= limit:
                    break
        return result

    async def update_training(self, actor: User, training_id: int, edit: TrainingEdit, now: datetime) -> Training:
        training = await self.session.get(Training, training_id)
        if training is None:
            raise NotFound("Тренировка не найдена")
        if training.status != TrainingStatus.SCHEDULED or training.starts_at <= now:
            raise InvalidAction("Тренировку уже нельзя изменить")
        if not await self.can_manage(actor, training):
            raise PermissionDenied("Нет прав на эту тренировку")
        if edit.place is not None:
            if not edit.place.strip():
                raise InvalidAction("Укажите место тренировки")
        if edit.capacity is not None and edit.capacity < 1:
            raise InvalidAction("Лимит мест должен быть больше нуля")
        if edit.capacity is not None and not edit.clear_capacity:
            going = await going_count(self.session, training_id)
            if edit.capacity < going:
                raise InvalidAction(f"Уже записано {going} — лимит не может быть меньше")
        if edit.place is not None:
            training.place = edit.place.strip()
        if edit.description is not None:
            training.description = edit.description
        if edit.clear_capacity:
            training.capacity = None
        elif edit.capacity is not None:
            training.capacity = edit.capacity
        await self.session.commit()
        return training

    async def update_series(
        self, actor: User, series_id: int, data: SeriesInput, now: datetime
    ) -> list[ReplacedTraining]:
        """Применяет шаблон к будущим тренировкам серии. Все проверки идут до первой мутации."""
        series = await self.session.get(TrainingSeries, series_id)
        if series is None:
            raise NotFound("Серия не найдена")
        if actor.role != Role.ADMIN and series.trainer_id != actor.telegram_id:
            raise PermissionDenied("Серию может менять только её тренер или администратор")
        self._validate(data)
        await self._check_can_create(actor, set(data.level_ids))
        levels = await self._load_levels(data.level_ids)
        tz = await self._tz()
        if not data.weekdays and datetime.combine(data.start_date, data.start_time, tzinfo=tz) <= now:
            raise InvalidAction("Тренировка должна быть в будущем")
        if data.trainer_id not in (None, series.trainer_id):
            require_admin(actor)
            if await self.session.get(User, data.trainer_id) is None:
                raise NotFound("Тренер не найден")
            series.trainer_id = data.trainer_id

        series.kind = data.kind
        series.place = data.place.strip()
        series.description = data.description
        series.capacity = data.capacity
        series.weekdays = ",".join(str(d) for d in sorted(data.weekdays))
        series.start_date = data.start_date
        series.start_time = data.start_time
        series.end_date = data.end_date
        series.duration_min = data.duration_min or series.duration_min
        series.levels = levels

        future = list(
            await self.session.scalars(
                select(Training).where(
                    Training.series_id == series.id,
                    Training.status == TrainingStatus.SCHEDULED,
                    Training.starts_at > now,
                )
            )
        )
        replaced: list[Training] = []
        for training in future:
            if training.published_at is None:
                await delete_for_training(self.session, training.id)
                await self.session.delete(training)
            else:
                training.status = TrainingStatus.CANCELLED
                training.cancel_reason = CancelReason.SERIES_EDIT
                await cancel_for_training(self.session, training.id, now)
                replaced.append(training)
        await self.session.flush()  # индекс: старое сначала выходит из уникальности, потом вставляется новое

        created = await self._generate_for_series(series, now, tz)
        by_date = {t.starts_at.astimezone(tz).date(): t for t in created}
        result: list[ReplacedTraining] = []
        for old in replaced:
            new = by_date.pop(old.starts_at.astimezone(tz).date(), None)
            result.append(ReplacedTraining(old.id, new.id if new is not None else None))
        await self.session.commit()
        return result

    async def active_series(self, actor: User, now: datetime) -> list[TrainingSeries]:
        """Активные повторяющиеся серии: свои у тренера, все у админа."""
        if actor.role not in (Role.ADMIN, Role.TRAINER):
            return []
        today = now.astimezone(await self._tz()).date()
        query = (
            select(TrainingSeries)
            .where(
                TrainingSeries.weekdays != "",
                or_(TrainingSeries.end_date.is_(None), TrainingSeries.end_date >= today),
            )
            .order_by(TrainingSeries.id)
        )
        if actor.role != Role.ADMIN:
            query = query.where(TrainingSeries.trainer_id == actor.telegram_id)
        return list(await self.session.scalars(query))

    async def future_training_count(self, series_id: int, now: datetime) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(Training)
            .where(
                Training.series_id == series_id,
                Training.status == TrainingStatus.SCHEDULED,
                Training.starts_at > now,
            )
        )
        return count or 0

    async def end_series(self, actor: User, series_id: int, now: datetime) -> list[Training]:
        """Завершает серию: отменяет её будущие тренировки и закрывает генерацию. Проверки идут до мутаций."""
        series = await self.session.get(TrainingSeries, series_id)
        if series is None:
            raise NotFound("Серия не найдена")
        if actor.role != Role.ADMIN and series.trainer_id != actor.telegram_id:
            raise PermissionDenied("Серию может завершить только её тренер или администратор")
        if not series.weekdays:
            raise InvalidAction("Это разовая тренировка: отмените её в разделе «Мои тренировки»")
        today = now.astimezone(await self._tz()).date()
        if not is_active_series(series, today):
            raise InvalidAction("Серия уже завершена")
        # вчера: generate() берёт серии с end_date >= today, поэтому серия сразу выпадает и из генерации, и из списка
        series.end_date = today - timedelta(days=1)
        future = list(
            await self.session.scalars(
                select(Training)
                .where(
                    Training.series_id == series.id,
                    Training.status == TrainingStatus.SCHEDULED,
                    Training.starts_at > now,
                )
                .order_by(Training.starts_at)
            )
        )
        await self._cancel_rows(future, now)
        await self.session.commit()
        return future

    # ---- internals ----

    async def _load_levels(self, level_ids: list[int]) -> list[Level]:
        levels = list(await self.session.scalars(select(Level).where(Level.id.in_(level_ids))))
        if len(levels) != len(set(level_ids)):
            raise NotFound("Уровень не найден")
        return levels

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
        # Одна тренировка серии на местную дату: существующая (в т.ч. отменённая пользователем) занимает
        # свой день, даже если у серии потом сменилось время. Отменённые правкой серии день не занимают —
        # их место как раз и должна занять замена.
        today_start = datetime.combine(now.astimezone(tz).date(), time(0), tzinfo=tz)
        taken_dates = {
            starts_at.astimezone(tz).date()
            for starts_at in await self.session.scalars(
                select(Training.starts_at).where(
                    Training.series_id == series.id,
                    Training.starts_at >= today_start,
                    Training.starts_at < horizon + timedelta(days=1),
                    or_(Training.cancel_reason.is_(None), Training.cancel_reason != CancelReason.SERIES_EDIT),
                )
            )
        }
        created: list[Training] = []
        for starts_at in starts:
            if starts_at.astimezone(tz).date() in taken_dates:
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
