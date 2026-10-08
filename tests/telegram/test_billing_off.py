from bot.telegram.handlers import billing_off
from tests.telegram.fakes import FakeCallback, FakeMessage


async def test_stale_billing_buttons_get_a_notice():
    for data in ("pay:menu", "att:o:1", "pay:cp:3"):
        cb = FakeCallback(data)
        await billing_off.on_callback(cb)
        assert cb.answers == [("Оплата отключена", True)]


async def test_payments_command_says_it_is_off():
    msg = FakeMessage()
    await billing_off.cmd_payments(msg)
    assert msg.answers[0][0] == "Оплата отключена"
