from bot.bootstrap import bootstrap
from bot.core.config import Config
from bot.core.settings import billing_enabled
from tests.factories import NOW


def test_billing_is_off_by_default(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.delenv("BILLING_ENABLED", raising=False)
    assert Config(_env_file=None).billing_enabled is False


def test_billing_flag_is_read_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.setenv("BILLING_ENABLED", "true")
    assert Config(_env_file=None).billing_enabled is True


async def test_setting_is_off_without_bootstrap(session):
    assert await billing_enabled(session) is False


async def test_bootstrap_mirrors_the_flag_on_every_start(session_factory):
    on = Config(bot_token="t", billing_enabled=True, _env_file=None)
    off = Config(bot_token="t", billing_enabled=False, _env_file=None)
    await bootstrap(session_factory, on, NOW)
    async with session_factory() as s:
        assert await billing_enabled(s) is True
    await bootstrap(session_factory, off, NOW)  # перезапуск с выключенным флагом
    async with session_factory() as s:
        assert await billing_enabled(s) is False
