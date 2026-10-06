from datetime import timedelta

import pytest

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.users.models import ChatTrainer, User
from bot.users.roles import RoleService
from tests.factories import NOW, add_chat, add_chat_trainer, add_level, add_user


async def admin_and(session, *roles):
    admin = await add_user(session, 99, "admin")
    users = [await add_user(session, 10 + i, r) for i, r in enumerate(roles)]
    return admin, users


async def chat_of(session, chat_id=-1001, name="A"):
    level = await add_level(session, name, abs(chat_id))
    return await add_chat(session, level, chat_id)


async def test_grant_and_revoke_trainer(session):
    admin, (u,) = await admin_and(session, "member")
    svc = RoleService(session)
    await svc.grant_trainer(admin, u.telegram_id)
    assert u.role == "trainer"
    await svc.revoke_trainer(admin, u.telegram_id)
    assert u.role == "member"


async def test_only_admin_changes_roles(session):
    _, (trainer, member) = await admin_and(session, "trainer", "member")
    with pytest.raises(PermissionDenied):
        await RoleService(session).grant_trainer(trainer, member.telegram_id)


async def test_cannot_make_trainer_of_admin_or_unknown_user(session):
    admin, _ = await admin_and(session)
    svc = RoleService(session)
    with pytest.raises(InvalidAction):
        await svc.grant_trainer(admin, admin.telegram_id)
    with pytest.raises(NotFound):
        await svc.grant_trainer(admin, 12345)


async def test_revoke_trainer_removes_chat_memberships(session):
    admin, (t, other) = await admin_and(session, "trainer", "trainer")
    chat = await chat_of(session)
    await add_chat_trainer(session, chat, other.telegram_id, is_lead=True)
    await add_chat_trainer(session, chat, t.telegram_id)
    await RoleService(session).revoke_trainer(admin, t.telegram_id)
    assert await session.get(ChatTrainer, (chat.chat_id, t.telegram_id)) is None


async def test_revoke_trainer_blocked_while_lead_with_colleagues(session):
    admin, (lead, colleague) = await admin_and(session, "trainer", "trainer")
    chat = await chat_of(session)
    await add_chat_trainer(session, chat, lead.telegram_id, is_lead=True)
    await add_chat_trainer(session, chat, colleague.telegram_id)
    with pytest.raises(InvalidAction, match="главного тренера"):
        await RoleService(session).revoke_trainer(admin, lead.telegram_id)
    assert lead.role == "trainer"


async def test_admins_from_env_cannot_be_revoked_others_can(session):
    admin, (other,) = await admin_and(session, "member")
    svc = RoleService(session, protected_admin_ids=[99])
    await svc.grant_admin(admin, other.telegram_id)
    assert other.role == "admin"
    await svc.revoke_admin(admin, other.telegram_id)
    assert other.role == "member"
    with pytest.raises(InvalidAction, match="нельзя снять"):
        await svc.revoke_admin(admin, 99)


async def test_invite_flow(session):
    admin, (member,) = await admin_and(session, "member")
    svc = RoleService(session)
    invite = await svc.create_invite(admin, NOW)
    assert invite.expires_at == NOW + timedelta(hours=24)
    await svc.redeem_invite(invite.token, member, NOW + timedelta(hours=1))
    assert member.role == "trainer"
    other = await add_user(session, 50)
    with pytest.raises(InvalidAction, match="уже использовано"):
        await svc.redeem_invite(invite.token, other, NOW + timedelta(hours=2))


async def test_expired_unknown_invites_and_non_admin_creator(session):
    admin, (member, trainer) = await admin_and(session, "member", "trainer")
    svc = RoleService(session)
    invite = await svc.create_invite(admin, NOW)
    with pytest.raises(InvalidAction, match="истёк"):
        await svc.redeem_invite(invite.token, member, NOW + timedelta(hours=25))
    with pytest.raises(NotFound):
        await svc.redeem_invite("nope", member, NOW)
    with pytest.raises(PermissionDenied):
        await svc.create_invite(trainer, NOW)


async def test_redeeming_does_not_demote_an_admin(session):
    admin, _ = await admin_and(session)
    svc = RoleService(session)
    invite = await svc.create_invite(admin, NOW)
    await svc.redeem_invite(invite.token, admin, NOW)
    assert admin.role == "admin"


