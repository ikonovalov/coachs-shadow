from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.schedule.models import Chat, Training
from bot.users.models import ChatTrainer


async def trainer_level_ids(session: AsyncSession, user_id: int) -> set[int]:
    rows = await session.scalars(
        select(Chat.level_id)
        .join(ChatTrainer, ChatTrainer.chat_id == Chat.chat_id)
        .where(ChatTrainer.user_id == user_id)
    )
    return set(rows)


async def lead_chat_ids(session: AsyncSession, user_id: int) -> set[int]:
    rows = await session.scalars(
        select(ChatTrainer.chat_id).where(ChatTrainer.user_id == user_id, ChatTrainer.is_lead.is_(True))
    )
    return set(rows)


async def target_chat_ids(session: AsyncSession, training: Training) -> list[int]:
    """Chats that get (or got) the post: chats whose level is among the training's levels."""
    level_ids = [lvl.id for lvl in training.levels]
    if not level_ids:
        return []
    rows = await session.scalars(
        select(Chat.chat_id).where(Chat.level_id.in_(level_ids)).order_by(Chat.chat_id)
    )
    return list(rows)
