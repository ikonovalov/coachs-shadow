from datetime import datetime, timezone

from bot.inventory.models import InventoryItem
from bot.schedule.models import Chat, Level, Training, TrainingSeries, TrainingStatus
from bot.users.models import ChatTrainer, User

NOW = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)


async def add_user(session, telegram_id=1, role="member", name=None, started_bot=True) -> User:
    user = User(telegram_id=telegram_id, name=name or f"User {telegram_id}", role=role, started_bot=started_bot)
    session.add(user)
    await session.commit()
    return user


async def add_level(session, name="Новички", position=0) -> Level:
    level = Level(name=name, position=position)
    session.add(level)
    await session.commit()
    return level


async def add_chat(session, level, chat_id=-1001) -> Chat:
    chat = Chat(chat_id=chat_id, title=f"Чат {chat_id}", level_id=level.id)
    session.add(chat)
    await session.commit()
    return chat


async def add_chat_trainer(session, chat, user_id, is_lead=False) -> ChatTrainer:
    row = ChatTrainer(chat_id=chat.chat_id, user_id=user_id, is_lead=is_lead)
    session.add(row)
    await session.commit()
    return row


async def add_training(
    session,
    *,
    trainer,
    levels,
    starts_at,
    capacity=None,
    published_at=None,
    status=TrainingStatus.SCHEDULED,
) -> Training:
    """A one-off series plus its single training, created directly (no jobs)."""
    series = TrainingSeries(
        trainer_id=trainer.telegram_id,
        place="Парк",
        kind="road",
        description="",
        capacity=capacity,
        weekdays="",
        start_date=starts_at.date(),
        start_time=starts_at.time(),
        duration_min=90,
        levels=list(levels),
    )
    session.add(series)
    await session.flush()
    training = Training(
        series_id=series.id,
        starts_at=starts_at,
        duration_min=90,
        trainer_id=trainer.telegram_id,
        place="Парк",
        kind="road",
        description="",
        capacity=capacity,
        status=status,
        published_at=published_at,
        levels=list(levels),
    )
    session.add(training)
    await session.commit()
    return training


async def add_item(session, kind, label, **params) -> InventoryItem:
    item = InventoryItem(kind=kind, label=label, **params)
    session.add(item)
    await session.commit()
    return item
