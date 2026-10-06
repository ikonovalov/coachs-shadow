from bot.users.models import User
from bot.users.service import UserService


async def test_new_user_is_member_and_private_chat_marks_started(session):
    user = await UserService(session).upsert_from_telegram(5, "Анна", is_private=True, admin_ids=[])
    assert (user.role, user.started_bot, user.name) == ("member", True, "Анна")


async def test_group_interaction_does_not_mark_started(session):
    user = await UserService(session).upsert_from_telegram(5, "Анна", is_private=False, admin_ids=[])
    assert user.started_bot is False


async def test_started_flag_is_sticky_and_name_updates(session):
    svc = UserService(session)
    await svc.upsert_from_telegram(5, "Анна", is_private=True, admin_ids=[])
    user = await svc.upsert_from_telegram(5, "Анна К.", is_private=False, admin_ids=[])
    assert user.started_bot is True and user.name == "Анна К."


async def test_configured_admin_gets_admin_role(session):
    user = await UserService(session).upsert_from_telegram(7, "Игорь", is_private=True, admin_ids=[7])
    assert user.role == "admin"


async def test_sync_admins_creates_and_promotes(session):
    svc = UserService(session)
    await svc.upsert_from_telegram(8, "Борис", is_private=True, admin_ids=[])
    await svc.sync_admins([8, 9])
    assert (await session.get(User, 8)).role == "admin"
    assert (await session.get(User, 9)).role == "admin"


async def test_search_is_case_insensitive_for_cyrillic_and_filters_roles(session):
    from tests.factories import add_user

    await add_user(session, 1, "member", name="Анна Петрова")
    await add_user(session, 2, "trainer", name="Олег Анненков")
    await add_user(session, 3, "member", name="Борис")
    svc = UserService(session)
    assert [u.telegram_id for u in await svc.search("АННА")] == [1]
    assert {u.telegram_id for u in await svc.search("ан")} == {1, 2}
    assert [u.telegram_id for u in await svc.search("ан", roles={"trainer"})] == [2]
    assert await svc.search("zzz") == []
    assert len(await svc.search("", limit=2)) == 2
