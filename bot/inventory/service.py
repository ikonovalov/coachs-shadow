import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, Integer, String, exists, func, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import UTCDateTime
from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.permissions import require_admin
from bot.core.settings import INVENTORY_DEADLINE_MAX_H, get_setting
from bot.inventory.matching import (
    KIND_LABELS, PEDAL_TYPES, Candidate, Profile, Suggestion, Wanted, missing_profile_fields, own_shoes_problem,
    set_problems, suggest,
)
from bot.inventory.models import CleatCompat, InventoryItem, InventoryOrder, ItemKind, OrderStatus
from bot.inventory.summary import OrderLine
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.queries import can_manage
from bot.users.models import User
from bot.users.profile import CLEAT_TYPES, HELMET_SIZES

log = logging.getLogger(__name__)

_PARAM_FIELDS = {"frame", "height_min", "height_max", "shoe_eu", "cleat_type", "pedal_type", "helmet_size", "price_per_use"}


class NotGoing(InvalidAction):
    """Участник не записан на тренировку: UI предлагает сначала записаться."""


@dataclass
class ConfirmResult:
    orders: list[InventoryOrder]
    conflicts: list[str]  # виды единиц, которые успели забрать или вывели из парка


@dataclass
class OrderView:
    order: InventoryOrder
    item: InventoryItem
    training: Training


def to_candidate(item: InventoryItem) -> Candidate:
    return Candidate(
        id=item.id, kind=item.kind, label=item.label, frame=item.frame, height_min=item.height_min,
        height_max=item.height_max, shoe_eu=item.shoe_eu, cleat_type=item.cleat_type,
        pedal_type=item.pedal_type, helmet_size=item.helmet_size,
    )


async def overlapping_training_ids(session: AsyncSession, training: Training) -> set[int]:
    """Тренировки, пересекающиеся по времени с данной (включая её саму).
    Пересечение: a.starts_at < b.ends_at AND b.starts_at < a.ends_at, ends_at = starts_at + duration_min;
    тренировки «встык» не пересекаются. Окно выборки сужено по самой длинной тренировке."""
    longest = await session.scalar(select(func.max(Training.duration_min))) or 0
    lo = training.starts_at - timedelta(minutes=longest)
    rows = await session.execute(
        select(Training.id, Training.starts_at, Training.duration_min)
        .where(Training.starts_at > lo, Training.starts_at < training.ends_at)
    )
    return {
        tid for tid, starts_at, duration in rows
        if starts_at < training.ends_at and training.starts_at < starts_at + timedelta(minutes=duration)
    }


async def busy_item_ids(session: AsyncSession, training: Training) -> set[int]:
    """Единицы с активным заказом на тренировке, пересекающейся по времени с данной."""
    ids = await overlapping_training_ids(session, training)
    rows = await session.scalars(
        select(InventoryOrder.item_id).where(
            InventoryOrder.status == OrderStatus.ACTIVE, InventoryOrder.training_id.in_(ids)
        )
    )
    return set(rows)


def _validate_item(kind: str, label: str, params: dict) -> None:
    if kind not in ItemKind.ALL:
        raise InvalidAction("Неизвестный вид инвентаря")
    if not label.strip():
        raise InvalidAction("Укажите метку единицы")
    unknown = set(params) - _PARAM_FIELDS
    if unknown:
        raise InvalidAction("Неизвестные параметры: " + ", ".join(sorted(unknown)))
    if kind == ItemKind.BIKE:
        if not params.get("frame") or not (params.get("height_min") and params.get("height_max")) \
                or params["height_min"] > params["height_max"]:
            raise InvalidAction("Для велосипеда нужны рама и диапазон роста")
    elif kind == ItemKind.PEDALS:
        if params.get("pedal_type") not in PEDAL_TYPES:
            raise InvalidAction("Неверный тип педалей")
    elif kind == ItemKind.SHOES:
        if not params.get("shoe_eu") or params.get("cleat_type") not in CLEAT_TYPES:
            raise InvalidAction("Для туфель нужны размер и тип шипов")
    elif params.get("helmet_size") not in HELMET_SIZES:
        raise InvalidAction("Размер шлема: S, M или L")


def _kind_ordered(training_id: int, user_id: int, kind: str):
    """Условие «у участника уже есть активный заказ этого вида на эту тренировку»."""
    return exists().where(
        InventoryOrder.training_id == training_id, InventoryOrder.user_id == user_id,
        InventoryOrder.status == OrderStatus.ACTIVE, InventoryOrder.item_id == InventoryItem.id,
        InventoryItem.kind == kind,
    )


