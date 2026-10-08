from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.models import Attendance, Charge, ChargeStatus, Subscription
from bot.billing.queries import candidate_subscription_ids, effective_prices
from bot.core.money import format_money, format_range, surcharge_estimate
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Training, TrainingSeries, TrainingStatus
from bot.users.models import User

HISTORY_LIMIT = 10


@dataclass(frozen=True)
class Debtor:
    user: User
    total: int


@dataclass(frozen=True)
class DebtView:
    charges: list[tuple[Charge, Training]]
    subscriptions: list[Subscription]
    total: int


@dataclass(frozen=True)
class MyBilling:
    subscriptions: list[Subscription]
    debts: DebtView
    history: list[tuple[Charge, Training]]


class BillingViews:
    """Только чтение: списки и подсказки для экранов."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def debtors(self) -> list[Debtor]:
        totals: dict[int, int] = {}
        pending = await self.session.execute(
            select(Charge.user_id, func.sum(Charge.amount))
            .where(Charge.status == ChargeStatus.PENDING)
            .group_by(Charge.user_id)
        )
        unpaid = await self.session.execute(
            select(Subscription.user_id, func.sum(Subscription.price))
            .where(Subscription.paid_at.is_(None), Subscription.price > 0)
            .group_by(Subscription.user_id)
        )
        for user_id, total in [*pending, *unpaid]:
            totals[user_id] = totals.get(user_id, 0) + total
        if not totals:
            return []
        users = await self.session.scalars(select(User).where(User.telegram_id.in_(totals)).order_by(User.name))
        return [Debtor(user, totals[user.telegram_id]) for user in users if totals[user.telegram_id] > 0]

    async def user_debts(self, user_id: int) -> DebtView:
        charges = [
            (charge, training)
            for charge, training in await self.session.execute(
                select(Charge, Training)
                .join(Training, Training.id == Charge.training_id)
                .where(Charge.user_id == user_id, Charge.status == ChargeStatus.PENDING)
                .order_by(Training.starts_at, Charge.id)
            )
        ]
        subscriptions = list(
            await self.session.scalars(
                select(Subscription)
                .where(Subscription.user_id == user_id, Subscription.paid_at.is_(None), Subscription.price > 0)
                .order_by(Subscription.id)
            )
        )
        total = sum(c.amount for c, _ in charges) + sum(s.price for s in subscriptions)
        return DebtView(charges, subscriptions, total)

    async def my_overview(self, user_id: int, now: datetime) -> MyBilling:
        subscriptions = list(
            await self.session.scalars(
                select(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.sessions_left > 0,
                    or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
                )
                .order_by(Subscription.expires_at.is_(None), Subscription.expires_at, Subscription.id)
            )
        )
        history = [
            (charge, training)
            for charge, training in await self.session.execute(
                select(Charge, Training)
                .join(Training, Training.id == Charge.training_id)
                .where(Charge.user_id == user_id, Charge.status != ChargeStatus.PENDING)
                .order_by(Training.starts_at.desc(), Charge.id.desc())
                .limit(HISTORY_LIMIT)
            )
        ]
        return MyBilling(subscriptions, await self.user_debts(user_id), history)

    async def refunds_due(self) -> list[tuple[Charge, User, Training]]:
        rows = await self.session.execute(
            select(Charge, User, Training)
            .join(User, User.telegram_id == Charge.user_id)
            .join(Training, Training.id == Charge.training_id)
            .where(Charge.status == ChargeStatus.VOID, Charge.refund_due.is_(True), Charge.refunded_at.is_(None))
            .order_by(Charge.id)
        )
        return [(charge, user, training) for charge, user, training in rows]

    async def roster(self, training_id: int) -> list[tuple[User, bool]]:
        """Список для экрана посещения: записавшиеся и отмеченные вручную, с текущей отметкой «пришёл»."""
        going = list(
            await self.session.scalars(
                select(Participation.user_id)
                .where(Participation.training_id == training_id, Participation.status == ParticipationStatus.GOING)
                .order_by(Participation.updated_at, Participation.user_id)
            )
        )
        marks = {
            row.user_id: row
            for row in await self.session.scalars(select(Attendance).where(Attendance.training_id == training_id))
        }
        extra = [uid for uid, _ in sorted(marks.items(), key=lambda kv: (kv[1].marked_at, kv[0])) if uid not in going]
        ids = going + extra
        if not ids:
            return []
        users = {u.telegram_id: u for u in await self.session.scalars(select(User).where(User.telegram_id.in_(ids)))}
        return [(users[uid], marks[uid].present if uid in marks else True) for uid in ids if uid in users]

    async def payment_hint(self, training_id: int, user_id: int, now: datetime) -> str | None:
        """Что ждёт ученика на этой тренировке: списание с абонемента или сумма, плюс ориентир по доплате."""
        training = await self.session.get(Training, training_id)
        if training is None or training.status != TrainingStatus.SCHEDULED:
            return None
        prices = await effective_prices(self.session, training)
        lines: list[str] = []
        candidates = await candidate_subscription_ids(self.session, training, user_id)
        if candidates:
            sub = await self.session.get(Subscription, candidates[0])
            lines.append(f"Будет списано с абонемента «{sub.name}» (осталось {sub.sessions_left})")
        elif prices.single_price:
            lines.append(f"К оплате: {format_money(prices.single_price)}")
        if prices.surcharge_per_person:
            going = await self.session.scalar(
                select(func.count()).select_from(Participation).where(
                    Participation.training_id == training_id, Participation.status == ParticipationStatus.GOING
                )
            )
            low, high = surcharge_estimate(prices.surcharge_per_person, going, training.capacity)
            lines.append(f"Доплата ≈ {format_range(low, high)} (зависит от числа участников)")
        return "\n".join(lines) or None
