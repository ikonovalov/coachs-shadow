import logging
from datetime import datetime
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
from bot.billing.queries import Prices, candidate_subscription_ids, resolve_prices
from bot.core.errors import NotFound
from bot.core.money import surcharge_share
from bot.core.permissions import require_admin
from bot.core.settings import get_setting
from bot.inventory.models import InventoryOrder, OrderStatus
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Training, TrainingSeries, TrainingStatus
from bot.users.models import User

log = logging.getLogger(__name__)


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
            created += await self._charge_participant(training, user_id, prices, share, now)
        log.info("charged training %s: %d new charge(s)", training_id, len(created))
        return created

    async def _prices(self, training: Training) -> Prices:
        """Снимок цен тренировки; отсутствующие поля берутся из серии/типа на текущий момент."""
        if training.single_price is not None and training.surcharge_per_person is not None:
            return Prices(training.single_price, training.surcharge_per_person)
        series = await self.session.get(TrainingSeries, training.series_id)
        resolved = await resolve_prices(self.session, series)
        return Prices(
            training.single_price if training.single_price is not None else resolved.single_price,
            training.surcharge_per_person if training.surcharge_per_person is not None else resolved.surcharge_per_person,
        )

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
