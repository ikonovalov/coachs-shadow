import pytest

from bot.core.errors import InvalidAction
from bot.users.profile import parse_cleat, parse_foot, parse_height, parse_helmet, render_profile
from bot.users.service import UserService
from tests.factories import add_user


def test_parse_height():
    assert parse_height(" 175 ") == 175
    assert parse_height("175 см") == 175


@pytest.mark.parametrize("bad", ["17", "1700", "высокий", "", "-170", "175.5"])
def test_parse_height_rejects_garbage(bad):
    with pytest.raises(InvalidAction, match="рост"):
        parse_height(bad)


def test_parse_foot():
    assert parse_foot("42") == 42
    for bad in ("7", "99", "сорок два", ""):
        with pytest.raises(InvalidAction, match="размер"):
            parse_foot(bad)


def test_parse_helmet_accepts_latin_and_cyrillic():
    assert parse_helmet("m") == "M"
    assert parse_helmet("Л") == "L"
    assert parse_helmet("с") == "S"
    for bad in ("XXL", "", "большой"):
        with pytest.raises(InvalidAction, match="S, M или L"):
            parse_helmet(bad)


def test_parse_cleat():
    assert parse_cleat("spd_sl") == "spd_sl"
    assert parse_cleat("NONE") == "none"
    with pytest.raises(InvalidAction):
        parse_cleat("xyz")


async def test_update_profile_saves_only_valid_values(session):
    user = await add_user(session, 1)
    svc = UserService(session)
    await svc.update_profile(user, "height_cm", "180")
    assert user.height_cm == 180
    with pytest.raises(InvalidAction):
        await svc.update_profile(user, "height_cm", "1700")
    assert user.height_cm == 180  # профиль не испорчен
    with pytest.raises(InvalidAction):
        await svc.update_profile(user, "name", "Хакер")


def test_render_profile():
    from bot.users.models import User

    empty = User(telegram_id=1, name="Анна")
    assert "не указан" in render_profile(empty)
    full = User(telegram_id=1, name="Анна", height_cm=170, foot_eu=39, helmet_size="M", cleat_type="spd")
    text = render_profile(full)
    assert "170" in text and "39" in text and "M" in text and "SPD" in text
