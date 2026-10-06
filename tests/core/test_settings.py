import pytest

from bot.core.errors import InvalidAction
from bot.core.settings import get_setting, set_setting, set_setting_if_absent, validate_setting


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


def test_validate_setting():
    assert validate_setting("publish_window_h", " 12 ") == "12"
    assert validate_setting("club_timezone", "Europe/Berlin") == "Europe/Berlin"
    assert validate_setting("inventory_deadline_h", "") == ""
    for key, bad in (("publish_window_h", "0"), ("reminder_h", "два"), ("club_timezone", "Mars/Base"), ("duration_min", "-5")):
        with pytest.raises(InvalidAction):
            validate_setting(key, bad)
    with pytest.raises(KeyError):
        validate_setting("nope", "1")
