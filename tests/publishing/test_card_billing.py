from bot.core.settings import set_setting
from bot.publishing.service import PublishService
from tests.billing.helpers import done_training


async def test_card_shows_prices_only_when_billing_is_on(session, messenger):
    t = await done_training(session, price=70000, surcharge=50000)
    svc = PublishService(session, messenger, send_delay=0)
    off = await svc.build_card(t)
    assert (off.single_price, off.surcharge_per_person) == (None, None)
    await set_setting(session, "billing_enabled", "1")
    on = await svc.build_card(t)
    assert (on.single_price, on.surcharge_per_person) == (70000, 50000)
