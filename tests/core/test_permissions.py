from types import SimpleNamespace

import pytest

from bot.core.errors import PermissionDenied
from bot.core.permissions import Role, can_create_for_levels, can_manage_training, require_admin


def actor(uid, role):
    return SimpleNamespace(telegram_id=uid, role=role)


def test_admin_can_manage_anything():
    assert can_manage_training(actor(1, Role.ADMIN), trainer_id=5, target_chat_ids={1}, lead_chat_ids=set())


def test_member_can_manage_nothing():
    assert not can_manage_training(actor(1, Role.MEMBER), trainer_id=1, target_chat_ids={1}, lead_chat_ids={1})


def test_trainer_manages_own_training():
    assert can_manage_training(actor(1, Role.TRAINER), trainer_id=1, target_chat_ids={1}, lead_chat_ids=set())


def test_lead_manages_foreign_training_only_if_leading_all_target_chats():
    lead = actor(2, Role.TRAINER)
    assert can_manage_training(lead, trainer_id=1, target_chat_ids={10}, lead_chat_ids={10, 11})
    assert not can_manage_training(lead, trainer_id=1, target_chat_ids={10, 12}, lead_chat_ids={10, 11})


def test_foreign_training_without_target_chats_is_not_manageable_by_lead():
    assert not can_manage_training(actor(2, Role.TRAINER), trainer_id=1, target_chat_ids=set(), lead_chat_ids={10})


def test_create_for_levels():
    assert can_create_for_levels(actor(1, Role.ADMIN), {1, 2}, set())
    assert can_create_for_levels(actor(1, Role.TRAINER), {1}, {1, 2})
    assert not can_create_for_levels(actor(1, Role.TRAINER), {1, 3}, {1, 2})
    assert not can_create_for_levels(actor(1, Role.TRAINER), set(), {1, 2})
    assert not can_create_for_levels(actor(1, Role.MEMBER), {1}, {1})


def test_require_admin():
    require_admin(actor(1, Role.ADMIN))
    with pytest.raises(PermissionDenied):
        require_admin(actor(1, Role.TRAINER))
