from typing import Protocol

from bot.core.errors import PermissionDenied


class Role:
    ADMIN = "admin"
    TRAINER = "trainer"
    MEMBER = "member"


class Actor(Protocol):
    telegram_id: int
    role: str


def require_admin(actor: Actor) -> None:
    if actor.role != Role.ADMIN:
        raise PermissionDenied("Только для администратора")


def can_manage_training(
    actor: Actor, trainer_id: int, target_chat_ids: set[int], lead_chat_ids: set[int]
) -> bool:
    """Admin: always. Trainer: own trainings. Lead trainer: a foreign training only if
    he leads *every* chat the training targets."""
    if actor.role == Role.ADMIN:
        return True
    if actor.role != Role.TRAINER:
        return False
    if trainer_id == actor.telegram_id:
        return True
    return bool(target_chat_ids) and target_chat_ids <= lead_chat_ids


def can_create_for_levels(actor: Actor, level_ids: set[int], own_level_ids: set[int]) -> bool:
    if actor.role == Role.ADMIN:
        return True
    if actor.role != Role.TRAINER:
        return False
    return bool(level_ids) and level_ids <= own_level_ids
