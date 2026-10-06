import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import ChatUnavailable, Keyboard, Messenger
from bot.core.permissions import Role
from bot.core.settings import get_setting
from bot.participation.models import Participation, ParticipationStatus
from bot.publishing.models import TrainingPost
from bot.publishing.render import KIND_NAMES, CardData, format_when, render_card
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.queries import target_chat_ids
from bot.users.models import User

log = logging.getLogger(__name__)


class PublishService:
    def __init__(self, session: AsyncSession, messenger: Messenger, *, send_delay: float = 0.05) -> None:
        self.session = session
        self.messenger = messenger
        self.send_delay = send_delay  # gentle pacing against Telegram rate limits

    async def _tz(self) -> ZoneInfo:
        return ZoneInfo(await get_setting(self.session, "club_timezone"))

    async def build_card(self, training: Training, published: bool | None = None) -> CardData:
        trainer = await self.session.get(User, training.trainer_id)
        going = await self.session.scalar(
            select(func.count())
            .select_from(Participation)
            .where(Participation.training_id == training.id, Participation.status == ParticipationStatus.GOING)
        )
        publish_h = int(await get_setting(self.session, "publish_window_h"))
        return CardData(
            training_id=training.id,
            status=training.status,
            starts_at=training.starts_at,
            duration_min=training.duration_min,
            kind=training.kind,
            place=training.place,
            trainer_name=trainer.name if trainer else "—",
            level_names=[lvl.name for lvl in sorted(training.levels, key=lambda lv: lv.position)],
            capacity=training.capacity,
            going_count=going,
            description=training.description,
            published=training.published_at is not None if published is None else published,
            opens_at=training.starts_at - timedelta(hours=publish_h),
        )

    async def render(self, training: Training, published: bool | None = None) -> tuple[str, Keyboard]:
        return render_card(await self.build_card(training, published), await self._tz())

    async def publish(self, training_id: int, now: datetime) -> None:
        training = await self.session.get(Training, training_id)
        if (
            training is None
            or training.status != TrainingStatus.SCHEDULED
            or training.published_at is not None
            or training.starts_at <= now
        ):
            return
        chat_ids = await target_chat_ids(self.session, training)
        existing = set(
            await self.session.scalars(select(TrainingPost.chat_id).where(TrainingPost.training_id == training_id))
        )
        text, keyboard = await self.render(training, published=True)
        failed: list[int] = []
        for chat_id in chat_ids:
            if chat_id in existing:
                continue
            try:
                message_id = await self.messenger.send(chat_id, text, keyboard)
            except ChatUnavailable:
                log.warning("cannot publish training %s to chat %s", training_id, chat_id)
                failed.append(chat_id)
                continue
            self.session.add(TrainingPost(training_id=training_id, chat_id=chat_id, message_id=message_id))
            await self.session.commit()  # per chat: a crash later resumes without duplicates
            await asyncio.sleep(self.send_delay)
        training.published_at = now
        await self.session.commit()
        log.info("published training %s: %d chat(s), %d failed", training_id, len(chat_ids), len(failed))
        if not chat_ids:
            await self._warn_admins(
                f"Тренировка {self._title(training, await self._tz())} опубликована, но подходящих чатов нет. "
                "Запись доступна через /schedule в личке."
            )
        if failed:
            await self._warn_admins(
                f"Не удалось опубликовать тренировку в чатах: {', '.join(str(c) for c in failed)}. "
                "Проверьте, что бот в чате и может писать."
            )

    async def refresh_posts(self, training_id: int) -> None:
        training = await self.session.get(Training, training_id)
        if training is None:
            return
        posts = list(await self.session.scalars(select(TrainingPost).where(TrainingPost.training_id == training_id)))
        if not posts:
            return
        text, keyboard = await self.render(training)
        for post in posts:
            try:
                new_id = await self.messenger.edit_post(post.chat_id, post.message_id, text, keyboard)
            except ChatUnavailable:
                log.warning("cannot update post of training %s in chat %s", training_id, post.chat_id)
                await self._warn_admins(f"Не удалось обновить пост тренировки в чате {post.chat_id}.")
                continue
            if new_id != post.message_id:
                post.message_id = new_id
            await asyncio.sleep(self.send_delay)
        await self.session.commit()

    async def close(self, training_id: int, now: datetime) -> None:
        training = await self.session.get(Training, training_id)
        if training is None or training.status != TrainingStatus.SCHEDULED or training.starts_at > now:
            return
        training.status = TrainingStatus.DONE
        log.info("closed training %s", training_id)
        await self.session.commit()
        await self.refresh_posts(training_id)

    async def send_reminders(self, training_id: int, now: datetime) -> None:
        training = await self.session.get(Training, training_id)
        if training is None or training.status != TrainingStatus.SCHEDULED or training.starts_at <= now:
            return
        tz = await self._tz()
        text = f"⏰ Скоро тренировка: {self._title(training, tz)}, {training.place}"
        for user_id in await self._going_ids(training_id):
            if not await self.messenger.notify(user_id, text):
                log.info("reminder not delivered to %s (bot not started?)", user_id)

    async def announce_cancelled(self, training_id: int) -> None:
        training = await self.session.get(Training, training_id)
        if training is None:
            return
        await self.refresh_posts(training_id)
        text = f"❌ Тренировка отменена: {self._title(training, await self._tz())}"
        for user_id in await self._going_ids(training_id):
            if not await self.messenger.notify(user_id, text):
                log.info("cancel notice not delivered to %s (bot not started?)", user_id)

    # ---- helpers ----

    @staticmethod
    def _title(training: Training, tz: ZoneInfo) -> str:
        kind = KIND_NAMES.get(training.kind, training.kind)
        return f"{kind}, {format_when(training.starts_at, training.duration_min, tz)}"

    async def _going_ids(self, training_id: int) -> list[int]:
        rows = await self.session.scalars(
            select(Participation.user_id).where(
                Participation.training_id == training_id, Participation.status == ParticipationStatus.GOING
            )
        )
        return list(rows)

    async def _warn_admins(self, text: str) -> None:
        admin_ids = await self.session.scalars(select(User.telegram_id).where(User.role == Role.ADMIN))
        for admin_id in list(admin_ids):
            await self.messenger.notify(admin_id, f"⚠️ {text}")
