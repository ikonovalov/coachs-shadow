from bot.billing.catalog import CatalogService
from bot.billing.queries import Prices
from bot.core.settings import set_setting
from bot.publishing.service import PublishService
from tests.billing.helpers import done_training, ensure_user


async def test_card_shows_prices_only_when_billing_is_on(session, messenger):
    t = await done_training(session, price=70000, surcharge=50000)
    svc = PublishService(session, messenger, send_delay=0)
    off = await svc.build_card(t)
    assert (off.single_price, off.surcharge_per_person) == (None, None)
    await set_setting(session, "billing_enabled", "1")
    on = await svc.build_card(t)
    assert (on.single_price, on.surcharge_per_person) == (70000, 50000)


async def test_card_shows_what_will_be_charged_when_the_snapshot_is_empty(session, messenger):
    """Цена, заданная после создания тренировки, попадает и в начисление, и в карточку: ученик видит ту же сумму."""
    t = await done_training(session, price=None, surcharge=None)
    await CatalogService(session).set_type_prices(await ensure_user(session, 99, "admin"), "road", Prices(65000, 40000))
    await set_setting(session, "billing_enabled", "1")
    card = await PublishService(session, messenger, send_delay=0).build_card(t)
    assert (card.single_price, card.surcharge_per_person) == (65000, 40000)
