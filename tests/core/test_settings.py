import pytest

from bot.core.settings import get_setting, set_setting, set_setting_if_absent


async def test_default_is_returned_when_unset(session):
    assert await get_setting(session, "publish_window_h") == "24"


async def test_set_overrides_default(session):
    await set_setting(session, "publish_window_h", "12")
    assert await get_setting(session, "publish_window_h") == "12"


async def test_set_if_absent_does_not_overwrite(session):
    await set_setting(session, "club_timezone", "Asia/Yekaterinburg")
    await set_setting_if_absent(session, "club_timezone", "Europe/Moscow")
    assert await get_setting(session, "club_timezone") == "Asia/Yekaterinburg"


async def test_unknown_key_rejected(session):
    with pytest.raises(KeyError):
        await set_setting(session, "nope", "1")
