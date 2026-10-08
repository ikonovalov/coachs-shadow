from datetime import date, time, timedelta

from sqlalchemy import select

from bot.billing.catalog import CatalogService
from bot.billing.queries import Prices
from bot.schedule.models import Training
from bot.schedule.service import ScheduleService, SeriesInput
from tests.factories import NOW, add_level, add_user


def one_off(level):
    return SeriesInput(kind="track", place="Велодром", level_ids=[level.id], start_date=date(2026, 10, 25), start_time=time(19, 0))


async def test_new_trainings_snapshot_type_prices_and_later_changes_do_not_rewrite_them(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    catalog = CatalogService(session)
    await catalog.set_type_prices(admin, "track", Prices(90000, 50000))
    series = await ScheduleService(session).create_series(admin, one_off(level), NOW)
    await catalog.set_type_prices(admin, "track", Prices(100000, 60000))
    training = (await session.scalars(select(Training))).one()
    assert (training.series_id, training.single_price, training.surcharge_per_person) == (series.id, 90000, 50000)


async def test_series_override_applies_to_trainings_generated_later(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    await CatalogService(session).set_type_prices(admin, "track", Prices(90000, 50000))
    weekly = SeriesInput(
        kind="track", place="Велодром", level_ids=[level.id], start_date=date(2026, 10, 20), start_time=time(19, 0),
        weekdays={2},
    )
    series = await ScheduleService(session).create_series(admin, weekly, NOW)
    first = list(await session.scalars(select(Training)))
    assert {(t.single_price, t.surcharge_per_person) for t in first} == {(90000, 50000)}
    await CatalogService(session).set_series_prices(admin, series.id, Prices(70000, 0))
    await ScheduleService(session).generate(NOW + timedelta(weeks=3))  # окно сдвинулось: появились новые вторники
    horizon = NOW + timedelta(weeks=4)
    prices = {t.starts_at: (t.single_price, t.surcharge_per_person) for t in await session.scalars(select(Training))}
    assert {p for at, p in prices.items() if at <= horizon} == {(90000, 50000)}  # старые — снимок типа
    assert {p for at, p in prices.items() if at > horizon} == {(70000, 0)}  # новые — переопределение серии


async def test_no_rules_leaves_snapshot_empty(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    await ScheduleService(session).create_series(admin, one_off(level), NOW)
    training = (await session.scalars(select(Training))).one()
    assert (training.single_price, training.surcharge_per_person) == (None, None)
