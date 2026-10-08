import pytest

from bot.billing.catalog import CatalogService
from bot.billing.queries import Prices, resolve_prices
from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.schedule.models import TrainingSeries
from tests.billing.helpers import done_training, ensure_user


async def admin(session):
    return await ensure_user(session, 99, "admin")


async def series_of(session, training) -> TrainingSeries:
    return await session.get(TrainingSeries, training.series_id)


async def test_only_admin_sets_prices_and_creates_plans(session):
    t = await done_training(session)
    member = await ensure_user(session, 1)
    trainer = await ensure_user(session, 10, "trainer")
    svc = CatalogService(session)
    for actor in (member, trainer):
        with pytest.raises(PermissionDenied):
            await svc.set_type_prices(actor, "road", Prices(70000, None))
        with pytest.raises(PermissionDenied):
            await svc.set_series_prices(actor, t.series_id, Prices(70000, None))
        with pytest.raises(PermissionDenied):
            await svc.create_plan(actor, "План", 4, 100, 1, [t.series_id])


async def test_series_value_overrides_type_value_field_by_field(session):
    t = await done_training(session)
    svc = CatalogService(session)
    await svc.set_type_prices(await admin(session), "road", Prices(70000, 50000))
    series = await series_of(session, t)
    assert await resolve_prices(session, series) == Prices(70000, 50000)
    await svc.set_series_prices(await admin(session), series.id, Prices(90000, None))
    assert await resolve_prices(session, await series_of(session, t)) == Prices(90000, 50000)
    await svc.set_series_prices(await admin(session), series.id, Prices(None, 0))  # 0 — явно без доплаты
    assert await resolve_prices(session, await series_of(session, t)) == Prices(70000, 0)


async def test_resolve_without_any_rule_is_empty(session):
    t = await done_training(session)
    assert await resolve_prices(session, await series_of(session, t)) == Prices(None, None)


async def test_price_validation(session):
    t = await done_training(session)
    svc = CatalogService(session)
    with pytest.raises(NotFound):
        await svc.set_type_prices(await admin(session), "swim", Prices(1, 1))
    with pytest.raises(InvalidAction):
        await svc.set_type_prices(await admin(session), "road", Prices(-1, None))
    with pytest.raises(NotFound):
        await svc.set_series_prices(await admin(session), 9999, Prices(1, 1))


async def test_create_list_and_delete_plan(session):
    t = await done_training(session)
    svc = CatalogService(session)
    plan = await svc.create_plan(await admin(session), " Утро+вечер ", 8, 550000, None, [t.series_id, t.series_id])
    assert plan.name == "Утро+вечер" and plan.valid_months is None
    assert [p.id for p in await svc.list_plans()] == [plan.id]
    assert [s.id for s in await svc.plan_series(plan.id)] == [t.series_id]
    await svc.delete_plan(await admin(session), plan.id)
    assert await svc.list_plans() == []
    with pytest.raises(NotFound):
        await svc.delete_plan(await admin(session), plan.id)


@pytest.mark.parametrize("kw", [
    dict(name="  "), dict(name="х" * 101), dict(sessions=0), dict(sessions=101), dict(price=-1),
    dict(valid_months=0), dict(valid_months=25), dict(series_ids=[]),
])
async def test_plan_validation(session, kw):
    t = await done_training(session)
    args = dict(name="План", sessions=4, price=100, valid_months=1, series_ids=[t.series_id])
    args.update(kw)
    with pytest.raises(InvalidAction):
        await CatalogService(session).create_plan(await admin(session), **args)


async def test_plan_with_unknown_series_is_rejected(session):
    await done_training(session)
    with pytest.raises(NotFound):
        await CatalogService(session).create_plan(await admin(session), "План", 4, 100, 1, [9999])