async def test_first_chat_trainer_becomes_lead_next_ones_do_not(session):
    admin, (a, b) = await admin_and(session, "trainer", "trainer")
    chat = await chat_of(session)
    svc = RoleService(session)
    first = await svc.add_chat_trainer(admin, chat.chat_id, a.telegram_id)
    second = await svc.add_chat_trainer(admin, chat.chat_id, b.telegram_id)
    assert first.is_lead is True and second.is_lead is False
    with pytest.raises(InvalidAction):
        await svc.add_chat_trainer(admin, chat.chat_id, b.telegram_id)


async def test_only_trainers_can_be_added_to_chats(session):
    admin, (member,) = await admin_and(session, "member")
    chat = await chat_of(session)
    with pytest.raises(InvalidAction, match="тренером"):
        await RoleService(session).add_chat_trainer(admin, chat.chat_id, member.telegram_id)


async def test_lead_manages_own_chat_plain_trainer_and_foreign_lead_do_not(session):
    admin, (lead, plain, newbie, foreign_lead) = await admin_and(session, "trainer", "trainer", "trainer", "trainer")
    chat = await chat_of(session, -1001, "A")
    foreign = await chat_of(session, -1002, "B")
    await add_chat_trainer(session, chat, lead.telegram_id, is_lead=True)
    await add_chat_trainer(session, chat, plain.telegram_id)
    await add_chat_trainer(session, foreign, foreign_lead.telegram_id, is_lead=True)
    svc = RoleService(session)
    await svc.add_chat_trainer(lead, chat.chat_id, newbie.telegram_id)
    await svc.remove_chat_trainer(lead, chat.chat_id, newbie.telegram_id)
    with pytest.raises(PermissionDenied):
        await svc.add_chat_trainer(plain, chat.chat_id, newbie.telegram_id)
    with pytest.raises(PermissionDenied):
        await svc.add_chat_trainer(foreign_lead, chat.chat_id, newbie.telegram_id)


async def test_lead_cannot_be_removed_while_colleagues_exist_but_set_lead_moves_the_flag(session):
    admin, (lead, colleague) = await admin_and(session, "trainer", "trainer")
    chat = await chat_of(session)
    await add_chat_trainer(session, chat, lead.telegram_id, is_lead=True)
    await add_chat_trainer(session, chat, colleague.telegram_id)
    svc = RoleService(session)
    with pytest.raises(InvalidAction, match="главного"):
        await svc.remove_chat_trainer(admin, chat.chat_id, lead.telegram_id)
    with pytest.raises(PermissionDenied):
        await svc.set_lead(lead, chat.chat_id, colleague.telegram_id)  # назначает только админ
    await svc.set_lead(admin, chat.chat_id, colleague.telegram_id)
    pairs = {u.telegram_id: is_lead for u, is_lead in await svc.chat_trainers(chat.chat_id)}
    assert pairs == {lead.telegram_id: False, colleague.telegram_id: True}
    await svc.remove_chat_trainer(admin, chat.chat_id, lead.telegram_id)  # теперь можно


async def test_sole_lead_can_be_removed(session):
    admin, (lead,) = await admin_and(session, "trainer")
    chat = await chat_of(session)
    await add_chat_trainer(session, chat, lead.telegram_id, is_lead=True)
    await RoleService(session).remove_chat_trainer(admin, chat.chat_id, lead.telegram_id)
    assert await RoleService(session).chat_trainers(chat.chat_id) == []


async def test_manageable_chats(session):
    admin, (lead, plain) = await admin_and(session, "trainer", "trainer")
    a = await chat_of(session, -1001, "A")
    b = await chat_of(session, -1002, "B")
    await add_chat_trainer(session, a, lead.telegram_id, is_lead=True)
    await add_chat_trainer(session, b, lead.telegram_id)
    await add_chat_trainer(session, a, plain.telegram_id)
    svc = RoleService(session)
    assert {c.chat_id for c in await svc.manageable_chats(admin)} == {-1001, -1002}
    assert {c.chat_id for c in await svc.manageable_chats(lead)} == {-1001}
    assert await svc.manageable_chats(plain) == []