def _already_ordered_error(kinds: set[str]) -> InvalidAction:
    names = ", ".join(KIND_LABELS[k].lower() for k in sorted(kinds))
    return InvalidAction(f"У вас уже есть заказ ({names}). Отмените его в /my, чтобы заказать заново")


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- парк ----

    async def add_item(self, actor: User, kind: str, label: str, **params) -> InventoryItem:
        require_admin(actor)
        _validate_item(kind, label, params)
        item = InventoryItem(kind=kind, label=label.strip(), **params)
        self.session.add(item)
        await self.session.commit()
        return item

    async def set_active(self, actor: User, item_id: int, active: bool) -> InventoryItem:
        require_admin(actor)
        item = await self.session.get(InventoryItem, item_id)
        if item is None:
            raise NotFound("Единица не найдена")
        item.active = active
        await self.session.commit()
        return item

    async def list_items(self, include_inactive: bool = True) -> list[InventoryItem]:
        query = select(InventoryItem).order_by(InventoryItem.kind, InventoryItem.id)
        if not include_inactive:
            query = query.where(InventoryItem.active.is_(True))
        return list(await self.session.scalars(query))

    # ---- проверки ----

    async def check_can_order(self, training_id: int, user_id: int, now: datetime) -> Training:
        training = await self.session.get(Training, training_id)
        if training is None:
            raise NotFound("Тренировка не найдена")
        if training.status != TrainingStatus.SCHEDULED or training.starts_at <= now:
            raise InvalidAction("На эту тренировку инвентарь заказать уже нельзя")
        deadline = await self._deadline_hours()
        if deadline is not None and now > training.starts_at - timedelta(hours=deadline):
            raise InvalidAction(f"Приём заказов закрыт за {deadline} ч до тренировки")
        row = await self.session.get(Participation, (training_id, user_id))
        if row is None or row.status != ParticipationStatus.GOING:
            raise NotGoing("Сначала запишитесь на тренировку")
        return training

    async def _deadline_hours(self) -> int | None:
        """Дедлайн заказа в часах. validate_setting ограничивает его 0..720, но значение в БД могли записать
        в обход: нечитаемое считается «без дедлайна», слишком большое ограничивается 720 ч (без OverflowError)."""
        raw = (await get_setting(self.session, "inventory_deadline_h")).strip()
        if not raw:
            return None
        if not (raw.isascii() and raw.isdigit()):
            log.warning("ignoring unreadable inventory_deadline_h=%r", raw)
            return None
        return min(int(raw), INVENTORY_DEADLINE_MAX_H)

    async def _reject_kinds_already_ordered(self, training_id: int, user_id: int, kinds: set[str]) -> None:
        ordered = await self.session.scalars(
            select(InventoryItem.kind)
            .join(InventoryOrder, InventoryOrder.item_id == InventoryItem.id)
            .where(
                InventoryOrder.training_id == training_id, InventoryOrder.user_id == user_id,
                InventoryOrder.status == OrderStatus.ACTIVE,
            )
        )
        clash = kinds & set(ordered)
        if clash:
            raise _already_ordered_error(clash)

    async def _compat(self) -> set[tuple[str, str]]:
        return {(r.cleat_type, r.pedal_type) for r in await self.session.scalars(select(CleatCompat))}

    async def ordered_gear(self, training_id: int, user_id: int) -> list[InventoryItem]:
        """Педали и туфли, уже заказанные участником на эту тренировку (активные заказы)."""
        return list(
            await self.session.scalars(
                select(InventoryItem)
                .join(InventoryOrder, InventoryOrder.item_id == InventoryItem.id)
                .where(
                    InventoryOrder.training_id == training_id, InventoryOrder.user_id == user_id,
                    InventoryOrder.status == OrderStatus.ACTIVE,
                    InventoryItem.kind.in_([ItemKind.PEDALS, ItemKind.SHOES]),
                )
            )
        )

    async def own_shoes_problem(self, own_cleat: str | None, pedal_type: str | None) -> str | None:
        return own_shoes_problem(own_cleat, pedal_type, await self._compat())

    # ---- подбор ----

    async def suggest(self, training_id: int, user: User, wanted: Wanted, now: datetime) -> list[Suggestion]:
        training = await self.check_can_order(training_id, user.telegram_id, now)
        await self._reject_kinds_already_ordered(training_id, user.telegram_id, set(wanted.kinds))
        profile = Profile(user.height_cm, user.foot_eu, user.helmet_size)
        if missing_profile_fields(profile, wanted.kinds):
            raise InvalidAction("Не хватает данных профиля — заполните /profile")
        if ItemKind.PEDALS in wanted.kinds and wanted.pedal_type is None:
            raise InvalidAction("Выберите тип педалей")
        compat = await self._compat()
        problem = own_shoes_problem(wanted.own_shoes_cleat, wanted.pedal_type, compat)
        if problem:
            raise InvalidAction(problem)
        items = await self.session.scalars(
            select(InventoryItem).where(InventoryItem.active.is_(True), InventoryItem.kind.in_(list(wanted.kinds)))
        )
        busy = await busy_item_ids(self.session, training)
        candidates = [to_candidate(i) for i in items if i.id not in busy]
        return suggest(profile, wanted, candidates, compat)

    # ---- подтверждение ----

    async def confirm(self, training_id: int, user_id: int, item_ids: list[int], now: datetime) -> ConfirmResult:
        """Заказывает выбранные единицы. Каждая единица берётся ОДНИМ оператором
        (INSERT … SELECT … WHERE свободна AND вид не заказан AND участник записан), который SQLite выполняет под
        блокировкой записи: отдельный SELECT «свободна ли» идёт без блокировки, и параллельные подтверждения
        могли бы взять одну единицу дважды (§8). Занятые к этому моменту единицы попадают в `conflicts`,
        остальные позиции заказываются."""
        training = await self.check_can_order(training_id, user_id, now)
        if not item_ids or len(set(item_ids)) != len(item_ids):
            raise InvalidAction("Нечего заказывать")
        # FOR UPDATE: на PostgreSQL сериализует конкурентные заказы одной единицы (SQLite игнорирует)
        items = list(
            await self.session.scalars(
                select(InventoryItem).where(InventoryItem.id.in_(item_ids)).order_by(InventoryItem.id).with_for_update()
            )
        )
        if len(items) != len(item_ids):
            raise NotFound("Единица инвентаря не найдена")
        kinds = [i.kind for i in items]
        if len(set(kinds)) != len(kinds):
            raise InvalidAction("Можно заказать одну единицу каждого вида")
        await self._reject_kinds_already_ordered(training_id, user_id, set(kinds))
        # совместимость — со всем набором участника: педали/туфли, заказанные раньше, тоже считаются
        held = [i for i in await self.ordered_gear(training_id, user_id) if i.kind not in kinds]
        problems = set_problems([to_candidate(i) for i in items + held], await self._compat())
        if problems:
            raise InvalidAction(problems[0])
        overlapping = await overlapping_training_ids(self.session, training)
        taken: list[int] = []
        conflicts: list[str] = []
        for item in items:
            if await self._take_item(training, user_id, item, overlapping, now):
                taken.append(item.id)
            else:
                await self._explain_miss(training, user_id, item)  # бросает, если дело не в занятости
                conflicts.append(item.kind)
        await self.session.commit()
        orders = list(
            await self.session.scalars(
                select(InventoryOrder).where(
                    InventoryOrder.training_id == training_id, InventoryOrder.user_id == user_id,
                    InventoryOrder.status == OrderStatus.ACTIVE, InventoryOrder.item_id.in_(taken),
                ).order_by(InventoryOrder.id)
            )
        ) if taken else []
        return ConfirmResult(orders, conflicts)

    async def _take_item(
        self, training: Training, user_id: int, item: InventoryItem, overlapping: set[int], now: datetime
    ) -> bool:
        item_free = ~exists().where(
            InventoryOrder.item_id == item.id, InventoryOrder.status == OrderStatus.ACTIVE,
            InventoryOrder.training_id.in_(overlapping),
        )
        item_active = exists().where(InventoryItem.id == item.id, InventoryItem.active.is_(True))
        going = exists().where(
            Participation.training_id == training.id, Participation.user_id == user_id,
            Participation.status == ParticipationStatus.GOING,
        )
        scheduled = exists().where(Training.id == training.id, Training.status == TrainingStatus.SCHEDULED)
        stmt = insert(InventoryOrder).from_select(
            ["training_id", "user_id", "item_id", "status", "price", "created_at"],
            select(
                literal(training.id, Integer),
                literal(user_id, BigInteger),
                literal(item.id, Integer),
                literal(OrderStatus.ACTIVE, String),
                literal(item.price_per_use, Integer),
                literal(now, UTCDateTime),
            ).where(item_free, item_active, going, scheduled, ~_kind_ordered(training.id, user_id, item.kind)),
        )
        return (await self.session.execute(stmt)).rowcount == 1

    async def _explain_miss(self, training: Training, user_id: int, item: InventoryItem) -> None:
        """Единицу не взяли. Если причина — отмена тренировки, отказ от записи или параллельный заказ того же
        вида (двойное нажатие), откатывает всё подтверждение и бросает ошибку; иначе единица просто занята."""
        reason: InvalidAction | None = None
        status = await self.session.scalar(select(Training.status).where(Training.id == training.id))
        if status != TrainingStatus.SCHEDULED:
            reason = InvalidAction("На эту тренировку инвентарь заказать уже нельзя")
        elif await self.session.scalar(
            select(Participation.status).where(
                Participation.training_id == training.id, Participation.user_id == user_id
            )
        ) != ParticipationStatus.GOING:
            reason = NotGoing("Сначала запишитесь на тренировку")
        elif await self.session.scalar(select(_kind_ordered(training.id, user_id, item.kind))):
            reason = _already_ordered_error({item.kind})
        if reason is not None:
            await self.session.rollback()
            raise reason

    # ---- мои заказы и отмена ----

    async def my_orders(self, user_id: int, now: datetime) -> list[OrderView]:
        rows = await self.session.execute(
            select(InventoryOrder, InventoryItem, Training)
            .join(InventoryItem, InventoryItem.id == InventoryOrder.item_id)
            .join(Training, Training.id == InventoryOrder.training_id)
            .where(
                InventoryOrder.user_id == user_id, InventoryOrder.status == OrderStatus.ACTIVE,
                Training.starts_at > now, Training.status == TrainingStatus.SCHEDULED,
            )
            .order_by(Training.starts_at, InventoryItem.kind)
        )
        return [OrderView(o, i, t) for o, i, t in rows]

    async def cancel_order(self, order_id: int, user_id: int, now: datetime) -> InventoryOrder:
        order = await self.session.get(InventoryOrder, order_id)
        if order is None:
            raise NotFound("Заказ не найден")
        if order.user_id != user_id:
            raise PermissionDenied("Это не ваш заказ")
        training = await self.session.get(Training, order.training_id)
        if training.starts_at <= now:
            raise InvalidAction("Тренировка уже началась — заказ отменить нельзя")
        if not await self._cancel_one(order.id):  # условный UPDATE: из двух нажатий отменяет одно
            raise InvalidAction("Заказ уже отменён")
        await self.session.commit()
        return order

    async def _cancel_one(self, order_id: int) -> bool:
        result = await self.session.execute(
            update(InventoryOrder)
            .where(InventoryOrder.id == order_id, InventoryOrder.status == OrderStatus.ACTIVE)
            .values(status=OrderStatus.CANCELLED)
            .execution_options(synchronize_session="fetch")
        )
        return result.rowcount == 1

    async def _cancel_where(self, *conditions) -> list[InventoryOrder]:
        """Отменяет активные заказы и возвращает те, что отменил именно этот вызов (идемпотентно)."""
        candidates = list(
            await self.session.scalars(
                select(InventoryOrder).where(InventoryOrder.status == OrderStatus.ACTIVE, *conditions)
                .order_by(InventoryOrder.id)
            )
        )
        cancelled = [order for order in candidates if await self._cancel_one(order.id)]
        await self.session.commit()
        return cancelled

    async def cancel_for_participant(self, training_id: int, user_id: int) -> list[InventoryOrder]:
        return await self._cancel_where(InventoryOrder.training_id == training_id, InventoryOrder.user_id == user_id)

    async def cancel_for_training(self, training_id: int) -> list[InventoryOrder]:
        return await self._cancel_where(InventoryOrder.training_id == training_id)

    async def cancel_for_item(self, actor: User, item_id: int, now: datetime) -> list[InventoryOrder]:
        """Единицу вывели из парка: отменяет её активные заказы на будущие запланированные тренировки.
        Прошедшие и уже идущие тренировки не трогает. Только админ (вызывается из usecases.set_item_active)."""
        require_admin(actor)
        future = select(Training.id).where(Training.status == TrainingStatus.SCHEDULED, Training.starts_at > now)
        return await self._cancel_where(InventoryOrder.item_id == item_id, InventoryOrder.training_id.in_(future))

    # ---- сводка тренера ----

    async def training_summary(self, actor: User, training_id: int) -> list[OrderLine]:
        training = await self.session.get(Training, training_id)
        if training is None:
            raise NotFound("Тренировка не найдена")
        if not await can_manage(self.session, actor, training):
            raise PermissionDenied("Нет прав на эту тренировку")
        rows = await self.session.execute(
            select(User.telegram_id, User.name, InventoryItem)
            .join(InventoryOrder, InventoryOrder.user_id == User.telegram_id)
            .join(InventoryItem, InventoryItem.id == InventoryOrder.item_id)
            .where(InventoryOrder.training_id == training_id, InventoryOrder.status == OrderStatus.ACTIVE)
            .order_by(User.name, InventoryItem.kind)
        )
        return [
            OrderLine(
                user_id=user_id, user_name=name, kind=item.kind, label=item.label, frame=item.frame,
                shoe_eu=item.shoe_eu, pedal_type=item.pedal_type, helmet_size=item.helmet_size,
            )
            for user_id, name, item in rows
        ]
