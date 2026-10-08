"""Read-хелперы биллинга: их можно импортировать из других фич (как bot/schedule/queries.py)."""
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.models import PriceRule
from bot.schedule.models import TrainingSeries


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
