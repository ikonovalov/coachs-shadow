from datetime import timedelta

from bot.users.access import AccessService
from tests.factories import NOW, add_chat, add_level
from tests.fakes import FakeMembership


async def with_chats(session, *chat_ids):
    level = await add_level(session)
    for cid in chat_ids:
        await add_chat(session, level, cid)


def svc(session, checker, cache=None, admin_ids=()):
    return AccessService(session, checker, list(admin_ids), cache if cache is not None else {})


async def test_member_of_any_bound_chat_is_allowed(session):
    await with_chats(session, -1, -2)
    checker = FakeMembership({(-2, 5)})
    assert await svc(session, checker).can_use(5, NOW) is True
    assert await svc(session, checker).can_use(6, NOW) is False


async def test_env_admins_skip_the_check(session):
    await with_chats(session, -1)
    checker = FakeMembership()
    assert await svc(session, checker, admin_ids=[7]).can_use(7, NOW) is True
    assert checker.calls == 0


async def test_no_bound_chats_means_no_access_for_ordinary_users(session):
    assert await svc(session, FakeMembership({(-1, 5)})).can_use(5, NOW) is False


async def test_result_is_cached_for_ttl_then_rechecked(session):
    await with_chats(session, -1)
    checker = FakeMembership({(-1, 5)})
    cache: dict = {}
    await svc(session, checker, cache).can_use(5, NOW)
    await svc(session, checker, cache).can_use(5, NOW + timedelta(minutes=4))
    assert checker.calls == 1
    checker.members.clear()  # человека удалили из чата
    assert await svc(session, checker, cache).can_use(5, NOW + timedelta(minutes=6)) is False
    assert checker.calls == 2


async def test_telegram_error_in_one_chat_does_not_break_the_check(session):
    await with_chats(session, -1, -2)
    checker = FakeMembership({(-2, 5)})
    checker.failing_chats.add(-1)
    assert await svc(session, checker).can_use(5, NOW) is True
    checker.failing_chats.add(-2)
    assert await svc(session, checker).can_use(6, NOW) is False


async def test_mark_member_in_bound_chat_is_proof_without_checking(session):
    await with_chats(session, -1)
    checker = FakeMembership()
    cache: dict = {}
    assert await svc(session, checker, cache).mark_member(-1, 9, NOW) is True
    assert await svc(session, checker, cache).can_use(9, NOW) is True
    assert checker.calls == 0


async def test_mark_member_in_unbound_chat_grants_nothing(session):
    await with_chats(session, -1)
    checker = FakeMembership()
    cache: dict = {}
    assert await svc(session, checker, cache).mark_member(-999, 9, NOW) is False
    assert cache == {}
    assert await svc(session, checker, cache).can_use(9, NOW) is False
