import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.models import (
    Attendance,
    Charge,
    ChargeKind,
    ChargeStatus,
    Subscription,
    SubscriptionPlan,
    SubscriptionPlanSeries,
    SubscriptionSeries,
)
from bot.billing.pricing import subscription_expiry
from bot.billing.queries import Prices, candidate_subscription_ids, effective_prices
from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.money import surcharge_share
from bot.core.permissions import Role, require_admin
from bot.core.settings import get_setting
from bot.inventory.models import InventoryOrder, OrderStatus
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Training, TrainingSeries, TrainingStatus
from bot.schedule.queries import can_manage
from bot.users.models import User

log = logging.getLogger(__name__)

ATTENDANCE_WINDOW = timedelta(hours=72)


@dataclass
class AttendanceResult:
    changed: bool = False
    returned_to: str | None = None  # название абонемента, на который вернули занятие
    refund_due: bool = False  # было оплачено: деньги нужно вернуть
    pending_changes: list[tuple[int, int]] = field(default_factory=list)  # (user_id, новая сумма доплаты)
    paid_diffs: list[tuple[int, int, int]] = field(default_factory=list)  # (user_id, оплачено, теперь должно быть)


class BillingService:
    """Выдача абонементов, начисления, оплата и посещение. Остаток абонемента и статусы начислений меняются
    только условным UPDATE с проверкой rowcount (см. ARCHITECTURE.md §8)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- абонементы ----

    async def issue_subscription(
        self, actor: User, user_id: int, plan_id: int, *, paid: bool, now: datetime
    ) -> Subscription:
        require_admin(actor)
        plan = await self.session.get(SubscriptionPlan, plan_id)
        if plan is None:
            raise NotFound("Тариф не найден")
        if await self.session.get(User, user_id) is None:
            raise NotFound("Пользователь не найден")
        series_ids = list(
            await self.session.scalars(
                select(SubscriptionPlanSeries.series_id)
                .where(SubscriptionPlanSeries.plan_id == plan_id)
                .order_by(SubscriptionPlanSeries.series_id)
            )
        )
        tz = ZoneInfo(await get_setting(self.session, "club_timezone"))
        sub = Subscription(
            user_id=user_id,
            name=plan.name,
            sessions_total=plan.sessions,
            sessions_left=plan.sessions,
            starts_at=now,
            expires_at=subscription_expiry(now, plan.valid_months, tz),
            price=plan.price,
            paid_at=now if paid else None,
            paid_by=actor.telegram_id if paid else None,
            issued_by=actor.telegram_id,
            created_at=now,
        )
        self.session.add(sub)
        await self.session.flush()
        for series_id in series_ids:
            self.session.add(SubscriptionSeries(subscription_id=sub.id, series_id=series_id))
        await self.session.commit()
        log.info("subscription %s issued to %s by %s (paid=%s)", sub.id, user_id, actor.telegram_id, paid)
        return sub

    # ---- начисления ----

    async def attendee_ids(self, training_id: int) -> list[int]:
        """Кто считается пришедшим: записавшиеся без отметки «не пришёл» и отмеченные «пришёл» без записи."""
        going = list(
            await self.session.scalars(
                select(Participation.user_id)
                .where(Participation.training_id == training_id, Participation.status == ParticipationStatus.GOING)
                .order_by(Participation.updated_at, Participation.user_id)
            )
        )
        marks = {
            user_id: present
            for user_id, present in await self.session.execute(
                select(Attendance.user_id, Attendance.present).where(Attendance.training_id == training_id)
            )
        }
        result = [uid for uid in going if marks.get(uid, True)]
        result += sorted(uid for uid, present in marks.items() if present and uid not in going)
        return result

    async def charge_training(self, training_id: int, now: datetime) -> list[Charge]:
        """Начисления за закрытую тренировку. Идемпотентно: уже созданное пропускается (уникальный ключ Charge).
        Возвращает только начисления, созданные этим вызовом."""
        training = await self.session.get(Training, training_id)
        if training is None or training.status != TrainingStatus.DONE:
            return []
        prices = await self._prices(training)
        attendees = await self.attendee_ids(training_id)
        share = surcharge_share(prices.surcharge_per_person, len(attendees)) if prices.surcharge_per_person else None
        created: list[Charge] = []
        for user_id in attendees:
            # откат при конфликте уникального ключа сбрасывает все объекты сессии, поэтому тренировку читаем заново
            training = await self.session.get(Training, training_id)
            created += await self._charge_participant(training, user_id, prices, share, now)
        log.info("charged training %s: %d new charge(s)", training_id, len(created))
        return created

    async def _prices(self, training: Training) -> Prices:
        return await effective_prices(self.session, training)

    async def _take_unit(self, training: Training, user_id: int) -> int | None:
        for sub_id in await candidate_subscription_ids(self.session, training, user_id):
            taken = await self.session.execute(
                update(Subscription)
                .where(Subscription.id == sub_id, Subscription.sessions_left > 0)
                .values(sessions_left=Subscription.sessions_left - 1)
                .execution_options(synchronize_session="fetch")
            )
            if taken.rowcount == 1:
                return sub_id
        return None

    async def _charge_participant(
        self, training: Training, user_id: int, prices: Prices, share: int | None, now: datetime, *,
        commit: bool = True,
    ) -> list[Charge]:
        existing = {
            (kind, ref_id)
            for kind, ref_id in await self.session.execute(
                select(Charge.kind, Charge.ref_id).where(Charge.training_id == training.id, Charge.user_id == user_id)
            )
        }

        def new(kind: str, amount: int, status: str, ref_id: int = 0, subscription_id: int | None = None) -> Charge:
            return Charge(
                user_id=user_id, training_id=training.id, kind=kind, ref_id=ref_id, amount=amount, status=status,
                subscription_id=subscription_id, created_at=now,
            )

        created: list[Charge] = []
        if (ChargeKind.SESSION, 0) not in existing:
            sub_id = await self._take_unit(training, user_id)
            if sub_id is not None:
                created.append(new(ChargeKind.SESSION, prices.single_price or 0, ChargeStatus.COVERED, subscription_id=sub_id))
            elif prices.single_price:
                created.append(new(ChargeKind.SESSION, prices.single_price, ChargeStatus.PENDING))
        if share and (ChargeKind.SURCHARGE, 0) not in existing:
            created.append(new(ChargeKind.SURCHARGE, share, ChargeStatus.PENDING))
        orders = await self.session.scalars(
            select(InventoryOrder).where(
                InventoryOrder.training_id == training.id,
                InventoryOrder.user_id == user_id,
                InventoryOrder.status == OrderStatus.ACTIVE,
                InventoryOrder.price.is_not(None),
                InventoryOrder.price > 0,
            )
        )
        for order in list(orders):
            if (ChargeKind.INVENTORY, order.id) not in existing:
                created.append(new(ChargeKind.INVENTORY, order.price, ChargeStatus.PENDING, ref_id=order.id))
        if not created:
            return []
        self.session.add_all(created)
        if not commit:
            await self.session.flush()
            return created
        try:
            await self.session.commit()
        except IntegrityError:  # параллельный запуск успел раньше: откат вернёт и списанную единицу
            await self.session.rollback()
            return []
        return created

    # ---- оплата и возвраты (только админ) ----

    async def mark_charge_paid(self, actor: User, charge_id: int, now: datetime) -> Charge:
        require_admin(actor)
        result = await self.session.execute(
            update(Charge)
            .where(Charge.id == charge_id, Charge.status == ChargeStatus.PENDING)
            .values(status=ChargeStatus.PAID, paid_at=now, paid_by=actor.telegram_id)
            .execution_options(synchronize_session="fetch")
        )
        await self.session.commit()
        if result.rowcount != 1:
            raise InvalidAction("Начисление уже оплачено или отменено")
        charge = await self.session.get(Charge, charge_id)
        await self.session.refresh(charge)  # bulk UPDATE не заполняет ещё не загруженные поля (paid_at, refunded_at…)
        return charge

    async def mark_subscription_paid(self, actor: User, subscription_id: int, now: datetime) -> Subscription:
        require_admin(actor)
        result = await self.session.execute(
            update(Subscription)
            .where(Subscription.id == subscription_id, Subscription.paid_at.is_(None))
            .values(paid_at=now, paid_by=actor.telegram_id)
            .execution_options(synchronize_session="fetch")
        )
        await self.session.commit()
        if result.rowcount != 1:
            raise InvalidAction("Абонемент уже оплачен")
        sub = await self.session.get(Subscription, subscription_id)
        await self.session.refresh(sub)  # bulk UPDATE не заполняет ещё не загруженные поля (paid_at, refunded_at…)
        return sub

    async def mark_user_paid(self, actor: User, user_id: int, now: datetime) -> int:
        """Закрывает все долги ученика: начисления pending и неоплаченные абонементы."""
        require_admin(actor)
        charges = await self.session.execute(
            update(Charge)
            .where(Charge.user_id == user_id, Charge.status == ChargeStatus.PENDING)
            .values(status=ChargeStatus.PAID, paid_at=now, paid_by=actor.telegram_id)
            .execution_options(synchronize_session="fetch")
        )
        subscriptions = await self.session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id, Subscription.paid_at.is_(None))
            .values(paid_at=now, paid_by=actor.telegram_id)
            .execution_options(synchronize_session="fetch")
        )
        await self.session.commit()
        return charges.rowcount + subscriptions.rowcount

    async def mark_refunded(self, actor: User, charge_id: int, now: datetime) -> Charge:
        require_admin(actor)
        result = await self.session.execute(
            update(Charge)
            .where(
                Charge.id == charge_id,
                Charge.status == ChargeStatus.VOID,
                Charge.refund_due.is_(True),
                Charge.refunded_at.is_(None),
            )
            .values(refunded_at=now)
            .execution_options(synchronize_session="fetch")
        )
        await self.session.commit()
        if result.rowcount != 1:
            raise InvalidAction("Возврат уже проведён или не требуется")
        charge = await self.session.get(Charge, charge_id)
        await self.session.refresh(charge)  # bulk UPDATE не заполняет ещё не загруженные поля (paid_at, refunded_at…)
        return charge

    # ---- посещение ----

    async def set_attendance(
        self, actor: User, training_id: int, user_id: int, present: bool, now: datetime
    ) -> AttendanceResult:
        training = await self.session.get(Training, training_id)
        if training is None:
            raise NotFound("Тренировка не найдена")
        if training.status != TrainingStatus.DONE:
            raise InvalidAction("Посещение можно отмечать после начала тренировки")
        if not await can_manage(self.session, actor, training):
            raise PermissionDenied("Нет прав на эту тренировку")
        if actor.role != Role.ADMIN and now > training.ends_at + ATTENDANCE_WINDOW:
            raise InvalidAction("Прошло больше 72 часов: отметку может изменить только администратор")
        if await self.session.get(User, user_id) is None:
            raise NotFound("Пользователь не найден")
        if (user_id in await self.attendee_ids(training_id)) == present:
            return AttendanceResult()
        mark = await self.session.get(Attendance, (training_id, user_id))
        if mark is None:
            self.session.add(
                Attendance(training_id=training_id, user_id=user_id, present=present, marked_by=actor.telegram_id, marked_at=now)
            )
        else:
            mark.present, mark.marked_by, mark.marked_at = present, actor.telegram_id, now
        await self.session.flush()
        result = AttendanceResult(changed=True)
        if present:
            await self._reinstate(training, user_id, now)
        else:
            await self._void_charges(training_id, user_id, result)
        await self._recompute_surcharge(training, result)
        await self.session.commit()
        log.info("attendance training=%s user=%s present=%s by=%s", training_id, user_id, present, actor.telegram_id)
        return result

    async def _cond_update(self, charge_id: int, from_status: str, **values) -> bool:
        result = await self.session.execute(
            update(Charge)
            .where(Charge.id == charge_id, Charge.status == from_status)
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )
        return result.rowcount == 1

    async def _void_charges(self, training_id: int, user_id: int, result: AttendanceResult) -> None:
        charges = await self.session.scalars(
            select(Charge).where(
                Charge.training_id == training_id, Charge.user_id == user_id, Charge.status != ChargeStatus.VOID
            )
        )
        for charge in list(charges):
            if await self._cond_update(charge.id, ChargeStatus.PENDING, status=ChargeStatus.VOID):
                continue
            if await self._cond_update(charge.id, ChargeStatus.PAID, status=ChargeStatus.VOID, refund_due=True):
                result.refund_due = True
                continue
            if await self._cond_update(charge.id, ChargeStatus.COVERED, status=ChargeStatus.VOID):
                returned = await self.session.execute(
                    update(Subscription)
                    .where(Subscription.id == charge.subscription_id, Subscription.sessions_left < Subscription.sessions_total)
                    .values(sessions_left=Subscription.sessions_left + 1)
                    .execution_options(synchronize_session="fetch")
                )
                if returned.rowcount == 1:
                    result.returned_to = (await self.session.get(Subscription, charge.subscription_id)).name

    async def _reinstate(self, training: Training, user_id: int, now: datetime) -> None:
        """«Пришёл» после «не пришёл» или без записи: оплаченное восстанавливается, остальное начисляется заново."""
        voided = await self.session.scalars(
            select(Charge).where(
                Charge.training_id == training.id, Charge.user_id == user_id, Charge.status == ChargeStatus.VOID
            )
        )
        for charge in list(voided):
            if charge.refund_due and charge.refunded_at is None:  # деньги ещё у клуба
                charge.status, charge.refund_due = ChargeStatus.PAID, False
            elif charge.refund_due:  # деньги уже вернули: ученик снова должен
                charge.status, charge.refund_due = ChargeStatus.PENDING, False
                charge.refunded_at = charge.paid_at = charge.paid_by = None
            else:
                await self.session.delete(charge)  # будет создано заново (и заново спишет единицу абонемента)
        await self.session.flush()
        prices = await self._prices(training)
        attendees = await self.attendee_ids(training.id)
        share = surcharge_share(prices.surcharge_per_person, len(attendees)) if prices.surcharge_per_person else None
        await self._charge_participant(training, user_id, prices, share, now, commit=False)

    async def _recompute_surcharge(self, training: Training, result: AttendanceResult) -> None:
        tariff = (await self._prices(training)).surcharge_per_person
        attendees = await self.attendee_ids(training.id)
        if not tariff or not attendees:
            return
        share = surcharge_share(tariff, len(attendees))
        rows = await self.session.scalars(
            select(Charge)
            .where(
                Charge.training_id == training.id,
                Charge.kind == ChargeKind.SURCHARGE,
                Charge.user_id.in_(attendees),
                Charge.status.in_([ChargeStatus.PENDING, ChargeStatus.PAID]),
            )
            .order_by(Charge.user_id)
        )
        for charge in list(rows):
            if charge.amount == share:
                continue
            if charge.status == ChargeStatus.PENDING:
                charge.amount = share
                result.pending_changes.append((charge.user_id, share))
            else:
                result.paid_diffs.append((charge.user_id, charge.amount, share))
