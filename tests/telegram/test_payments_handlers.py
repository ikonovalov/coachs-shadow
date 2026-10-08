from bot.billing.service import BillingService
from bot.telegram.handlers import payments
from bot.users.models import User
from tests.billing.helpers import done_training, give_subscription
from tests.factories import NOW
from tests.telegram.fakes import FakeMessage, callback_data


async def test_payments_command_shows_subscriptions_and_debts(session):
    t = await done_training(session, going=[1])
    await give_subscription(session, 1, [t.series_id], name="Утро", sessions=8)
    user = await session.get(User, 1)
    msg = FakeMessage()
    await payments.cmd_payments(msg, session, user)
    text, markup = msg.answers[0]
    assert "«Утро» — осталось 8 из 8, бессрочный" in text
    assert "Задолженности нет" in text
    assert callback_data(markup) == ["menu:back"]


async def test_payments_shows_debt_after_close(session):
    t = await done_training(session, going=[1])
    await BillingService(session).charge_training(t.id, NOW)
    user = await session.get(User, 1)
    msg = FakeMessage()
    await payments.send_payments(msg, session, user)
    assert "Итого: 700 ₽" in msg.answers[0][0]
