from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction
from bot.core.permissions import Role
from bot.users.models import User
from bot.users.profile import PROFILE_FIELDS


class UserService:
    """Пользователи: создание и обновление по данным Telegram, профиль, роли админов, поиск."""
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_from_telegram(
        self, telegram_id: int, name: str, *, is_private: bool, admin_ids: list[int]
    ) -> User:
        """Создаёт пользователя или обновляет имя; сообщение в личку отмечает started_bot. Id из `admin_ids`
        получают роль ADMIN (понижения нет). Возвращает пользователя."""
        user = await self.session.get(User, telegram_id)
        if user is None:
            user = User(
                telegram_id=telegram_id,
                name=name,
                role=Role.ADMIN if telegram_id in admin_ids else Role.MEMBER,
                started_bot=is_private,
            )
            self.session.add(user)
        else:
            user.name = name
            if is_private:
                user.started_bot = True
            if telegram_id in admin_ids:
                user.role = Role.ADMIN
        await self.session.commit()
        return user

    async def update_profile(self, user: User, field: str, raw: str) -> User:
        """Меняет одно поле профиля из пользовательского текста. Неизвестное поле — InvalidAction;
        ошибка разбора значения отклоняет изменение, профиль остаётся прежним."""
        parser = PROFILE_FIELDS.get(field)
        if parser is None:
            raise InvalidAction("Это поле профиля нельзя изменить")
        setattr(user, field, parser(raw))  # сначала разбор: при ошибке профиль не меняется
        await self.session.commit()
        return user

    async def sync_admins(self, admin_ids: list[int]) -> None:
        """Гарантирует роль ADMIN у id из настроек: создаёт недостающих пользователей и повышает существующих."""
        for telegram_id in admin_ids:
            user = await self.session.get(User, telegram_id)
            if user is None:
                self.session.add(User(telegram_id=telegram_id, name="Админ", role=Role.ADMIN))
            elif user.role != Role.ADMIN:
                user.role = Role.ADMIN
        await self.session.commit()

    async def search(self, query: str, limit: int = 8, roles: set[str] | None = None) -> list[User]:
        """Поиск по подстроке имени без учёта регистра: не более `limit` результатов,
        при `roles` — только пользователи этих ролей."""
        needle = query.strip().casefold()
        rows = await self.session.scalars(select(User).order_by(User.name))
        found: list[User] = []
        for user in rows:
            if roles is not None and user.role not in roles:
                continue
            if needle in user.name.casefold():
                found.append(user)
                if len(found) >= limit:
                    break
        return found
