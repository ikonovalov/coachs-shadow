from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.models import PriceRule, SubscriptionPlan, SubscriptionPlanSeries
from bot.billing.queries import Prices
from bot.core.errors import InvalidAction, NotFound
from bot.core.permissions import require_admin
from bot.schedule.models import TrainingKind, TrainingSeries
from bot.users.models import User

MAX_PLAN_NAME = 100


def _check_prices(prices: Prices) -> None:
    for value in (prices.single_price, prices.surcharge_per_person):
        if value is not None and value < 0:
            raise InvalidAction("Сумма не может быть отрицательной")


class CatalogService:
    """Цены по типу и серии, тарифы абонементов. Меняет только админ."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def type_prices(self, kind: str) -> Prices:
        rule = await self.session.get(PriceRule, kind)
        return Prices(rule.single_price, rule.surcharge_per_person) if rule else Prices()

    async def set_type_prices(self, actor: User, kind: str, prices: Prices) -> None:
        require_admin(actor)
        if kind not in TrainingKind.ALL:
            raise NotFound("Неизвестный тип тренировки")
        _check_prices(prices)
        rule = await self.session.get(PriceRule, kind)
        if rule is None:
            rule = PriceRule(kind=kind)
            self.session.add(rule)
        rule.single_price = prices.single_price
        rule.surcharge_per_person = prices.surcharge_per_person
        await self.session.commit()

    async def set_series_prices(self, actor: User, series_id: int, prices: Prices) -> None:
        require_admin(actor)
        _check_prices(prices)
        series = await self.session.get(TrainingSeries, series_id)
        if series is None:
            raise NotFound("Серия не найдена")
        series.single_price = prices.single_price
        series.surcharge_per_person = prices.surcharge_per_person
        await self.session.commit()

    async def create_plan(
        self, actor: User, name: str, sessions: int, price: int, valid_months: int | None, series_ids: list[int]
    ) -> SubscriptionPlan:
        require_admin(actor)
        name = name.strip()
        if not name or len(name) > MAX_PLAN_NAME:
            raise InvalidAction(f"Название — от 1 до {MAX_PLAN_NAME} символов")
        if not 1 <= sessions <= 100:
            raise InvalidAction("Занятий — от 1 до 100")
        if price < 0:
            raise InvalidAction("Цена не может быть отрицательной")
        if valid_months is not None and not 1 <= valid_months <= 24:
            raise InvalidAction("Срок — от 1 до 24 месяцев или бессрочный")
        ids = sorted(set(series_ids))
        if not ids:
            raise InvalidAction("Выберите хотя бы одну серию")
        found = set(await self.session.scalars(select(TrainingSeries.id).where(TrainingSeries.id.in_(ids))))
        if found != set(ids):
            raise NotFound("Серия не найдена")
        plan = SubscriptionPlan(name=name, sessions=sessions, price=price, valid_months=valid_months)
        self.session.add(plan)
        await self.session.flush()
        for series_id in ids:
            self.session.add(SubscriptionPlanSeries(plan_id=plan.id, series_id=series_id))
        await self.session.commit()
        return plan

    async def list_plans(self) -> list[SubscriptionPlan]:
        return list(await self.session.scalars(select(SubscriptionPlan).order_by(SubscriptionPlan.id)))

    async def plan_series(self, plan_id: int) -> list[TrainingSeries]:
        rows = await self.session.scalars(
            select(TrainingSeries)
            .join(SubscriptionPlanSeries, SubscriptionPlanSeries.series_id == TrainingSeries.id)
            .where(SubscriptionPlanSeries.plan_id == plan_id)
            .order_by(TrainingSeries.id)
        )
        return list(rows)

    async def delete_plan(self, actor: User, plan_id: int) -> SubscriptionPlan:
        """Выданные абонементы не меняются: название, цена и набор серий скопированы при выдаче."""
        require_admin(actor)
        plan = await self.session.get(SubscriptionPlan, plan_id)
        if plan is None:
            raise NotFound("Тариф не найден")
        await self.session.execute(delete(SubscriptionPlanSeries).where(SubscriptionPlanSeries.plan_id == plan_id))
        await self.session.delete(plan)
        await self.session.commit()
        return plan
