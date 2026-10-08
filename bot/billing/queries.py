"""Read-хелперы биллинга: их можно импортировать из других фич (как bot/schedule/queries.py)."""
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.models import PriceRule, Subscription, SubscriptionSeries
from bot.schedule.models import Training, TrainingSeries


@dataclass(frozen=True)
class Prices:
    single_price: int | None = None  # копейки; None — не задано
    surcharge_per_person: int | None = None  # None — не задано; 0 — явно без доплаты


async def resolve_prices(session: AsyncSession, series: TrainingSeries) -> Prices:
    """Для каждого поля: значение серии, иначе значение типа тренировки, иначе None."""
    rule = await session.get(PriceRule, series.kind)
    inherited = Prices(rule.single_price, rule.surcharge_per_person) if rule else Prices()
    return Prices(
        series.single_price if series.single_price is not None else inherited.single_price,
        series.surcharge_per_person if series.surcharge_per_person is not None else inherited.surcharge_per_person,
    )


async def effective_prices(session: AsyncSession, training: Training) -> Prices:
    """Цены тренировки: снимок, а недостающее поле — из серии/типа на текущий момент. Один источник правды для
    карточки, подсказки и начисления, чтобы ученик видел в посте ту же сумму, которую ему начислят."""
    if training.single_price is not None and training.surcharge_per_person is not None:
        return Prices(training.single_price, training.surcharge_per_person)
    series = await session.get(TrainingSeries, training.series_id)
    resolved = await resolve_prices(session, series)
    return Prices(
        training.single_price if training.single_price is not None else resolved.single_price,
        training.surcharge_per_person if training.surcharge_per_person is not None else resolved.surcharge_per_person,
    )


async def candidate_subscription_ids(session: AsyncSession, training: Training, user_id: int) -> list[int]:
    """Абонементы ученика, которыми можно закрыть тренировку: серия в наборе, есть остаток, выдан до старта,
    срок не истёк к старту. Сначала истекающие раньше, бессрочные последними."""
    rows = await session.scalars(
        select(Subscription.id)
        .join(SubscriptionSeries, SubscriptionSeries.subscription_id == Subscription.id)
        .where(
            Subscription.user_id == user_id,
            SubscriptionSeries.series_id == training.series_id,
            Subscription.sessions_left > 0,
            Subscription.starts_at <= training.starts_at,
            or_(Subscription.expires_at.is_(None), Subscription.expires_at >= training.starts_at),
        )
        .order_by(Subscription.expires_at.is_(None), Subscription.expires_at, Subscription.id)
    )
    return list(rows)
