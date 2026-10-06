import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.permissions import Role, require_admin
from bot.schedule.models import Chat
from bot.users.models import ChatTrainer, Invite, User

INVITE_TTL = timedelta(hours=24)


class RoleService:
    def __init__(self, session: AsyncSession, protected_admin_ids: list[int] | None = None) -> None:
        self.session = session
        self.protected_admin_ids = protected_admin_ids or []

    async def _user(self, user_id: int) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise NotFound("Пользователь не найден")
        return user

    async def list_by_role(self, role: str) -> list[User]:
        rows = await self.session.scalars(select(User).where(User.role == role).order_by(User.name))
        return list(rows)

    # ---- роли ----

    async def grant_trainer(self, actor: User, user_id: int) -> User:
        require_admin(actor)
        user = await self._user(user_id)
        if user.role == Role.ADMIN:
            raise InvalidAction("Администратор уже имеет права тренера")
        user.role = Role.TRAINER
        await self.session.commit()
        return user

    async def revoke_trainer(self, actor: User, user_id: int) -> User:
        require_admin(actor)
        user = await self._user(user_id)
        if user.role != Role.TRAINER:
            raise InvalidAction("Пользователь не тренер")
        blocked: list[int] = []
        lead_chats = await self.session.scalars(
            select(ChatTrainer.chat_id).where(ChatTrainer.user_id == user_id, ChatTrainer.is_lead.is_(True))
        )
        for chat_id in list(lead_chats):
            others = await self.session.scalar(
                select(func.count()).select_from(ChatTrainer).where(
                    ChatTrainer.chat_id == chat_id, ChatTrainer.user_id != user_id
                )
            )
            if others:
                blocked.append(chat_id)
        if blocked:
            titles = await self.session.scalars(select(Chat.title).where(Chat.chat_id.in_(blocked)))
            raise InvalidAction(
                "Сначала назначьте нового главного тренера в чатах: " + ", ".join(titles)
            )
        await self.session.execute(delete(ChatTrainer).where(ChatTrainer.user_id == user_id))
        user.role = Role.MEMBER
        await self.session.commit()
        return user

    async def grant_admin(self, actor: User, user_id: int) -> User:
        require_admin(actor)
        user = await self._user(user_id)
        user.role = Role.ADMIN
        await self.session.commit()
        return user

    async def revoke_admin(self, actor: User, user_id: int) -> User:
        require_admin(actor)
        if user_id in self.protected_admin_ids:
            raise InvalidAction("Администратора из настроек (.env) нельзя снять через бота")
        user = await self._user(user_id)
        if user.role != Role.ADMIN:
            raise InvalidAction("Пользователь не администратор")
        user.role = Role.MEMBER
        await self.session.commit()
        return user

    # ---- приглашения ----

    async def create_invite(self, actor: User, now: datetime) -> Invite:
        require_admin(actor)
        invite = Invite(
            token=secrets.token_urlsafe(16),
            role=Role.TRAINER,
            created_by=actor.telegram_id,
            created_at=now,
            expires_at=now + INVITE_TTL,
        )
        self.session.add(invite)
        await self.session.commit()
        return invite

    async def redeem_invite(self, token: str, user: User, now: datetime) -> User:
        invite = await self.session.get(Invite, token)
        if invite is None:
            raise NotFound("Приглашение не найдено")
        if invite.used_by is not None:
            raise InvalidAction("Приглашение уже использовано")
        if invite.expires_at <= now:
            raise InvalidAction("Срок приглашения истёк")
        if user.role == Role.MEMBER:
            user.role = invite.role
        invite.used_by = user.telegram_id
        invite.used_at = now
        await self.session.commit()
        return user

    # ---- тренеры чатов ----

    async def manageable_chats(self, actor: User) -> list[Chat]:
        query = select(Chat).order_by(Chat.title)
        if actor.role != Role.ADMIN:
            query = query.join(ChatTrainer, ChatTrainer.chat_id == Chat.chat_id).where(
                ChatTrainer.user_id == actor.telegram_id, ChatTrainer.is_lead.is_(True)
            )
        return list(await self.session.scalars(query))

    async def chat_trainers(self, chat_id: int) -> list[tuple[User, bool]]:
        rows = await self.session.execute(
            select(User, ChatTrainer.is_lead)
            .join(ChatTrainer, ChatTrainer.user_id == User.telegram_id)
            .where(ChatTrainer.chat_id == chat_id)
            .order_by(ChatTrainer.is_lead.desc(), User.name)
        )
        return [(user, bool(is_lead)) for user, is_lead in rows]

    async def _require_chat_manager(self, actor: User, chat_id: int) -> None:
        if actor.role == Role.ADMIN:
            return
        row = await self.session.get(ChatTrainer, (chat_id, actor.telegram_id))
        if row is None or not row.is_lead:
            raise PermissionDenied("Только администратор или главный тренер чата")

    async def _trainer_count(self, chat_id: int) -> int:
        return await self.session.scalar(
            select(func.count()).select_from(ChatTrainer).where(ChatTrainer.chat_id == chat_id)
        )

    async def add_chat_trainer(self, actor: User, chat_id: int, user_id: int) -> ChatTrainer:
        if await self.session.get(Chat, chat_id) is None:
            raise NotFound("Чат не найден")
        await self._require_chat_manager(actor, chat_id)
        target = await self._user(user_id)
        if target.role != Role.TRAINER:
            raise InvalidAction("Сначала назначьте пользователя тренером")
        if await self.session.get(ChatTrainer, (chat_id, user_id)) is not None:
            raise InvalidAction("Уже тренер этого чата")
        row = ChatTrainer(chat_id=chat_id, user_id=user_id, is_lead=await self._trainer_count(chat_id) == 0)
        self.session.add(row)
        await self.session.commit()
        return row

    async def remove_chat_trainer(self, actor: User, chat_id: int, user_id: int) -> None:
        await self._require_chat_manager(actor, chat_id)
        row = await self.session.get(ChatTrainer, (chat_id, user_id))
        if row is None:
            raise NotFound("Он не тренер этого чата")
        if row.is_lead and await self._trainer_count(chat_id) > 1:
            raise InvalidAction("Сначала назначьте другого главного тренера")
        await self.session.delete(row)
        await self.session.commit()

    async def set_lead(self, actor: User, chat_id: int, user_id: int) -> None:
        require_admin(actor)
        row = await self.session.get(ChatTrainer, (chat_id, user_id))
        if row is None:
            raise NotFound("Он не тренер этого чата")
        if row.is_lead:
            return
        current = await self.session.scalar(
            select(ChatTrainer).where(ChatTrainer.chat_id == chat_id, ChatTrainer.is_lead.is_(True))
        )
        if current is not None:
            current.is_lead = False
            await self.session.flush()  # частичный уникальный индекс: двух главных одновременно быть не может
        row.is_lead = True
        await self.session.commit()
