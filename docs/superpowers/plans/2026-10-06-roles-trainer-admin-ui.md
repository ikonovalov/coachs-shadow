# Coach's Shadow — План 2: роли, доступ, интерфейсы тренера и админа

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Цель:** Сделать бота пригодным для реальной работы клуба: роли и приглашения, тренеры чатов с главным тренером, проверка членства в клубе, создание и правка тренировок и серий через Telegram, отмена нескольких тренировок, ручная запись участников, админ-меню.

**Архитектура:** Расширяем сервисы плана 1 (`ScheduleService`, `ParticipationService`, `PublishService`) и добавляем `RoleService` и `AccessService`. Сценарии на стыке фич остаются в `bot/usecases.py`. Весь Telegram-код (FSM-формы, меню, выбор пользователя по имени) живёт в `bot/telegram/`; разбор пользовательского ввода вынесен в чистые функции `bot/schedule/forms.py` и тестируется без aiogram.

**Tech Stack:** как в плане 1 (Python 3.11+, aiogram 3, SQLAlchemy 2 async, Alembic, pytest).

**Spec:** `docs/superpowers/specs/2026-10-06-coachs-shadow-design.md`
**Предыдущий план:** `docs/superpowers/plans/2026-10-06-engine-schedule-signup.md` (должен быть выполнен полностью; этот план ссылается на его имена и код).

## Место в общей картине

| План | Содержание |
|---|---|
| 1 (выполнен) | Движок: модель, генерация, публикация, запись, напоминания, отмена на уровне сервисов |
| **2 (этот)** | Роли, приглашения, тренеры чатов, доступ по членству, UI `/trainer` и `/admin`, правка серии с перепубликацией, ручная запись |
| 3 | Инвентарь, `/profile`, `/my` |

## Global Constraints

- Язык интерфейса: русский (все тексты для пользователя).
- Права проверяют сами сервисы внутри операции (политики в `core/permissions.py`), хендлер их не обходит.
- Фичи не импортируют aiogram и `telegram/`; разбор ввода (`schedule/forms.py`) без aiogram.
- Первые админы задаются `ADMIN_IDS` в `.env`; их нельзя снять через бота. Админов назначает только админ, выбором из списка.
- Приглашение: одноразовая ссылка `t.me/bot?start=trainer_<токен>`, срок 24 ч.
- Главный тренер чата: один на чат (частичный уникальный индекс), обязателен, если у чата есть тренеры; назначает админ; главный добавляет и убирает остальных тренеров своего чата; снять главного нельзя без назначения нового.
- Тренер создаёт тренировки только для уровней своих чатов; правит и отменяет свои; главный тренер — чужие, только если он главный во **всех** чатах, куда тренировка нацелена.
- Правка серии: неопубликованные будущие тренировки пересоздаются без уведомлений; опубликованные явно отменяются (пост «Отменена», уведомление «Тренировка изменена, запишитесь на обновлённую» со ссылкой на новый пост), вместо них публикуется новая; начатые и прошедшие не меняются. Правка одной тренировки не затрагивает серию.
- Ручная запись тренером игнорирует лимит: бот предупреждает «Лимит превышен», просит подтверждения, пишет `added_by`, уведомляет записанного (если он запускал бота).
- Доступ: пользоваться ботом может только участник хотя бы одного привязанного чата (проверка `getChatMember`, результат кэшируется на короткое время) — при `/start`, записи и заказах через личку. Админы из `ADMIN_IDS` проверку не проходят. Нажатие кнопки в посте внутри привязанного чата считается доказательством членства. Остальным: «Бот доступен участникам клуба».
- Необратимое действие требует подтверждения; никаких лишних сообщений в группе.
- Не входит: инвентарь, `/profile`, `/my` (план 3); редактирование уровней серии (меняются пересозданием серии).

## Отступления от спецификации (мелкие)

- `Training.cancel_reason` (`user` / `series_edit`) и замена уникального ограничения `(series_id, starts_at)` на частичный уникальный индекс: иначе заменяющая тренировка с тем же временем (правка только места) не может быть создана рядом с отменённой правкой. Отменённые пользователем тренировки по-прежнему блокируют повторную генерацию.
- Правка серии из Telegram меняет поля: тип, место, описание, лимит, дни недели, время, дату окончания. Уровни — через новую серию.
- FSM-состояния хранятся в памяти (`MemoryStorage`): после перезапуска бота незавершённая форма сбрасывается.
- Новая тренировка при правке серии сопоставляется со старой опубликованной **по дате** (в часовом поясе клуба); если пары нет (день недели сменился), участники получают обычное уведомление об отмене.

## Review Focus

1. **Правка только места у опубликованной тренировки** — замена имеет тот же `starts_at`; индекс не должен ей мешать (Задачи 1, 3).
2. **Правка серии не воскрешает тренировку, отменённую пользователем** (Задача 3).
3. **Смена дня недели в серии** — старая опубликованная тренировка без пары отменяется обычным уведомлением, а не ссылкой в никуда (Задачи 3, 4).
4. **Лимит ниже числа записавшихся при правке тренировки** — отказ с понятным текстом, данные не меняются (Задача 4).
5. **Кривой ввод в формах** (`31.02`, `25:00`, «вторник и пятница через раз», пустое место) — вежливая ошибка, состояние формы сохраняется, повтор возможен (Задача 6).

---

## Структура файлов (новое и изменяемое)

```
bot/
  core/ messenger.py(+MembershipChecker) settings.py(+validate_setting)
  users/ models.py(+Invite) service.py(+search) roles.py(новый) access.py(новый)
  schedule/ models.py(+cancel_reason, индекс) service.py(+правка) forms.py(новый)
  jobs/ queue.py(+delete_for_training)
  participation/ service.py(+manual_*)
  publishing/ service.py(+announce_replaced, post_link, notify_manual_change)
  usecases.py (+4 сценария)
  telegram/ states.py ui.py middleware.py(+AccessMiddleware) membership.py
            handlers/ common.py(deep link) picker.py trainer.py trainer_forms.py chats.py admin.py(расширен)
  main.py (обновлён)
alembic/versions/<rev>_roles_invites_cancel_reason.py
tests/ (зеркалит; fakes.py + FakeMembership)
```

---

### Task 1: Схема — приглашения, причина отмены, частичный индекс

**Files:**
- Modify: `bot/users/models.py`, `bot/schedule/models.py`, `bot/schedule/service.py` (метод `cancel`)
- Create: `alembic/versions/<rev>_roles_invites_cancel_reason.py`
- Test: `tests/schedule/test_schema_delta.py`, `tests/test_migrations.py` (дополнить)

**Interfaces:**
- Produces: `Invite(token PK, role, created_by, created_at, expires_at, used_by, used_at)`; `CancelReason.USER = "user"`, `CancelReason.SERIES_EDIT = "series_edit"`; `Training.cancel_reason: str | None`; уникальный индекс `uq_training_series_start` по `(series_id, starts_at)` **только** для строк, где `COALESCE(cancel_reason, '') <> 'series_edit'`. `ScheduleService.cancel` теперь ставит `cancel_reason = "user"`.

- [ ] **Step 1: Написать падающие тесты**

`tests/schedule/test_schema_delta.py`:
```python
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from bot.schedule.models import CancelReason, Training, TrainingStatus
from bot.schedule.service import ScheduleService
from tests.factories import NOW, add_level, add_training, add_user


def clone(t: Training) -> Training:
    return Training(
        series_id=t.series_id, starts_at=t.starts_at, duration_min=t.duration_min, trainer_id=t.trainer_id,
        place=t.place, kind=t.kind, capacity=t.capacity, status=TrainingStatus.SCHEDULED, levels=list(t.levels),
    )


async def make(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    return admin, await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=1))


async def test_duplicate_series_and_start_is_rejected(session):
    _, t = await make(session)
    session.add(clone(t))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_series_edit_cancelled_training_does_not_block_its_replacement(session):
    _, t = await make(session)
    t.status = TrainingStatus.CANCELLED
    t.cancel_reason = CancelReason.SERIES_EDIT
    await session.commit()
    session.add(clone(t))
    await session.commit()  # no error


async def test_user_cancelled_training_still_blocks_a_duplicate(session):
    _, t = await make(session)
    t.status = TrainingStatus.CANCELLED
    t.cancel_reason = CancelReason.USER
    await session.commit()
    session.add(clone(t))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_cancel_records_user_reason(session):
    admin, t = await make(session)
    await ScheduleService(session).cancel(admin, [t.id], NOW)
    assert t.cancel_reason == CancelReason.USER
```

Заменить `tests/test_migrations.py` целиком (расширен: приглашения, `cancel_reason`, частичный индекс):
```python
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def test_upgrade_head_on_empty_db(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    lead_sql = con.execute("select sql from sqlite_master where name='uq_chat_one_lead'").fetchone()[0]
    uniq_sql = con.execute("select sql from sqlite_master where name='uq_training_series_start'").fetchone()[0]
    cols = {r[1] for r in con.execute("pragma table_info(trainings)")}
    con.close()
    assert {
        "users", "levels", "chats", "chat_trainers", "training_series", "trainings",
        "series_levels", "training_levels", "participations", "training_posts",
        "scheduled_jobs", "settings", "invites",
    } <= tables
    assert "WHERE" in lead_sql.upper()
    assert "cancel_reason" in cols
    assert "series_edit" in uniq_sql and "WHERE" in uniq_sql.upper()
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/schedule/test_schema_delta.py -v` → FAIL (`CancelReason` не найден).

- [ ] **Step 3: Изменить модели**

В `bot/users/models.py` добавить импорт `Index` уже есть; добавить класс:
```python
class Invite(Base):
    __tablename__ = "invites"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default=Role.TRAINER)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

В `bot/schedule/models.py`: заменить импорт `UniqueConstraint` на `Index, text` (оставить остальные), добавить
```python
class CancelReason:
    USER = "user"
    SERIES_EDIT = "series_edit"
```
В `Training` заменить `__table_args__` и добавить колонку:
```python
    __table_args__ = (
        Index(
            "uq_training_series_start",
            "series_id",
            "starts_at",
            unique=True,
            sqlite_where=text("COALESCE(cancel_reason, '') <> 'series_edit'"),
            postgresql_where=text("COALESCE(cancel_reason, '') <> 'series_edit'"),
        ),
    )
    ...
    cancel_reason: Mapped[str | None] = mapped_column(String(16))
```
(колонку поставить после `published_at`).

В `bot/schedule/service.py` импортировать `CancelReason` и в `cancel` внутри цикла мутации добавить строку после смены статуса:
```python
            training.cancel_reason = CancelReason.USER
```

- [ ] **Step 4: Миграция (вручную, не autogenerate: Alembic путает уникальное ограничение и индекс с одним именем)**

Run: `alembic revision -m "roles invites cancel reason"` — создаётся файл с правильными `revision`/`down_revision`. Заменить тела функций:
```python
import sqlalchemy as sa
import bot.core.db
from alembic import op

_WHERE = "COALESCE(cancel_reason, '') <> 'series_edit'"


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_by", sa.BigInteger, sa.ForeignKey("users.telegram_id"), nullable=False),
        sa.Column("created_at", bot.core.db.UTCDateTime(), nullable=False),
        sa.Column("expires_at", bot.core.db.UTCDateTime(), nullable=False),
        sa.Column("used_by", sa.BigInteger, sa.ForeignKey("users.telegram_id"), nullable=True),
        sa.Column("used_at", bot.core.db.UTCDateTime(), nullable=True),
    )
    with op.batch_alter_table("trainings") as batch:
        batch.add_column(sa.Column("cancel_reason", sa.String(16), nullable=True))
        batch.drop_constraint("uq_training_series_start", type_="unique")
    op.create_index(
        "uq_training_series_start", "trainings", ["series_id", "starts_at"], unique=True,
        sqlite_where=sa.text(_WHERE), postgresql_where=sa.text(_WHERE),
    )


def downgrade() -> None:
    op.drop_index("uq_training_series_start", table_name="trainings")
    with op.batch_alter_table("trainings") as batch:
        batch.create_unique_constraint("uq_training_series_start", ["series_id", "starts_at"])
        batch.drop_column("cancel_reason")
    op.drop_table("invites")
```
Если в миграции плана 1 ограничение называется иначе — подставьте его имя в `drop_constraint`.

- [ ] **Step 5: Прогнать, закоммитить**

Run: `python -m pytest tests/schedule tests/test_migrations.py tests/test_models.py -v` → PASS.
```bash
git add -A
git commit -m "feat(schema): invites, cancel reason, partial unique training index"
```

---

### Task 2: RoleService и поиск пользователей

**Files:**
- Create: `bot/users/roles.py`
- Modify: `bot/users/service.py` (метод `search`)
- Test: `tests/users/test_roles.py`, `tests/users/test_service.py` (дополнить)

**Interfaces:**
- Consumes: `User`, `ChatTrainer`, `Invite`, `Chat`, `require_admin`, `Role`, `DomainError`-ы.
- Produces:
  - `UserService.search(query: str, limit: int = 8, roles: set[str] | None = None) -> list[User]` — без учёта регистра, в том числе для кириллицы (фильтрация в Python: `LIKE` в SQLite нечувствителен к регистру только для ASCII).
  - `RoleService(session, protected_admin_ids: list[int] | None = None)`:
    `list_by_role(role) -> list[User]`; `grant_trainer(actor, user_id) -> User`; `revoke_trainer(actor, user_id) -> User`; `grant_admin(actor, user_id) -> User`; `revoke_admin(actor, user_id) -> User`; `create_invite(actor, now) -> Invite`; `redeem_invite(token, user, now) -> User`; `manageable_chats(actor) -> list[Chat]` (админ — все, главный тренер — свои); `chat_trainers(chat_id) -> list[tuple[User, bool]]`; `add_chat_trainer(actor, chat_id, user_id) -> ChatTrainer`; `remove_chat_trainer(actor, chat_id, user_id)`; `set_lead(actor, chat_id, user_id)`.

- [ ] **Step 1: Написать падающие тесты**

`tests/users/test_roles.py`:
```python
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
```

Добавить в `tests/users/test_service.py`:
```python
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
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/users -v` → FAIL (`bot.users.roles` отсутствует; `search` отсутствует).

- [ ] **Step 3: Реализовать**

В `bot/users/service.py` добавить импорт `from sqlalchemy import select` и метод:
```python
    async def search(self, query: str, limit: int = 8, roles: set[str] | None = None) -> list[User]:
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
```

`bot/users/roles.py`:
```python
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
```

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/users -v` → PASS.
```bash
git add -A
git commit -m "feat(users): RoleService (roles, invites, chat trainers, lead), user search"
```

---

### Task 3: Правка серии и тренировки в ScheduleService

**Files:**
- Modify: `bot/jobs/queue.py` (`delete_for_training`), `bot/schedule/service.py`
- Test: `tests/schedule/test_editing.py`

**Interfaces:**
- Consumes: Task 1 (`cancel_reason`, индекс), `can_manage_training`, `lead_chat_ids`, `target_chat_ids`, `trainer_level_ids`.
- Produces:
  - `delete_for_training(session, training_id)` в `jobs/queue.py`.
  - `TrainingEdit(place: str | None = None, description: str | None = None, capacity: int | None = None, clear_capacity: bool = False)`.
  - `ReplacedTraining(old_id: int, new_id: int | None)` (dataclass).
  - `series_to_input(series: TrainingSeries) -> SeriesInput` (функция модуля).
  - `ScheduleService.allowed_levels(actor) -> list[Level]`; `managed_trainings(actor, now, limit=20) -> list[Training]`; `update_training(actor, training_id, edit, now) -> Training`; `update_series(actor, series_id, data, now) -> list[ReplacedTraining]`.
  - Вспомогательный `_load_levels(level_ids) -> list[Level]` (бросает `NotFound("Уровень не найден")`), его же теперь использует `create_series`.

- [ ] **Step 1: Написать падающие тесты**

`tests/schedule/test_editing.py` (время: `T0 = Вт 2026-10-06 10:00 UTC`, серия «вт в 19:00 МСК» → тренировки 6, 13, 20, 27 октября в 16:00 UTC; правка выполняется `T1 = T0 + 2 дня`):
```python
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, PermissionDenied
from bot.jobs.models import ScheduledJob
from bot.schedule.models import CancelReason, Training, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput, TrainingEdit, series_to_input
from tests.factories import add_chat, add_chat_trainer, add_level, add_user

T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=2)


def utc(m, d, h=16):
    return datetime(2026, m, d, h, 0, tzinfo=timezone.utc)


async def weekly(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    svc = ScheduleService(session)
    series = await svc.create_series(
        admin,
        SeriesInput(kind="intervals", place="Парк", level_ids=[level.id], weekdays={2},
                    start_date=date(2026, 10, 6), start_time=time(19, 0), capacity=5),
        T0,
    )
    return admin, level, svc, series


async def all_trainings(session):
    return list(await session.scalars(select(Training).order_by(Training.starts_at, Training.id)))


async def publish_directly(session, starts):
    t = await session.scalar(select(Training).where(Training.starts_at == starts))
    t.published_at = T0
    await session.commit()
    return t


# ---------- правка серии ----------

async def test_place_change_replaces_published_and_regenerates_unpublished(session):
    admin, level, svc, series = await weekly(session)
    published = await publish_directly(session, utc(10, 13))
    edit = replace(series_to_input(series), place="Стадион")
    result = await svc.update_series(admin, series.id, edit, T1)

    rows = await all_trainings(session)
    live = [t for t in rows if t.status == TrainingStatus.SCHEDULED and t.starts_at > T1]
    assert [t.starts_at for t in live] == [utc(10, 13), utc(10, 20), utc(10, 27), utc(11, 3)]
    assert {t.place for t in live} == {"Стадион"}
    assert published.status == TrainingStatus.CANCELLED and published.cancel_reason == CancelReason.SERIES_EDIT
    assert len(result) == 1 and result[0].old_id == published.id
    new_13 = next(t for t in live if t.starts_at == utc(10, 13))
    assert result[0].new_id == new_13.id
    started = next(t for t in rows if t.starts_at == utc(10, 6))
    assert started.place == "Парк"  # начатая не меняется


async def test_unpublished_trainings_are_deleted_with_their_jobs(session):
    admin, level, svc, series = await weekly(session)
    before = {t.id for t in await all_trainings(session)}
    await svc.update_series(admin, series.id, replace(series_to_input(series), place="Стадион"), T1)
    remaining_ids = {t.id for t in await all_trainings(session)}
    jobs = list(await session.scalars(select(ScheduledJob).where(ScheduledJob.training_id.is_not(None))))
    assert all(j.training_id in remaining_ids for j in jobs)  # нет висящих задач
    assert (before - remaining_ids)  # старые неопубликованные удалены


async def test_user_cancelled_training_is_not_resurrected_by_series_edit(session):
    admin, level, svc, series = await weekly(session)
    t20 = await session.scalar(select(Training).where(Training.starts_at == utc(10, 20)))
    await svc.cancel(admin, [t20.id], T1)
    await svc.update_series(admin, series.id, replace(series_to_input(series), place="Стадион"), T1)
    on_20 = [t for t in await all_trainings(session) if t.starts_at == utc(10, 20)]
    assert len(on_20) == 1 and on_20[0].status == TrainingStatus.CANCELLED


async def test_weekday_change_leaves_published_training_without_a_pair(session):
    admin, level, svc, series = await weekly(session)
    old = await publish_directly(session, utc(10, 13))
    result = await svc.update_series(admin, series.id, replace(series_to_input(series), weekdays={4}), T1)
    assert [(r.old_id, r.new_id) for r in result] == [(old.id, None)]
    thursdays = [t for t in await all_trainings(session) if t.status == "scheduled" and t.starts_at > T1]
    assert all(t.starts_at.isoweekday() == 4 for t in thursdays)


async def test_only_owner_or_admin_edits_a_series(session):
    admin, level, svc, series = await weekly(session)
    chat = await add_chat(session, level)
    other = await add_user(session, 10, "trainer")
    await add_chat_trainer(session, chat, 10)
    with pytest.raises(PermissionDenied):
        await svc.update_series(other, series.id, series_to_input(series), T1)


async def test_trainer_edits_own_series_within_his_levels(session):
    level = await add_level(session)
    other_level = await add_level(session, "Другой", 1)
    chat = await add_chat(session, level)
    trainer = await add_user(session, 10, "trainer")
    await add_chat_trainer(session, chat, 10)
    svc = ScheduleService(session)
    series = await svc.create_series(
        trainer,
        SeriesInput(kind="intervals", place="Парк", level_ids=[level.id], weekdays={2},
                    start_date=date(2026, 10, 6), start_time=time(19, 0)),
        T0,
    )
    await svc.update_series(trainer, series.id, replace(series_to_input(series), place="Лес"), T1)
    with pytest.raises(PermissionDenied):
        await svc.update_series(trainer, series.id, replace(series_to_input(series), level_ids=[other_level.id]), T1)


async def test_invalid_series_edit_changes_nothing(session):
    admin, level, svc, series = await weekly(session)
    before = [(t.id, t.status) for t in await all_trainings(session)]
    with pytest.raises(InvalidAction):
        await svc.update_series(admin, series.id, replace(series_to_input(series), capacity=0), T1)
    assert [(t.id, t.status) for t in await all_trainings(session)] == before


# ---------- правка одной тренировки ----------

async def test_edit_single_training_does_not_touch_the_series(session):
    admin, level, svc, series = await weekly(session)
    t = await session.scalar(select(Training).where(Training.starts_at == utc(10, 13)))
    await svc.update_training(admin, t.id, TrainingEdit(place="Склон", description="Дождь", capacity=3), T1)
    assert (t.place, t.description, t.capacity) == ("Склон", "Дождь", 3)
    other = await session.scalar(select(Training).where(Training.starts_at == utc(10, 20)))
    assert other.place == "Парк"
    await session.refresh(series)
    assert series.place == "Парк"
    await svc.update_training(admin, t.id, TrainingEdit(clear_capacity=True), T1)
    assert t.capacity is None


async def test_edit_single_training_rules(session):
    admin, level, svc, series = await weekly(session)
    t = await session.scalar(select(Training).where(Training.starts_at == utc(10, 13)))
    member = await add_user(session, 1, "member")
    with pytest.raises(PermissionDenied):
        await svc.update_training(member, t.id, TrainingEdit(place="X"), T1)
    with pytest.raises(InvalidAction):
        await svc.update_training(admin, t.id, TrainingEdit(place="  "), T1)
    with pytest.raises(InvalidAction):
        await svc.update_training(admin, t.id, TrainingEdit(capacity=0), T1)
    with pytest.raises(InvalidAction):
        await svc.update_training(admin, t.id, TrainingEdit(place="X"), utc(10, 13, 17))  # уже началась


# ---------- списки ----------

async def test_allowed_levels(session):
    a = await add_level(session, "A", 0)
    b = await add_level(session, "B", 1)
    chat = await add_chat(session, a)
    trainer = await add_user(session, 10, "trainer")
    admin = await add_user(session, 99, "admin")
    await add_chat_trainer(session, chat, 10)
    svc = ScheduleService(session)
    assert [l.id for l in await svc.allowed_levels(trainer)] == [a.id]
    assert [l.id for l in await svc.allowed_levels(admin)] == [a.id, b.id]


async def test_managed_trainings_for_owner_lead_and_admin(session):
    level_a = await add_level(session, "A", 0)
    level_b = await add_level(session, "B", 1)
    chat_a = await add_chat(session, level_a, -1001)
    chat_b = await add_chat(session, level_b, -1002)
    owner = await add_user(session, 10, "trainer")
    lead = await add_user(session, 20, "trainer")
    admin = await add_user(session, 99, "admin")
    await add_chat_trainer(session, chat_a, 20, is_lead=True)
    await add_chat_trainer(session, chat_b, 10, is_lead=True)
    svc = ScheduleService(session)
    mk = lambda levels: SeriesInput(  # noqa: E731
        kind="intervals", place="Парк", level_ids=[l.id for l in levels],
        start_date=date(2026, 10, 20), start_time=time(19, 0),
    )
    await svc.create_series(admin, replace(mk([level_a]), trainer_id=10), T0)
    await svc.create_series(admin, replace(mk([level_a, level_b]), start_date=date(2026, 10, 21), trainer_id=10), T0)
    assert len(await svc.managed_trainings(owner, T0)) == 2
    assert len(await svc.managed_trainings(lead, T0)) == 1  # лидирует только в A
    assert len(await svc.managed_trainings(admin, T0)) == 2
    member = await add_user(session, 1, "member")
    assert await svc.managed_trainings(member, T0) == []
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/schedule/test_editing.py -v` → FAIL (`TrainingEdit` не найден).

- [ ] **Step 3: Реализовать**

В `bot/jobs/queue.py` добавить `delete` в импорт `sqlalchemy` и функцию:
```python
async def delete_for_training(session: AsyncSession, training_id: int) -> None:
    await session.execute(delete(ScheduledJob).where(ScheduledJob.training_id == training_id))
```

В `bot/schedule/service.py`:

1. Импорты: добавить `delete`-не нужен; добавить `CancelReason` (уже из Task 1), `delete_for_training`, `can_manage_training` (уже есть).

2. После `SeriesInput` добавить:
```python
@dataclass
class TrainingEdit:
    place: str | None = None
    description: str | None = None
    capacity: int | None = None
    clear_capacity: bool = False


@dataclass(frozen=True)
class ReplacedTraining:
    old_id: int
    new_id: int | None  # None: пары нет, участникам уйдёт обычное уведомление об отмене


def series_to_input(series: TrainingSeries) -> SeriesInput:
    return SeriesInput(
        kind=series.kind,
        place=series.place,
        level_ids=[lvl.id for lvl in series.levels],
        start_date=series.start_date,
        start_time=series.start_time,
        weekdays={int(x) for x in series.weekdays.split(",") if x},
        end_date=series.end_date,
        capacity=series.capacity,
        description=series.description,
        duration_min=series.duration_min,
        trainer_id=series.trainer_id,
    )
```

3. В `create_series` заменить блок загрузки уровней
```python
        levels = list(await self.session.scalars(select(Level).where(Level.id.in_(data.level_ids))))
        if len(levels) != len(set(data.level_ids)):
            raise NotFound("Уровень не найден")
```
на `levels = await self._load_levels(data.level_ids)` и добавить метод:
```python
    async def _load_levels(self, level_ids: list[int]) -> list[Level]:
        levels = list(await self.session.scalars(select(Level).where(Level.id.in_(level_ids))))
        if len(levels) != len(set(level_ids)):
            raise NotFound("Уровень не найден")
        return levels
```

4. В `_generate_for_series` заменить запрос `existing`:
```python
        existing = set(
            await self.session.scalars(
                select(Training.starts_at).where(
                    Training.series_id == series.id,
                    Training.starts_at >= now,
                    Training.starts_at < horizon,
                    or_(Training.cancel_reason.is_(None), Training.cancel_reason != CancelReason.SERIES_EDIT),
                )
            )
        )
```

5. Новые методы класса:
```python
    async def allowed_levels(self, actor: User) -> list[Level]:
        query = select(Level).order_by(Level.position, Level.id)
        if actor.role != Role.ADMIN:
            ids = await trainer_level_ids(self.session, actor.telegram_id)
            query = query.where(Level.id.in_(ids))
        return list(await self.session.scalars(query))

    async def managed_trainings(self, actor: User, now: datetime, limit: int = 20) -> list[Training]:
        if actor.role not in (Role.ADMIN, Role.TRAINER):
            return []
        candidates = await self.session.scalars(
            select(Training)
            .where(Training.status == TrainingStatus.SCHEDULED, Training.starts_at > now)
            .order_by(Training.starts_at)
            .limit(300)
        )
        lead = set() if actor.role == Role.ADMIN else await lead_chat_ids(self.session, actor.telegram_id)
        result: list[Training] = []
        for training in candidates:
            if actor.role == Role.ADMIN or training.trainer_id == actor.telegram_id:
                allowed = True
            else:
                allowed = can_manage_training(
                    actor, training.trainer_id, set(await target_chat_ids(self.session, training)), lead
                )
            if allowed:
                result.append(training)
                if len(result) >= limit:
                    break
        return result

    async def update_training(self, actor: User, training_id: int, edit: TrainingEdit, now: datetime) -> Training:
        training = await self.session.get(Training, training_id)
        if training is None:
            raise NotFound("Тренировка не найдена")
        if training.status != TrainingStatus.SCHEDULED or training.starts_at <= now:
            raise InvalidAction("Тренировку уже нельзя изменить")
        if not await self.can_manage(actor, training):
            raise PermissionDenied("Нет прав на эту тренировку")
        if edit.place is not None:
            if not edit.place.strip():
                raise InvalidAction("Укажите место тренировки")
        if edit.capacity is not None and edit.capacity < 1:
            raise InvalidAction("Лимит мест должен быть больше нуля")
        if edit.place is not None:
            training.place = edit.place.strip()
        if edit.description is not None:
            training.description = edit.description
        if edit.clear_capacity:
            training.capacity = None
        elif edit.capacity is not None:
            training.capacity = edit.capacity
        await self.session.commit()
        return training

    async def update_series(
        self, actor: User, series_id: int, data: SeriesInput, now: datetime
    ) -> list[ReplacedTraining]:
        """Применяет шаблон к будущим тренировкам серии. Все проверки идут до первой мутации."""
        series = await self.session.get(TrainingSeries, series_id)
        if series is None:
            raise NotFound("Серия не найдена")
        if actor.role != Role.ADMIN and series.trainer_id != actor.telegram_id:
            raise PermissionDenied("Серию может менять только её тренер или администратор")
        self._validate(data)
        await self._check_can_create(actor, set(data.level_ids))
        levels = await self._load_levels(data.level_ids)
        tz = await self._tz()
        if not data.weekdays and datetime.combine(data.start_date, data.start_time, tzinfo=tz) <= now:
            raise InvalidAction("Тренировка должна быть в будущем")
        if data.trainer_id not in (None, series.trainer_id):
            require_admin(actor)
            if await self.session.get(User, data.trainer_id) is None:
                raise NotFound("Тренер не найден")
            series.trainer_id = data.trainer_id

        series.kind = data.kind
        series.place = data.place.strip()
        series.description = data.description
        series.capacity = data.capacity
        series.weekdays = ",".join(str(d) for d in sorted(data.weekdays))
        series.start_date = data.start_date
        series.start_time = data.start_time
        series.end_date = data.end_date
        series.duration_min = data.duration_min or series.duration_min
        series.levels = levels

        future = list(
            await self.session.scalars(
                select(Training).where(
                    Training.series_id == series.id,
                    Training.status == TrainingStatus.SCHEDULED,
                    Training.starts_at > now,
                )
            )
        )
        replaced: list[Training] = []
        for training in future:
            if training.published_at is None:
                await delete_for_training(self.session, training.id)
                await self.session.delete(training)
            else:
                training.status = TrainingStatus.CANCELLED
                training.cancel_reason = CancelReason.SERIES_EDIT
                await cancel_for_training(self.session, training.id, now)
                replaced.append(training)
        await self.session.flush()  # индекс: старое сначала выходит из уникальности, потом вставляется новое

        created = await self._generate_for_series(series, now, tz)
        by_date = {t.starts_at.astimezone(tz).date(): t for t in created}
        result: list[ReplacedTraining] = []
        for old in replaced:
            new = by_date.pop(old.starts_at.astimezone(tz).date(), None)
            result.append(ReplacedTraining(old.id, new.id if new is not None else None))
        await self.session.commit()
        return result
```

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/schedule tests/jobs -v` → PASS (включая тесты плана 1).
```bash
git add -A
git commit -m "feat(schedule): edit series (replace published, regenerate unpublished) and single trainings"
```

---

### Task 4: Ручная запись, уведомления о замене и сценарии

**Files:**
- Modify: `bot/participation/service.py`, `bot/publishing/service.py`, `bot/usecases.py`
- Test: `tests/participation/test_manual.py`, `tests/publishing/test_replaced.py`, `tests/test_usecases_editing.py`

**Interfaces:**
- Consumes: Task 3 (`ScheduleService.can_manage`, `update_series`, `update_training`, `TrainingEdit`, `ReplacedTraining`).
- Produces:
  - `JoinOutcome.OVER_LIMIT = "over_limit"`.
  - `ParticipationService.manual_add(actor, training_id, user_id, now, *, allow_over_limit=False) -> JoinOutcome` (`JOINED` / `ALREADY` / `OVER_LIMIT`; пишет `added_by`); `manual_remove(actor, training_id, user_id, now) -> bool` (True, если человек был `going`; переводит в `declined`).
  - `PublishService.post_link(training_id) -> str | None`; `announce_replaced(old_id, new_id | None)`; `notify_manual_change(training_id, user_id, added: bool)`.
  - Сценарии `bot/usecases.py`: `update_series(session, messenger, actor, series_id, data, now) -> list[ReplacedTraining]`; `update_training(session, messenger, actor, training_id, edit, now) -> Training`; `manual_add_participant(session, messenger, actor, training_id, user_id, now, allow_over_limit=False) -> JoinOutcome`; `manual_remove_participant(session, messenger, actor, training_id, user_id, now) -> bool`.

- [ ] **Step 1: Написать падающие тесты**

`tests/participation/test_manual.py`:
```python
from datetime import timedelta

import pytest

from bot.core.errors import NotFound, PermissionDenied
from bot.participation.models import Participation
from bot.participation.service import JoinOutcome, ParticipationService
from tests.factories import NOW, add_level, add_training, add_user


async def setup(session, capacity=1):
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20),
        capacity=capacity, published_at=NOW - timedelta(hours=1),
    )
    return trainer, t


async def test_manual_add_records_who_added(session):
    trainer, t = await setup(session, capacity=5)
    await add_user(session, 1)
    svc = ParticipationService(session)
    assert await svc.manual_add(trainer, t.id, 1, NOW) == JoinOutcome.JOINED
    row = await session.get(Participation, (t.id, 1))
    assert row.status == "going" and row.added_by == 10
    assert await svc.manual_add(trainer, t.id, 1, NOW) == JoinOutcome.ALREADY


async def test_manual_add_over_limit_needs_confirmation(session):
    trainer, t = await setup(session, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    svc = ParticipationService(session)
    await svc.manual_add(trainer, t.id, 1, NOW)
    assert await svc.manual_add(trainer, t.id, 2, NOW) == JoinOutcome.OVER_LIMIT
    assert await session.get(Participation, (t.id, 2)) is None
    assert await svc.manual_add(trainer, t.id, 2, NOW, allow_over_limit=True) == JoinOutcome.JOINED
    assert await svc.going_count(t.id) == 2


async def test_manual_add_requires_rights_and_known_user(session):
    trainer, t = await setup(session)
    stranger = await add_user(session, 5, "trainer")
    await add_user(session, 1)
    svc = ParticipationService(session)
    with pytest.raises(PermissionDenied):
        await svc.manual_add(stranger, t.id, 1, NOW)
    with pytest.raises(NotFound):
        await svc.manual_add(trainer, t.id, 777, NOW)


async def test_manual_remove(session):
    trainer, t = await setup(session, capacity=5)
    await add_user(session, 1)
    svc = ParticipationService(session)
    assert await svc.manual_remove(trainer, t.id, 1, NOW) is False  # не был записан
    await svc.manual_add(trainer, t.id, 1, NOW)
    assert await svc.manual_remove(trainer, t.id, 1, NOW) is True
    assert await svc.going_count(t.id) == 0
```

`tests/publishing/test_replaced.py`:
```python
from datetime import timedelta

from bot.participation.service import ParticipationService
from bot.publishing.service import PublishService
from bot.schedule.models import TrainingStatus
from tests.factories import NOW, add_chat, add_level, add_training, add_user

STARTS = NOW + timedelta(hours=24)


async def world(session, chat_id=-1001234567890):
    await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer", name="Иван")
    level = await add_level(session)
    await add_chat(session, level, chat_id)
    return trainer, level


async def test_post_link_for_supergroup_and_none_otherwise(session, messenger):
    trainer, level = await world(session)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    svc = PublishService(session, messenger, send_delay=0)
    assert await svc.post_link(t.id) is None  # ещё не опубликована
    await svc.publish(t.id, NOW)
    post_id = messenger.sent[0].message_id
    assert await svc.post_link(t.id) == f"https://t.me/c/1234567890/{post_id}"


async def test_post_link_is_none_for_basic_group(session, messenger):
    trainer, level = await world(session, chat_id=-555)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    svc = PublishService(session, messenger, send_delay=0)
    await svc.publish(t.id, NOW)
    assert await svc.post_link(t.id) is None


async def test_announce_replaced_notifies_with_link_to_the_new_post(session, messenger):
    trainer, level = await world(session)
    old = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    new = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS + timedelta(days=7))
    svc = PublishService(session, messenger, send_delay=0)
    await svc.publish(old.id, NOW)
    await svc.publish(new.id, NOW)
    await add_user(session, 1)
    await ParticipationService(session).join(old.id, 1, NOW)
    old.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc.announce_replaced(old.id, new.id)
    (uid, text), = [n for n in messenger.notifications if n[0] == 1]
    assert "изменена" in text and "https://t.me/c/1234567890/" in text
    assert "Отменена" in messenger.edits[-1].text


async def test_announce_replaced_without_pair_is_a_plain_cancellation(session, messenger):
    trainer, level = await world(session)
    old = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    svc = PublishService(session, messenger, send_delay=0)
    await svc.publish(old.id, NOW)
    await add_user(session, 1)
    await ParticipationService(session).join(old.id, 1, NOW)
    old.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc.announce_replaced(old.id, None)
    assert [text for uid, text in messenger.notifications if uid == 1][0].startswith("❌ Тренировка отменена")


async def test_notify_manual_change_ignores_unreachable_user(session, messenger):
    trainer, level = await world(session)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    await add_user(session, 1)
    messenger.unreachable.add(1)
    svc = PublishService(session, messenger, send_delay=0)
    await svc.notify_manual_change(t.id, 1, added=True)  # не бросает
    assert messenger.notifications == []
    messenger.unreachable.clear()
    await svc.notify_manual_change(t.id, 1, added=False)
    assert "сн" in messenger.notifications[0][1]
```

`tests/test_usecases_editing.py`:
```python
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction
from bot.participation.service import JoinOutcome
from bot.publishing.service import PublishService
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput, TrainingEdit, series_to_input
from bot.usecases import (
    join_training, manual_add_participant, manual_remove_participant, update_series, update_training,
)
from tests.factories import add_chat, add_level, add_user

T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=2)


async def weekly_published(session, messenger, capacity=2):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    svc = ScheduleService(session)
    series = await svc.create_series(
        admin,
        SeriesInput(kind="intervals", place="Парк", level_ids=[level.id], weekdays={2},
                    start_date=date(2026, 10, 6), start_time=time(19, 0), capacity=capacity),
        T0,
    )
    t13 = await session.scalar(select(Training).where(Training.starts_at == datetime(2026, 10, 13, 16, tzinfo=timezone.utc)))
    await PublishService(session, messenger, send_delay=0).publish(t13.id, T1)
    return admin, svc, series, t13


async def test_series_edit_replaces_publishes_new_and_notifies_with_link(session, messenger):
    admin, svc, series, t13 = await weekly_published(session, messenger)
    await add_user(session, 1)
    await join_training(session, messenger, t13.id, 1, T1)
    result = await update_series(session, messenger, admin, series.id, replace(series_to_input(series), place="Стадион"), T1)
    assert len(result) == 1 and result[0].new_id is not None
    new = await session.get(Training, result[0].new_id)
    assert new.published_at is not None  # новая опубликована сразу
    msg = [text for uid, text in messenger.notifications if uid == 1][-1]
    assert "изменена" in msg and "https://t.me/c/1234567890/" in msg


async def test_training_edit_refreshes_the_post_and_protects_going_users(session, messenger):
    admin, svc, series, t13 = await weekly_published(session, messenger, capacity=5)
    for uid in (1, 2, 3):
        await add_user(session, uid)
        await join_training(session, messenger, t13.id, uid, T1)
    await update_training(session, messenger, admin, t13.id, TrainingEdit(place="Склон"), T1)
    assert "Склон" in messenger.edits[-1].text
    with pytest.raises(InvalidAction, match="записано"):
        await update_training(session, messenger, admin, t13.id, TrainingEdit(capacity=2), T1)
    assert t13.capacity == 5


async def test_manual_add_flow_over_limit_and_notifications(session, messenger):
    admin, svc, series, t13 = await weekly_published(session, messenger, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    assert await manual_add_participant(session, messenger, admin, t13.id, 1, T1) == JoinOutcome.JOINED
    assert "Идут: 1/1" in messenger.edits[-1].text
    assert any(uid == 1 for uid, _ in messenger.notifications)
    edits = len(messenger.edits)
    assert await manual_add_participant(session, messenger, admin, t13.id, 2, T1) == JoinOutcome.OVER_LIMIT
    assert len(messenger.edits) == edits  # ничего не менялось
    assert await manual_add_participant(session, messenger, admin, t13.id, 2, T1, allow_over_limit=True) == JoinOutcome.JOINED
    assert "Идут: 2/1" in messenger.edits[-1].text
    assert await manual_remove_participant(session, messenger, admin, t13.id, 2, T1) is True
    assert "Идут: 1/1" in messenger.edits[-1].text
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/participation/test_manual.py tests/publishing/test_replaced.py tests/test_usecases_editing.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

В `bot/participation/service.py`: добавить `OVER_LIMIT = "over_limit"` в `JoinOutcome`; импортировать `PermissionDenied` и `ScheduleService` (`from bot.schedule.service import ScheduleService`); добавить методы:
```python
    async def manual_add(
        self, actor: User, training_id: int, user_id: int, now: datetime, *, allow_over_limit: bool = False
    ) -> JoinOutcome:
        training = await self._open_training(training_id, now)
        if not await ScheduleService(self.session).can_manage(actor, training):
            raise PermissionDenied("Нет прав на эту тренировку")
        if await self.session.get(User, user_id) is None:
            raise NotFound("Пользователь не найден")
        row = await self.session.get(Participation, (training_id, user_id))
        if row is not None and row.status == ParticipationStatus.GOING:
            return JoinOutcome.ALREADY
        full = training.capacity is not None and await self.going_count(training_id) >= training.capacity
        if full and not allow_over_limit:
            return JoinOutcome.OVER_LIMIT
        if row is None:
            self.session.add(
                Participation(
                    training_id=training_id, user_id=user_id, status=ParticipationStatus.GOING,
                    updated_at=now, added_by=actor.telegram_id,
                )
            )
        else:
            row.status = ParticipationStatus.GOING
            row.updated_at = now
            row.added_by = actor.telegram_id
        await self.session.commit()
        return JoinOutcome.JOINED

    async def manual_remove(self, actor: User, training_id: int, user_id: int, now: datetime) -> bool:
        training = await self._open_training(training_id, now)
        if not await ScheduleService(self.session).can_manage(actor, training):
            raise PermissionDenied("Нет прав на эту тренировку")
        row = await self.session.get(Participation, (training_id, user_id))
        if row is None or row.status != ParticipationStatus.GOING:
            return False
        row.status = ParticipationStatus.DECLINED
        row.updated_at = now
        await self.session.commit()
        return True
```
(импорт `PermissionDenied` добавить в строку `from bot.core.errors import ...`).

В `bot/publishing/service.py` добавить методы в `PublishService`:
```python
    async def post_link(self, training_id: int) -> str | None:
        post = await self.session.scalar(
            select(TrainingPost).where(TrainingPost.training_id == training_id).order_by(TrainingPost.chat_id)
        )
        if post is None:
            return None
        chat = str(post.chat_id)
        if not chat.startswith("-100"):  # ссылки на сообщения есть только у супергрупп
            return None
        return f"https://t.me/c/{chat[4:]}/{post.message_id}"

    async def announce_replaced(self, old_id: int, new_id: int | None) -> None:
        if new_id is None:
            await self.announce_cancelled(old_id)
            return
        old = await self.session.get(Training, old_id)
        if old is None:
            return
        await self.refresh_posts(old_id)
        text = f"🔄 Тренировка изменена, запишитесь на обновлённую: {self._title(old, await self._tz())}"
        link = await self.post_link(new_id)
        if link:
            text += f"\n{link}"
        for user_id in await self._going_ids(old_id):
            if not await self.messenger.notify(user_id, text):
                log.info("replace notice not delivered to %s (bot not started?)", user_id)

    async def notify_manual_change(self, training_id: int, user_id: int, added: bool) -> None:
        training = await self.session.get(Training, training_id)
        if training is None:
            return
        title = self._title(training, await self._tz())
        text = f"✅ Тренер записал вас на тренировку: {title}" if added else f"❌ Тренер снял вас с тренировки: {title}"
        if not await self.messenger.notify(user_id, text):
            log.info("manual change notice not delivered to %s", user_id)
```

В `bot/usecases.py` добавить импорты (`InvalidAction`, `TrainingEdit`, `SeriesInput`, `ReplacedTraining`) и функции:
```python
async def update_series(
    session: AsyncSession, messenger: Messenger, actor: User, series_id: int, data: SeriesInput, now: datetime
) -> list[ReplacedTraining]:
    replaced = await ScheduleService(session).update_series(actor, series_id, data, now)
    publisher = PublishService(session, messenger)
    for item in replaced:
        if item.new_id is not None:
            await publisher.publish(item.new_id, now)  # сразу, чтобы в уведомлении была ссылка на пост
        await publisher.announce_replaced(item.old_id, item.new_id)
    return replaced


async def update_training(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, edit: TrainingEdit, now: datetime
) -> Training:
    if edit.capacity is not None:
        going = await ParticipationService(session).going_count(training_id)
        if edit.capacity < going:
            raise InvalidAction(f"Уже записано {going} — лимит не может быть меньше")
    training = await ScheduleService(session).update_training(actor, training_id, edit, now)
    await PublishService(session, messenger).refresh_posts(training_id)
    return training


async def manual_add_participant(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, user_id: int, now: datetime,
    allow_over_limit: bool = False,
) -> JoinOutcome:
    outcome = await ParticipationService(session).manual_add(
        actor, training_id, user_id, now, allow_over_limit=allow_over_limit
    )
    if outcome == JoinOutcome.JOINED:
        publisher = PublishService(session, messenger)
        await publisher.refresh_posts(training_id)
        await publisher.notify_manual_change(training_id, user_id, added=True)
    return outcome


async def manual_remove_participant(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, user_id: int, now: datetime
) -> bool:
    removed = await ParticipationService(session).manual_remove(actor, training_id, user_id, now)
    if removed:
        publisher = PublishService(session, messenger)
        await publisher.refresh_posts(training_id)
        await publisher.notify_manual_change(training_id, user_id, added=False)
    return removed
```
Импорты вверху `usecases.py`: `from bot.core.errors import InvalidAction`, `from bot.schedule.service import ReplacedTraining, ScheduleService, SeriesInput, TrainingEdit`.

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest -v` → всё PASS.
```bash
git add -A
git commit -m "feat: manual sign-up, series-edit announcements, editing use cases"
```

---

### Task 5: Доступ по членству в клубе

**Files:**
- Modify: `bot/core/messenger.py` (протокол `MembershipChecker`), `tests/fakes.py` (`FakeMembership`)
- Create: `bot/users/access.py`
- Test: `tests/users/test_access.py`

**Interfaces:**
- Produces: `MembershipChecker` Protocol: `async is_member(chat_id: int, user_id: int) -> bool`. `AccessService(session, checker, admin_ids: list[int], cache: dict[int, tuple[bool, datetime]], ttl: timedelta = 5 минут)` с `async can_use(user_id, now) -> bool`. Исключение при проверке одного чата считается «не участник» и не ломает остальные. Без привязанных чатов доступа нет (кроме админов из `ADMIN_IDS`).
- `FakeMembership(members: set[tuple[int, int]])` со счётчиком `calls` и множеством `failing_chats`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/fakes.py`:
```python
class FakeMembership:
    def __init__(self, members: set[tuple[int, int]] | None = None) -> None:
        self.members = members or set()
        self.calls = 0
        self.failing_chats: set[int] = set()

    async def is_member(self, chat_id: int, user_id: int) -> bool:
        self.calls += 1
        if chat_id in self.failing_chats:
            raise RuntimeError("telegram error")
        return (chat_id, user_id) in self.members
```

`tests/users/test_access.py`:
```python
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
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/users/test_access.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

Дописать в `bot/core/messenger.py`:
```python
class MembershipChecker(Protocol):
    async def is_member(self, chat_id: int, user_id: int) -> bool:
        """Состоит ли пользователь в чате. Не должен бросать из-за «чат недоступен» — вернуть False."""
```

`bot/users/access.py`:
```python
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import MembershipChecker
from bot.schedule.models import Chat

log = logging.getLogger(__name__)

DENIED_TEXT = "Бот доступен участникам клуба"


class AccessService:
    def __init__(
        self,
        session: AsyncSession,
        checker: MembershipChecker,
        admin_ids: list[int],
        cache: dict[int, tuple[bool, datetime]],
        ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        self.session = session
        self.checker = checker
        self.admin_ids = admin_ids
        self.cache = cache
        self.ttl = ttl

    async def can_use(self, user_id: int, now: datetime) -> bool:
        if user_id in self.admin_ids:
            return True
        hit = self.cache.get(user_id)
        if hit is not None and now - hit[1] < self.ttl:
            return hit[0]
        allowed = False
        for chat_id in list(await self.session.scalars(select(Chat.chat_id))):
            try:
                if await self.checker.is_member(chat_id, user_id):
                    allowed = True
                    break
            except Exception:
                log.warning("membership check failed for chat %s", chat_id, exc_info=True)
        self.cache[user_id] = (allowed, now)
        return allowed
```

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/users -v` → PASS.
```bash
git add -A
git commit -m "feat(users): club membership access check with cache"
```

---

### Task 6: Разбор ввода форм (чистая логика)

**Files:**
- Create: `bot/schedule/forms.py`
- Modify: `bot/core/settings.py` (`validate_setting`)
- Test: `tests/schedule/test_forms.py`, `tests/core/test_settings.py` (дополнить)

**Interfaces:**
- Produces (все ошибки — `InvalidAction` с русским текстом и подсказкой формата):
  - `parse_date(text, today) -> date` (`20.10` — ближайшая такая дата не раньше `today`; `20.10.2026`);
  - `parse_optional_date(text, today) -> date | None` (`-`/`нет` → `None`);
  - `parse_time(text) -> time` (`19:00`, `19.00`, `9:05`);
  - `parse_weekdays(text) -> set[int]` (`вт чт`, `пн,ср,пт`, `понедельник`);
  - `parse_capacity(text) -> int | None` (`-`/`нет` → `None`; целое ≥ 1);
  - `parse_kind(text) -> str` (`интервалы`/`длительная`/`техника` или значение `intervals/...`);
  - `build_series_input(data: dict) -> SeriesInput`; `summarize_series(inp, level_names) -> str`;
  - `apply_series_field(inp, field, text, today) -> SeriesInput` для полей `place, description, capacity, weekdays, time, end_date, kind`; `apply_training_field(field, text) -> TrainingEdit` для `place, description, capacity` (`-` в `capacity` = снять лимит);
  - `SERIES_FIELD_PROMPTS: dict[str, str]`, `TRAINING_FIELD_PROMPTS: dict[str, str]` — подсказки для пользователя.
  - `validate_setting(key, value) -> str` в `core/settings.py`.

- [ ] **Step 1: Написать падающие тесты**

`tests/schedule/test_forms.py`:
```python
from datetime import date, time

import pytest

from bot.core.errors import InvalidAction
from bot.schedule.forms import (
    apply_series_field, apply_training_field, build_series_input, parse_capacity, parse_date, parse_kind,
    parse_optional_date, parse_time, parse_weekdays, summarize_series,
)
from bot.schedule.service import SeriesInput, TrainingEdit

TODAY = date(2026, 10, 6)


def test_parse_date():
    assert parse_date("20.10", TODAY) == date(2026, 10, 20)
    assert parse_date("5.10", TODAY) == date(2027, 10, 5)  # уже прошла в этом году -> следующий
    assert parse_date("06.10", TODAY) == date(2026, 10, 6)  # сегодня подходит
    assert parse_date(" 20.10.2026 ", TODAY) == date(2026, 10, 20)


@pytest.mark.parametrize("bad", ["31.02", "32.01", "20-10", "завтра", "", "20.13", "1.1.26"])
def test_parse_date_rejects_garbage_with_a_hint(bad):
    with pytest.raises(InvalidAction, match="20.10"):
        parse_date(bad, TODAY)


def test_parse_optional_date():
    assert parse_optional_date("-", TODAY) is None
    assert parse_optional_date("нет", TODAY) is None
    assert parse_optional_date("31.12", TODAY) == date(2026, 12, 31)


def test_parse_time():
    assert parse_time("19:00") == time(19, 0)
    assert parse_time("9.05") == time(9, 5)


@pytest.mark.parametrize("bad", ["25:00", "19:60", "7", "вечером", ""])
def test_parse_time_rejects_garbage(bad):
    with pytest.raises(InvalidAction, match="19:00"):
        parse_time(bad)


def test_parse_weekdays():
    assert parse_weekdays("вт чт") == {2, 4}
    assert parse_weekdays("Пн, ср,ПТ") == {1, 3, 5}
    assert parse_weekdays("понедельник воскресенье") == {1, 7}


@pytest.mark.parametrize("bad", ["", "вторник и пятница через раз", "xx"])
def test_parse_weekdays_rejects_garbage(bad):
    with pytest.raises(InvalidAction, match="вт чт"):
        parse_weekdays(bad)


def test_parse_capacity():
    assert parse_capacity("12") == 12
    assert parse_capacity("-") is None and parse_capacity("нет") is None
    for bad in ("0", "-3", "много", "1.5"):
        with pytest.raises(InvalidAction):
            parse_capacity(bad)


def test_parse_kind():
    assert parse_kind("Интервалы") == "intervals"
    assert parse_kind("длительная") == "endurance"
    assert parse_kind("technique") == "technique"
    with pytest.raises(InvalidAction):
        parse_kind("йога")


def base():
    return SeriesInput(
        kind="intervals", place="Парк", level_ids=[1, 2], start_date=TODAY, start_time=time(19, 0),
        weekdays={2, 4}, capacity=10,
    )


def test_build_series_input_from_fsm_data():
    data = {
        "kind": "intervals", "place": "Парк", "level_ids": [1, 2], "weekdays": [2, 4],
        "start_date": "2026-10-06", "start_time": "19:00", "end_date": None, "capacity": 10, "description": "",
    }
    assert build_series_input(data) == base()
    one_off = {**data, "weekdays": [], "start_date": "2026-10-20"}
    assert build_series_input(one_off).weekdays == set()


def test_summary_mentions_all_decisions():
    text = summarize_series(base(), ["Новички", "Любители"])
    for part in ("Интервалы", "Парк", "Новички, Любители", "вт, чт", "19:00", "10"):
        assert part in text
    one_off = summarize_series(SeriesInput(
        kind="technique", place="Зал", level_ids=[1], start_date=date(2026, 10, 20), start_time=time(8, 0),
    ), ["Новички"])
    assert "20.10.2026" in one_off and "без лимита" in one_off


def test_apply_series_field_changes_only_that_field():
    inp = base()
    assert apply_series_field(inp, "place", " Стадион ", TODAY).place == "Стадион"
    assert apply_series_field(inp, "weekdays", "пн", TODAY).weekdays == {1}
    assert apply_series_field(inp, "time", "7:30", TODAY).start_time == time(7, 30)
    assert apply_series_field(inp, "capacity", "-", TODAY).capacity is None
    assert apply_series_field(inp, "end_date", "31.12", TODAY).end_date == date(2026, 12, 31)
    assert apply_series_field(inp, "description", "-", TODAY).description == ""
    assert apply_series_field(inp, "kind", "техника", TODAY).kind == "technique"
    assert apply_series_field(inp, "place", "Стадион", TODAY).capacity == 10
    with pytest.raises(InvalidAction):
        apply_series_field(inp, "level_ids", "1", TODAY)


def test_apply_training_field():
    assert apply_training_field("place", "Склон") == TrainingEdit(place="Склон")
    assert apply_training_field("description", "Дождь") == TrainingEdit(description="Дождь")
    assert apply_training_field("capacity", "4") == TrainingEdit(capacity=4)
    assert apply_training_field("capacity", "-") == TrainingEdit(clear_capacity=True)
```

Добавить в `tests/core/test_settings.py`:
```python
from bot.core.errors import InvalidAction
from bot.core.settings import validate_setting


def test_validate_setting():
    assert validate_setting("publish_window_h", " 12 ") == "12"
    assert validate_setting("club_timezone", "Europe/Berlin") == "Europe/Berlin"
    assert validate_setting("inventory_deadline_h", "") == ""
    for key, bad in (("publish_window_h", "0"), ("reminder_h", "два"), ("club_timezone", "Mars/Base"), ("duration_min", "-5")):
        with pytest.raises(InvalidAction):
            validate_setting(key, bad)
    with pytest.raises(KeyError):
        validate_setting("nope", "1")
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/schedule/test_forms.py tests/core/test_settings.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

Добавить в `bot/core/settings.py` (импорты `from zoneinfo import ZoneInfo`, `from bot.core.errors import InvalidAction`):
```python
def validate_setting(key: str, value: str) -> str:
    value = value.strip()
    if key in ("publish_window_h", "reminder_h", "duration_min"):
        if not value.isdigit() or int(value) < 1:
            raise InvalidAction("Нужно целое число больше нуля")
    elif key == "inventory_deadline_h":
        if value and not value.isdigit():
            raise InvalidAction("Нужно целое число часов или пусто (без ограничения)")
    elif key == "club_timezone":
        try:
            ZoneInfo(value)
        except Exception:
            raise InvalidAction("Неизвестный часовой пояс (пример: Europe/Moscow)") from None
    else:
        raise KeyError(key)
    return value
```

`bot/schedule/forms.py`:
```python
import re
from dataclasses import replace
from datetime import date, time

from bot.core.errors import InvalidAction
from bot.schedule.models import TrainingKind
from bot.schedule.service import SeriesInput, TrainingEdit

KIND_BY_NAME = {"интервалы": "intervals", "длительная": "endurance", "техника": "technique"}
KIND_LABELS = {v: k.capitalize() for k, v in KIND_BY_NAME.items()}
WEEKDAY_BY_PREFIX = {
    "пн": 1, "по": 1, "вт": 2, "ср": 3, "чт": 4, "че": 4, "пт": 5, "пя": 5,
    "сб": 6, "су": 6, "вс": 7, "во": 7,
}
WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_NO = {"-", "нет", "—"}

SERIES_FIELD_PROMPTS = {
    "kind": "Тип: интервалы, длительная или техника",
    "place": "Новое место",
    "description": "Новое описание (или «-», чтобы убрать)",
    "capacity": "Лимит мест (число) или «-» без лимита",
    "weekdays": "Дни недели, например: вт чт",
    "time": "Время начала, например: 19:00",
    "end_date": "Дата окончания, например 31.12, или «-» без окончания",
}
TRAINING_FIELD_PROMPTS = {
    "place": SERIES_FIELD_PROMPTS["place"],
    "description": SERIES_FIELD_PROMPTS["description"],
    "capacity": SERIES_FIELD_PROMPTS["capacity"],
}


def parse_date(text: str, today: date) -> date:
    hint = "Введите дату как 20.10 или 20.10.2026"
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", text.strip())
    if not match:
        raise InvalidAction(hint)
    day, month = int(match[1]), int(match[2])
    try:
        if match[3]:
            return date(int(match[3]), month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:
        raise InvalidAction(hint) from None


def parse_optional_date(text: str, today: date) -> date | None:
    return None if text.strip().lower() in _NO else parse_date(text, today)


def parse_time(text: str) -> time:
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", text.strip())
    if match and int(match[1]) < 24 and int(match[2]) < 60:
        return time(int(match[1]), int(match[2]))
    raise InvalidAction("Введите время как 19:00")


def parse_weekdays(text: str) -> set[int]:
    days: set[int] = set()
    for token in re.split(r"[,\s]+", text.strip().lower()):
        if not token:
            continue
        day = WEEKDAY_BY_PREFIX.get(token[:2])
        if day is None or len(token) > 11:
            raise InvalidAction("Введите дни недели, например: вт чт")
        days.add(day)
    if not days:
        raise InvalidAction("Введите дни недели, например: вт чт")
    return days


def parse_capacity(text: str) -> int | None:
    value = text.strip().lower()
    if value in _NO:
        return None
    if not value.isdigit() or int(value) < 1:
        raise InvalidAction("Введите целое число больше нуля или «-» без лимита")
    return int(value)


def parse_kind(text: str) -> str:
    value = text.strip().lower()
    if value in KIND_BY_NAME:
        return KIND_BY_NAME[value]
    if value in TrainingKind.ALL:
        return value
    raise InvalidAction("Тип: интервалы, длительная или техника")


def build_series_input(data: dict) -> SeriesInput:
    return SeriesInput(
        kind=data["kind"],
        place=data["place"],
        level_ids=list(data["level_ids"]),
        start_date=date.fromisoformat(data["start_date"]),
        start_time=time.fromisoformat(data["start_time"]),
        weekdays=set(data.get("weekdays", [])),
        end_date=date.fromisoformat(data["end_date"]) if data.get("end_date") else None,
        capacity=data.get("capacity"),
        description=data.get("description", ""),
    )


def summarize_series(inp: SeriesInput, level_names: list[str]) -> str:
    if inp.weekdays:
        days = ", ".join(WEEKDAY_SHORT[d - 1] for d in sorted(inp.weekdays))
        when = f"каждый {days} в {inp.start_time:%H:%M}"
        if inp.end_date:
            when += f" до {inp.end_date:%d.%m.%Y}"
    else:
        when = f"{inp.start_date:%d.%m.%Y} в {inp.start_time:%H:%M}"
    lines = [
        f"Тип: {KIND_LABELS.get(inp.kind, inp.kind)}",
        f"Место: {inp.place}",
        f"Уровни: {', '.join(level_names)}",
        f"Когда: {when}",
        f"Лимит мест: {inp.capacity if inp.capacity else 'без лимита'}",
    ]
    if inp.description:
        lines.append(f"Описание: {inp.description}")
    return "\n".join(lines)


def apply_series_field(inp: SeriesInput, field: str, text: str, today: date) -> SeriesInput:
    if field == "place":
        return replace(inp, place=text.strip())
    if field == "description":
        return replace(inp, description="" if text.strip() in _NO else text.strip())
    if field == "capacity":
        return replace(inp, capacity=parse_capacity(text))
    if field == "weekdays":
        return replace(inp, weekdays=parse_weekdays(text))
    if field == "time":
        return replace(inp, start_time=parse_time(text))
    if field == "end_date":
        return replace(inp, end_date=parse_optional_date(text, today))
    if field == "kind":
        return replace(inp, kind=parse_kind(text))
    raise InvalidAction("Это поле нельзя изменить")


def apply_training_field(field: str, text: str) -> TrainingEdit:
    if field == "place":
        return TrainingEdit(place=text.strip())
    if field == "description":
        return TrainingEdit(description="" if text.strip() in _NO else text.strip())
    if field == "capacity":
        capacity = parse_capacity(text)
        return TrainingEdit(clear_capacity=True) if capacity is None else TrainingEdit(capacity=capacity)
    raise InvalidAction("Это поле нельзя изменить")
```
Примечание: в `parse_date` формат `1.1.26` отклоняется регулярным выражением (год должен быть из 4 цифр).

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/schedule/test_forms.py tests/core/test_settings.py -v` → PASS.
```bash
git add -A
git commit -m "feat(schedule): pure input parsing for trainer forms, settings validation"
```

---

### Task 7: Telegram — общая основа, доступ, выбор пользователя, диплинки

**Files:**
- Create: `bot/telegram/states.py`, `bot/telegram/ui.py`, `bot/telegram/membership.py`, `bot/telegram/handlers/picker.py`
- Modify: `bot/telegram/middleware.py` (`AccessMiddleware`), `bot/telegram/handlers/common.py`
- Test: `tests/telegram/test_ui.py`

**Interfaces:**
- Produces:
  - `markup(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup` (строка — список пар `(текст, callback_data)`); `club_tz(session) -> ZoneInfo`; `local_today(session) -> date`; `short_title(training, tz) -> str`.
  - Состояния: `NewTraining` (`kind, place, levels, weekdays, date, time, end_date, capacity, description, confirm`), `EditField.value`, `Picker.query`, `AdminInput.value`.
  - `AiogramMembership(bot)` (реализует `MembershipChecker`); `AccessMiddleware(checker, admin_ids)`.
  - Выбор пользователя: `start_picker(target, state, *, purpose, ctx, prompt, roles=None)`; декоратор `@on_pick("<purpose>")` регистрирует обработчик `async (cb, session, user, messenger, ctx, picked_id)`; `PICK_HANDLERS: dict`.
  - `/start trainer_<токен>` активирует приглашение.

- [ ] **Step 1: Написать падающий тест**

`tests/telegram/test_ui.py`:
```python
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from bot.telegram.ui import markup, short_title
from tests.factories import add_level, add_training, add_user


def test_markup_builds_callback_buttons():
    m = markup([("A", "a:1"), ("B", "b:2")], [("C", "c:3")])
    assert [[b.callback_data for b in row] for row in m.inline_keyboard] == [["a:1", "b:2"], ["c:3"]]
    assert m.inline_keyboard[1][0].text == "C"


async def test_short_title(session):
    trainer = await add_user(session, 1, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    )
    assert short_title(t, ZoneInfo("Europe/Moscow")) == "вторник, 20.10 19:00–20:30 · Интервалы"
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/telegram/test_ui.py -v` → FAIL.

- [ ] **Step 3: Реализовать основу**

`bot/telegram/ui.py`:
```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.settings import get_setting
from bot.publishing.render import KIND_NAMES, format_when
from bot.schedule.models import Training


def markup(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows]
    )


async def club_tz(session: AsyncSession) -> ZoneInfo:
    return ZoneInfo(await get_setting(session, "club_timezone"))


async def local_today(session: AsyncSession) -> date:
    return datetime.now(await club_tz(session)).date()


def short_title(training: Training, tz: ZoneInfo) -> str:
    return f"{format_when(training.starts_at, training.duration_min, tz)} · {KIND_NAMES.get(training.kind, training.kind)}"
```

`bot/telegram/states.py`:
```python
from aiogram.fsm.state import State, StatesGroup


class NewTraining(StatesGroup):
    kind = State()
    place = State()
    levels = State()
    weekdays = State()
    date = State()
    time = State()
    end_date = State()
    capacity = State()
    description = State()
    confirm = State()


class EditField(StatesGroup):
    value = State()  # data: target ("series"|"training"), id, field, text (после ввода, до подтверждения)


class Picker(StatesGroup):
    query = State()  # data: purpose, ctx, roles


class AdminInput(StatesGroup):
    value = State()  # data: purpose ("add_level" | "setting:<key>")
```

`bot/telegram/membership.py`:
```python
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError


class AiogramMembership:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def is_member(self, chat_id: int, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            return False  # бота нет в чате или чат недоступен
        if member.status in ("creator", "administrator", "member"):
            return True
        return member.status == "restricted" and bool(getattr(member, "is_member", False))
```

В `bot/telegram/middleware.py` добавить:
```python
from aiogram.types import CallbackQuery, Message

from bot.core.db import utcnow
from bot.core.messenger import MembershipChecker
from bot.users.access import DENIED_TEXT, AccessService


class AccessMiddleware(BaseMiddleware):
    """Внутренний middleware: в личке пускает только участников клуба. Кнопки в группах — доказательство членства."""

    def __init__(self, checker: MembershipChecker, admin_ids: list[int]) -> None:
        self.checker = checker
        self.admin_ids = admin_ids
        self.cache: dict = {}

    @staticmethod
    def _exempt(event) -> bool:
        text = getattr(event, "text", None) or ""
        return text.startswith("/whoami") or text.startswith("/start trainer_")

    async def __call__(self, handler, event, data):
        chat = data.get("event_chat")
        user = data.get("user")
        if chat is None or chat.type != "private" or user is None or self._exempt(event):
            return await handler(event, data)
        service = AccessService(data["session"], self.checker, self.admin_ids, self.cache)
        if await service.can_use(user.telegram_id, utcnow()):
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer(DENIED_TEXT, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(DENIED_TEXT)
```

`bot/telegram/handlers/picker.py`:
```python
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import Messenger
from bot.telegram.states import Picker
from bot.telegram.ui import markup
from bot.users.models import User
from bot.users.service import UserService

router = Router()

PickHandler = Callable[[CallbackQuery, AsyncSession, User, Messenger, dict, int], Awaitable[None]]
PICK_HANDLERS: dict[str, PickHandler] = {}


def on_pick(purpose: str):
    def decorator(func: PickHandler) -> PickHandler:
        PICK_HANDLERS[purpose] = func
        return func

    return decorator


async def start_picker(
    target: Message | CallbackQuery,
    state: FSMContext,
    *,
    purpose: str,
    ctx: dict,
    prompt: str,
    roles: set[str] | None = None,
) -> None:
    await state.clear()
    await state.set_state(Picker.query)
    await state.update_data(purpose=purpose, ctx=ctx, roles=sorted(roles) if roles else None)
    message = target.message if isinstance(target, CallbackQuery) else target
    await message.answer(f"{prompt}\nНапишите часть имени.")
    if isinstance(target, CallbackQuery):
        await target.answer()


@router.message(Picker.query, F.text)
async def on_query(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    roles = set(data["roles"]) if data.get("roles") else None
    found = await UserService(session).search(message.text, roles=roles)
    if not found:
        await message.answer("Никого не нашёл. Попробуйте другое имя.")
        return
    rows = [[(u.name, f"pk:{u.telegram_id}")] for u in found]
    rows.append([("✖️ Отмена", "pk:cancel")])
    await message.answer("Выберите:", reply_markup=markup(*rows))


@router.callback_query(Picker.query, F.data == "pk:cancel")
async def on_pick_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Отменено")
    await cb.answer()


@router.callback_query(Picker.query, F.data.startswith("pk:"))
async def on_picked(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, messenger: Messenger
) -> None:
    data = await state.get_data()
    await state.clear()
    handler = PICK_HANDLERS[data["purpose"]]
    await handler(cb, session, user, messenger, data["ctx"], int(cb.data.split(":", 1)[1]))
```

В `bot/telegram/handlers/common.py` заменить содержимое на (диплинк обязан идти до обычного `/start`):
```python
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.users.models import User
from bot.users.roles import RoleService

router = Router()


@router.message(CommandStart(deep_link=True), F.chat.type == "private")
async def cmd_start_link(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    arg = command.args or ""
    if arg.startswith("trainer_"):
        await RoleService(session).redeem_invite(arg[len("trainer_"):], user, utcnow())
        await message.answer("Вы назначены тренером. Администратор добавит вас в чат. Меню: /trainer")
        return
    await cmd_start(message, user)


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, user: User) -> None:
    await message.answer(f"Привет, {user.name}! Я бот велоклуба.\nБлижайшие тренировки: /schedule")


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")
```

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/telegram -v` → PASS.
```bash
git add -A
git commit -m "feat(telegram): ui helpers, states, access middleware, user picker, invite deep link"
```

---

### Task 8: Telegram — меню тренера (список, правка, отмена, участники)

**Files:**
- Create: `bot/telegram/handlers/trainer.py`
- Test: ручная проверка (Task 11) + покрытие логики сервисами выше

**Interfaces:**
- Consumes: `ScheduleService.managed_trainings/update_training/update_series`, `usecases.*`, `forms.*`, `picker.start_picker/on_pick`.
- Produces: роутер `trainer.router`. Callback-данные: `tr:menu`, `tr:list`, `tr:t:<id>`, `tr:cancel:<id>`, `tr:cancelok:<id>`, `tr:multi`, `tr:mt:<id>`, `tr:mgo`, `tr:mok`, `tr:edit:<id>:<field>`, `tr:series:<series_id>`, `tr:sedit:<series_id>:<field>`, `tr:sok`, `tr:who:<id>`, `tr:add:<id>`, `tr:force:<id>:<uid>`, `tr:rm:<id>:<uid>`. Команда `/trainer`. Функция `ensure_trainer(session, user)` (бросает `PermissionDenied` с текстом для тренера без чатов).

- [ ] **Step 1: Реализовать**

`bot/telegram/handlers/trainer.py`:
```python
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.messenger import Messenger
from bot.core.permissions import Role
from bot.participation.service import JoinOutcome, ParticipationService
from bot.publishing.render import render_participants
from bot.publishing.service import PublishService
from bot.schedule.forms import (
    SERIES_FIELD_PROMPTS, TRAINING_FIELD_PROMPTS, apply_series_field, apply_training_field,
)
from bot.schedule.models import Training, TrainingSeries
from bot.schedule.queries import trainer_level_ids
from bot.schedule.service import ScheduleService, series_to_input
from bot.telegram.handlers.picker import on_pick, start_picker
from bot.telegram.states import EditField
from bot.telegram.ui import club_tz, local_today, markup, short_title
from bot.usecases import (
    cancel_trainings, manual_add_participant, manual_remove_participant, update_series, update_training,
)
from bot.users.models import User

router = Router()

TRAINER_MENU = markup(
    [("📋 Мои тренировки", "tr:list")],
    [("➕ Новая серия", "tr:new:series"), ("➕ Разовая", "tr:new:one")],
    [("💬 Мои чаты", "ch:list")],
)


async def ensure_trainer(session: AsyncSession, user: User) -> None:
    if user.role == Role.ADMIN:
        return
    if user.role != Role.TRAINER:
        raise PermissionDenied("Раздел для тренеров")
    if not await trainer_level_ids(session, user.telegram_id):
        raise PermissionDenied("Вы тренер, но пока не добавлены ни в один чат. Обратитесь к администратору")


async def _get_training(session: AsyncSession, training_id: int) -> Training:
    training = await session.get(Training, training_id)
    if training is None:
        raise NotFound("Тренировка не найдена")
    return training


def _id(cb: CallbackQuery, index: int = 2) -> int:
    return int(cb.data.split(":")[index])


# ---------- меню и список ----------

@router.message(Command("trainer"), F.chat.type == "private")
async def cmd_trainer(message: Message, session: AsyncSession, user: User) -> None:
    await ensure_trainer(session, user)
    await message.answer("Меню тренера", reply_markup=TRAINER_MENU)


@router.callback_query(F.data == "tr:menu")
async def on_menu(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await ensure_trainer(session, user)
    await state.clear()
    await cb.message.edit_text("Меню тренера", reply_markup=TRAINER_MENU)
    await cb.answer()


@router.callback_query(F.data == "tr:list")
async def on_list(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await ensure_trainer(session, user)
    items = await ScheduleService(session).managed_trainings(user, utcnow())
    tz = await club_tz(session)
    rows = [[(short_title(t, tz), f"tr:t:{t.id}")] for t in items]
    if items:
        rows.append([("🗑 Отменить несколько", "tr:multi")])
    rows.append([("⬅️ Меню", "tr:menu")])
    await cb.message.edit_text("Мои тренировки" if items else "Ближайших тренировок нет", reply_markup=markup(*rows))
    await cb.answer()


@router.callback_query(F.data.startswith("tr:t:"))
async def on_detail(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    training = await _get_training(session, _id(cb))
    text, _ = await PublishService(session, messenger).render(training)
    tid = training.id
    await cb.message.edit_text(
        text,
        reply_markup=markup(
            [("🗑 Отменить", f"tr:cancel:{tid}")],
            [("✏️ Место", f"tr:edit:{tid}:place"), ("✏️ Описание", f"tr:edit:{tid}:description"),
             ("✏️ Лимит", f"tr:edit:{tid}:capacity")],
            [("👥 Участники", f"tr:who:{tid}")],
            [("🔁 Изменить серию", f"tr:series:{training.series_id}")],
            [("⬅️ Назад", "tr:list")],
        ),
    )
    await cb.answer()


# ---------- отмена ----------

@router.callback_query(F.data.startswith("tr:cancel:"))
async def on_cancel_ask(cb: CallbackQuery, session: AsyncSession) -> None:
    training = await _get_training(session, _id(cb))
    tz = await club_tz(session)
    await cb.message.edit_text(
        f"Отменить тренировку?\n{short_title(training, tz)}\nЗаписавшимся уйдёт уведомление.",
        reply_markup=markup([("🗑 Да, отменить", f"tr:cancelok:{training.id}"), ("Нет", f"tr:t:{training.id}")]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("tr:cancelok:"))
async def on_cancel_do(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    await cancel_trainings(session, messenger, user, [_id(cb)], utcnow())
    await cb.message.edit_text("Тренировка отменена", reply_markup=markup([("⬅️ К списку", "tr:list")]))
    await cb.answer()


async def _render_multi(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    selected = set((await state.get_data()).get("sel", []))
    tz = await club_tz(session)
    items = await ScheduleService(session).managed_trainings(user, utcnow(), limit=30)
    rows = [[(("✅ " if t.id in selected else "▫️ ") + short_title(t, tz), f"tr:mt:{t.id}")] for t in items]
    rows.append([(f"Отменить выбранные ({len(selected)})", "tr:mgo")])
    rows.append([("⬅️ Назад", "tr:list")])
    await cb.message.edit_text("Отметьте тренировки для отмены", reply_markup=markup(*rows))


@router.callback_query(F.data == "tr:multi")
async def on_multi(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await ensure_trainer(session, user)
    await state.clear()
    await state.update_data(sel=[])
    await _render_multi(cb, session, user, state)
    await cb.answer()


@router.callback_query(F.data.startswith("tr:mt:"))
async def on_multi_toggle(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    tid = _id(cb)
    sel = set((await state.get_data()).get("sel", []))
    sel.symmetric_difference_update({tid})
    await state.update_data(sel=sorted(sel))
    await _render_multi(cb, session, user, state)
    await cb.answer()


@router.callback_query(F.data == "tr:mgo")
async def on_multi_ask(cb: CallbackQuery, state: FSMContext) -> None:
    sel = (await state.get_data()).get("sel", [])
    if not sel:
        await cb.answer("Ничего не выбрано", show_alert=True)
        return
    await cb.message.edit_text(
        f"Отменить тренировок: {len(sel)}? Записавшимся уйдут уведомления.",
        reply_markup=markup([("🗑 Да, отменить", "tr:mok"), ("Нет", "tr:multi")]),
    )
    await cb.answer()


@router.callback_query(F.data == "tr:mok")
async def on_multi_do(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, state: FSMContext) -> None:
    sel = (await state.get_data()).get("sel", [])
    await state.clear()
    await cancel_trainings(session, messenger, user, list(sel), utcnow())  # всё или ничего
    await cb.message.edit_text(f"Отменено тренировок: {len(sel)}", reply_markup=markup([("⬅️ К списку", "tr:list")]))
    await cb.answer()


# ---------- правка одной тренировки ----------

@router.callback_query(F.data.startswith("tr:edit:"))
async def on_edit_training(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, tid, field = cb.data.split(":")
    await state.clear()
    await state.set_state(EditField.value)
    await state.update_data(target="training", id=int(tid), field=field)
    await cb.message.answer(TRAINING_FIELD_PROMPTS[field])
    await cb.answer()


# ---------- правка серии ----------

@router.callback_query(F.data.startswith("tr:series:"))
async def on_series_menu(cb: CallbackQuery) -> None:
    sid = _id(cb)
    labels = {"kind": "Тип", "place": "Место", "description": "Описание", "capacity": "Лимит",
              "weekdays": "Дни", "time": "Время", "end_date": "Окончание"}
    rows = [[(f"✏️ {label}", f"tr:sedit:{sid}:{field}")] for field, label in labels.items()]
    rows.append([("⬅️ Назад", "tr:list")])
    await cb.message.edit_text(
        "Изменение серии применится ко всем будущим тренировкам. Уже опубликованные будут отменены "
        "и заменены новыми, записи придётся оформить заново.",
        reply_markup=markup(*rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("tr:sedit:"))
async def on_edit_series(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, sid, field = cb.data.split(":")
    await state.clear()
    await state.set_state(EditField.value)
    await state.update_data(target="series", id=int(sid), field=field)
    await cb.message.answer(SERIES_FIELD_PROMPTS[field])
    await cb.answer()


@router.message(EditField.value, F.text)
async def on_edit_value(
    message: Message, state: FSMContext, session: AsyncSession, user: User, messenger: Messenger
) -> None:
    data = await state.get_data()
    now = utcnow()
    if data["target"] == "training":
        edit = apply_training_field(data["field"], message.text)  # ошибка ввода: состояние остаётся, можно повторить
        await update_training(session, messenger, user, data["id"], edit, now)
        await state.clear()
        await message.answer("Сохранено ✅", reply_markup=markup([("⬅️ К списку", "tr:list")]))
        return
    series = await session.get(TrainingSeries, data["id"])
    if series is None:
        raise NotFound("Серия не найдена")
    apply_series_field(series_to_input(series), data["field"], message.text, await local_today(session))  # проверка ввода
    await state.update_data(text=message.text)
    await message.answer(
        "Применить к будущим тренировкам серии? Опубликованные будут отменены и заменены.",
        reply_markup=markup([("✅ Применить", "tr:sok"), ("✖️ Отмена", "tr:menu")]),
    )


@router.callback_query(EditField.value, F.data == "tr:sok")
async def on_series_confirm(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, messenger: Messenger) -> None:
    data = await state.get_data()
    series = await session.get(TrainingSeries, data["id"])
    if series is None or "text" not in data:
        raise InvalidAction("Сначала введите значение")
    new = apply_series_field(series_to_input(series), data["field"], data["text"], await local_today(session))
    replaced = await update_series(session, messenger, user, series.id, new, utcnow())
    await state.clear()
    await cb.message.edit_text(
        f"Серия обновлена ✅ Заменено опубликованных тренировок: {len(replaced)}",
        reply_markup=markup([("⬅️ К списку", "tr:list")]),
    )
    await cb.answer()


# ---------- участники ----------

async def _show_participants(cb: CallbackQuery, session: AsyncSession, training_id: int) -> None:
    going, declined = await ParticipationService(session).list_participants(training_id)
    rows = [[(f"❌ {u.name}", f"tr:rm:{training_id}:{u.telegram_id}")] for u in going]
    rows.append([("➕ Записать участника", f"tr:add:{training_id}")])
    rows.append([("⬅️ Назад", f"tr:t:{training_id}")])
    text = render_participants([u.name for u in going], [u.name for u in declined])
    await cb.message.edit_text(text + "\n\nНажмите на имя, чтобы убрать из списка.", reply_markup=markup(*rows))


@router.callback_query(F.data.startswith("tr:who:"))
async def on_who(cb: CallbackQuery, session: AsyncSession) -> None:
    await _show_participants(cb, session, _id(cb))
    await cb.answer()


@router.callback_query(F.data.startswith("tr:rm:"))
async def on_remove(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    training_id, user_id = _id(cb), _id(cb, 3)
    await manual_remove_participant(session, messenger, user, training_id, user_id, utcnow())
    await _show_participants(cb, session, training_id)
    await cb.answer("Убран из списка")


@router.callback_query(F.data.startswith("tr:add:"))
async def on_add(cb: CallbackQuery, state: FSMContext) -> None:
    await start_picker(cb, state, purpose="manual_add", ctx={"training_id": _id(cb)}, prompt="Кого записать?")


@on_pick("manual_add")
async def picked_for_manual_add(cb, session, user, messenger, ctx, picked_id) -> None:
    training_id = ctx["training_id"]
    outcome = await manual_add_participant(session, messenger, user, training_id, picked_id, utcnow())
    if outcome == JoinOutcome.OVER_LIMIT:
        await cb.message.edit_text(
            "⚠️ Лимит превышен. Записать всё равно?",
            reply_markup=markup([("Да, записать", f"tr:force:{training_id}:{picked_id}"), ("Нет", f"tr:who:{training_id}")]),
        )
    else:
        await cb.message.edit_text(
            "Уже записан" if outcome == JoinOutcome.ALREADY else "Записан ✅",
            reply_markup=markup([("⬅️ К участникам", f"tr:who:{training_id}")]),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("tr:force:"))
async def on_force(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    training_id, user_id = _id(cb), _id(cb, 3)
    await manual_add_participant(session, messenger, user, training_id, user_id, utcnow(), allow_over_limit=True)
    await _show_participants(cb, session, training_id)
    await cb.answer("Записан сверх лимита")
```

Примечание: в `on_force`/`on_remove` права проверяет сервис (`PermissionDenied` превращается в алерт глобальным обработчиком).

- [ ] **Step 2: Проверить импорты и закоммитить**

Run: `python -c "import bot.telegram.handlers.trainer"` → без ошибок.
```bash
git add -A
git commit -m "feat(telegram): trainer menu — list, cancel (multi), edit, participants"
```

---

### Task 9: Telegram — форма новой серии / разовой тренировки

**Files:**
- Create: `bot/telegram/handlers/trainer_forms.py`

**Interfaces:**
- Consumes: `NewTraining`, `forms.*`, `ScheduleService.allowed_levels/create_series`.
- Produces: роутер `trainer_forms.router`. Данные FSM: `one_off: bool`, `kind`, `place`, `level_ids: list[int]`, `weekdays: list[int]`, `start_date` (ISO), `start_time` (`HH:MM`), `end_date` (ISO | None), `capacity`, `description`. Для регулярной серии `start_date` = сегодня по времени клуба. Ошибка ввода в любом шаге — вежливый ответ (глобальный обработчик), состояние остаётся.

- [ ] **Step 1: Реализовать**

`bot/telegram/handlers/trainer_forms.py`:
```python
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.schedule.forms import (
    build_series_input, parse_capacity, parse_date, parse_kind, parse_optional_date, parse_time,
    parse_weekdays, summarize_series,
)
from bot.schedule.models import Level
from bot.schedule.service import ScheduleService
from bot.telegram.handlers.trainer import ensure_trainer
from bot.telegram.states import NewTraining
from bot.telegram.ui import local_today, markup
from bot.users.models import User
from sqlalchemy import select

router = Router()

KIND_ROW = [("Интервалы", "nf:kind:intervals"), ("Длительная", "nf:kind:endurance"), ("Техника", "nf:kind:technique")]


@router.callback_query(F.data.in_({"tr:new:series", "tr:new:one"}))
async def on_new(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await ensure_trainer(session, user)
    await state.clear()
    await state.update_data(one_off=cb.data.endswith(":one"), level_ids=[])
    await state.set_state(NewTraining.kind)
    await cb.message.edit_text("Тип тренировки:", reply_markup=markup(KIND_ROW, [("✖️ Отмена", "nf:no")]))
    await cb.answer()


@router.callback_query(NewTraining.kind, F.data.startswith("nf:kind:"))
async def on_kind(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(kind=parse_kind(cb.data.split(":")[2]))
    await state.set_state(NewTraining.place)
    await cb.message.edit_text("Где проходит тренировка? Напишите место.")
    await cb.answer()


@router.message(NewTraining.place, F.text)
async def on_place(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    place = message.text.strip()
    if not place:
        await message.answer("Напишите место текстом")
        return
    await state.update_data(place=place)
    await state.set_state(NewTraining.levels)
    await message.answer("Для каких уровней? Отметьте и нажмите «Готово».", reply_markup=await _levels_markup(session, user, []))


async def _levels_markup(session: AsyncSession, user: User, selected: list[int]):
    levels = await ScheduleService(session).allowed_levels(user)
    rows = [[(("✅ " if lvl.id in selected else "▫️ ") + lvl.name, f"nf:lvl:{lvl.id}")] for lvl in levels]
    rows.append([("Готово", "nf:lvldone")])
    return markup(*rows)


@router.callback_query(NewTraining.levels, F.data.startswith("nf:lvl:"))
async def on_level_toggle(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    level_id = int(cb.data.split(":")[2])
    selected = set((await state.get_data())["level_ids"])
    selected.symmetric_difference_update({level_id})
    await state.update_data(level_ids=sorted(selected))
    await cb.message.edit_reply_markup(reply_markup=await _levels_markup(session, user, sorted(selected)))
    await cb.answer()


@router.callback_query(NewTraining.levels, F.data == "nf:lvldone")
async def on_levels_done(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data["level_ids"]:
        await cb.answer("Выберите хотя бы один уровень", show_alert=True)
        return
    if data["one_off"]:
        await state.set_state(NewTraining.date)
        await cb.message.answer("Дата тренировки, например 20.10:")
    else:
        await state.set_state(NewTraining.weekdays)
        await cb.message.answer("Дни недели, например: вт чт")
    await cb.answer()


@router.message(NewTraining.weekdays, F.text)
async def on_weekdays(message: Message, state: FSMContext, session: AsyncSession) -> None:
    days = parse_weekdays(message.text)
    await state.update_data(weekdays=sorted(days), start_date=(await local_today(session)).isoformat())
    await state.set_state(NewTraining.time)
    await message.answer("Время начала, например 19:00:")


@router.message(NewTraining.date, F.text)
async def on_date(message: Message, state: FSMContext, session: AsyncSession) -> None:
    day = parse_date(message.text, await local_today(session))
    await state.update_data(weekdays=[], start_date=day.isoformat())
    await state.set_state(NewTraining.time)
    await message.answer("Время начала, например 19:00:")


@router.message(NewTraining.time, F.text)
async def on_time(message: Message, state: FSMContext) -> None:
    await state.update_data(start_time=parse_time(message.text).strftime("%H:%M"))
    if (await state.get_data())["one_off"]:
        await state.set_state(NewTraining.capacity)
        await message.answer("Лимит мест (число) или «-» без лимита:")
    else:
        await state.set_state(NewTraining.end_date)
        await message.answer("Дата окончания серии, например 31.12, или «-» без окончания:")


@router.message(NewTraining.end_date, F.text)
async def on_end_date(message: Message, state: FSMContext, session: AsyncSession) -> None:
    end = parse_optional_date(message.text, await local_today(session))
    await state.update_data(end_date=end.isoformat() if end else None)
    await state.set_state(NewTraining.capacity)
    await message.answer("Лимит мест (число) или «-» без лимита:")


@router.message(NewTraining.capacity, F.text)
async def on_capacity(message: Message, state: FSMContext) -> None:
    await state.update_data(capacity=parse_capacity(message.text))
    await state.set_state(NewTraining.description)
    await message.answer("Описание (или «-»):")


@router.message(NewTraining.description, F.text)
async def on_description(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = message.text.strip()
    await state.update_data(description="" if text in ("-", "—") else text)
    data = await state.get_data()
    names = list(await session.scalars(select(Level.name).where(Level.id.in_(data["level_ids"])).order_by(Level.position)))
    await state.set_state(NewTraining.confirm)
    await message.answer(
        "Проверьте:\n" + summarize_series(build_series_input(data), names),
        reply_markup=markup([("✅ Создать", "nf:ok"), ("✖️ Отмена", "nf:no")]),
    )


@router.callback_query(NewTraining.confirm, F.data == "nf:ok")
async def on_confirm(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    await ScheduleService(session).create_series(user, build_series_input(data), utcnow())
    await state.clear()
    await cb.message.edit_text("Готово ✅ Пост появится в чатах за 24 часа до старта. Расписание: /schedule")
    await cb.answer()


@router.callback_query(F.data == "nf:no")
async def on_cancel_form(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Отменено")
    await cb.answer()
```
Ограничение: `nf:no` и `nf:ok` обрабатываются только в своих состояниях/без состояния; повторное нажатие после завершения безвредно.

- [ ] **Step 2: Проверить импорты и закоммитить**

Run: `python -c "import bot.telegram.handlers.trainer_forms"` → без ошибок.
```bash
git add -A
git commit -m "feat(telegram): new series / one-off training form"
```

---

### Task 10: Telegram — чаты и тренеры чатов, админ-меню, запуск

**Files:**
- Create: `bot/telegram/handlers/chats.py`
- Modify: `bot/telegram/handlers/admin.py`, `bot/main.py`

**Interfaces:**
- Consumes: `RoleService`, `UserService.search`, `picker`, `validate_setting`, `set_setting`.
- Produces: callback-данные `ch:list`, `ch:c:<chat_id>`, `ch:rm:<chat_id>:<uid>`, `ch:lead:<chat_id>:<uid>`, `ch:add:<chat_id>`; админ: `/admin`, `ad:menu`, `ad:trainers`, `ad:rt:<uid>`, `ad:addt`, `ad:admins`, `ad:ra:<uid>`, `ad:adda`, `ad:levels`, `ad:addlvl`, `ad:invite`, `ad:settings`, `ad:set:<key>`. Основной `main.py` с `AccessMiddleware`, `admin_ids` в workflow-данных и всеми роутерами.

- [ ] **Step 1: Экран чатов (для админа и главных тренеров)**

`bot/telegram/handlers/chats.py`:
```python
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import Messenger
from bot.core.permissions import Role
from bot.schedule.models import Chat
from bot.telegram.handlers.picker import on_pick, start_picker
from bot.telegram.ui import markup
from bot.users.models import User
from bot.users.roles import RoleService

router = Router()


async def _show_chat(cb: CallbackQuery, session: AsyncSession, user: User, chat_id: int) -> None:
    svc = RoleService(session)
    chat = await session.get(Chat, chat_id)
    rows = []
    lines = [f"Чат «{chat.title}»", "Тренеры:"]
    for trainer, is_lead in await svc.chat_trainers(chat_id):
        lines.append(f"{'⭐ ' if is_lead else '• '}{trainer.name}")
        row = [(f"❌ {trainer.name}", f"ch:rm:{chat_id}:{trainer.telegram_id}")]
        if user.role == Role.ADMIN and not is_lead:
            row.append(("⭐ Главным", f"ch:lead:{chat_id}:{trainer.telegram_id}"))
        rows.append(row)
    if len(lines) == 2:
        lines.append("пока нет")
    rows.append([("➕ Добавить тренера", f"ch:add:{chat_id}")])
    rows.append([("⬅️ К чатам", "ch:list")])
    await cb.message.edit_text("\n".join(lines), reply_markup=markup(*rows))


@router.callback_query(F.data == "ch:list")
async def on_list(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    chats = await RoleService(session).manageable_chats(user)
    if not chats:
        await cb.message.edit_text("Нет чатов под вашим управлением", reply_markup=markup([("⬅️ Меню", "tr:menu")]))
    else:
        rows = [[(c.title, f"ch:c:{c.chat_id}")] for c in chats]
        rows.append([("⬅️ Меню", "tr:menu")])
        await cb.message.edit_text("Чаты:", reply_markup=markup(*rows))
    await cb.answer()


@router.callback_query(F.data.startswith("ch:c:"))
async def on_chat(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await _show_chat(cb, session, user, int(cb.data.split(":")[2]))
    await cb.answer()


@router.callback_query(F.data.startswith("ch:rm:"))
async def on_remove(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    _, _, chat_id, uid = cb.data.split(":")
    await RoleService(session).remove_chat_trainer(user, int(chat_id), int(uid))
    await _show_chat(cb, session, user, int(chat_id))
    await cb.answer("Убран")


@router.callback_query(F.data.startswith("ch:lead:"))
async def on_lead(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    _, _, chat_id, uid = cb.data.split(":")
    await RoleService(session).set_lead(user, int(chat_id), int(uid))
    await _show_chat(cb, session, user, int(chat_id))
    await cb.answer("Назначен главным")


@router.callback_query(F.data.startswith("ch:add:"))
async def on_add(cb: CallbackQuery, state: FSMContext) -> None:
    await start_picker(
        cb, state, purpose="chat_trainer_add", ctx={"chat_id": int(cb.data.split(":")[2])},
        prompt="Кого добавить тренером чата? (человек уже должен быть тренером)", roles={Role.TRAINER},
    )


@on_pick("chat_trainer_add")
async def picked(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, ctx: dict, picked_id: int) -> None:
    await RoleService(session).add_chat_trainer(user, ctx["chat_id"], picked_id)
    await _show_chat(cb, session, user, ctx["chat_id"])
    await cb.answer("Добавлен")
```

- [ ] **Step 2: Админ-меню**

Заменить `bot/telegram/handlers/admin.py` целиком (команды `/add_level`, `/bind_level` сохраняются):
```python
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.errors import InvalidAction
from bot.core.messenger import Messenger
from bot.core.permissions import Role, require_admin
from bot.core.settings import DEFAULTS, get_setting, set_setting, validate_setting
from bot.schedule.models import Level
from bot.schedule.service import ScheduleService
from bot.telegram.handlers.picker import on_pick, start_picker
from bot.telegram.states import AdminInput
from bot.telegram.ui import markup
from bot.users.models import User
from bot.users.roles import RoleService

router = Router()

ADMIN_MENU = markup(
    [("👥 Тренеры", "ad:trainers"), ("🛡 Админы", "ad:admins")],
    [("🎚 Уровни", "ad:levels"), ("💬 Чаты", "ch:list")],
    [("🔗 Приглашение тренера", "ad:invite"), ("⚙️ Настройки", "ad:settings")],
)
SETTING_LABELS = {
    "publish_window_h": "За сколько часов публиковать пост",
    "reminder_h": "За сколько часов напоминать в личку",
    "duration_min": "Длительность по умолчанию, мин",
    "club_timezone": "Часовой пояс клуба",
}


@router.message(Command("add_level"))
async def cmd_add_level(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    if not command.args:
        raise InvalidAction("Использование: /add_level Название уровня")
    level = await ScheduleService(session).add_level(user, command.args)
    await message.answer(f"Уровень «{level.name}» добавлен")


@router.message(Command("bind_level"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_bind_level(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    if not command.args:
        raise InvalidAction("Использование: /bind_level Название уровня")
    await ScheduleService(session).bind_chat(user, message.chat.id, message.chat.title or "", command.args)
    await message.answer(f"Чат привязан к уровню «{command.args.strip()}»")


@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message, user: User) -> None:
    require_admin(user)
    await message.answer("Меню администратора", reply_markup=ADMIN_MENU)


@router.callback_query(F.data == "ad:menu")
async def on_menu(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    require_admin(user)
    await state.clear()
    await cb.message.edit_text("Меню администратора", reply_markup=ADMIN_MENU)
    await cb.answer()


# ---------- тренеры ----------

async def _show_trainers(cb: CallbackQuery, session: AsyncSession) -> None:
    trainers = await RoleService(session).list_by_role(Role.TRAINER)
    rows = [[(f"❌ {t.name}", f"ad:rt:{t.telegram_id}")] for t in trainers]
    rows.append([("➕ Назначить тренера", "ad:addt")])
    rows.append([("⬅️ Меню", "ad:menu")])
    await cb.message.edit_text("Тренеры (нажмите, чтобы снять роль):" if trainers else "Тренеров пока нет", reply_markup=markup(*rows))


@router.callback_query(F.data == "ad:trainers")
async def on_trainers(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await _show_trainers(cb, session)
    await cb.answer()


@router.callback_query(F.data.startswith("ad:rt:"))
async def on_revoke_trainer(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await RoleService(session).revoke_trainer(user, int(cb.data.split(":")[2]))
    await _show_trainers(cb, session)
    await cb.answer("Роль снята")


@router.callback_query(F.data == "ad:addt")
async def on_add_trainer(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    await start_picker(cb, state, purpose="grant_trainer", ctx={}, prompt="Кого назначить тренером?", roles={Role.MEMBER})


@on_pick("grant_trainer")
async def picked_trainer(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, ctx: dict, picked_id: int) -> None:
    await RoleService(session).grant_trainer(user, picked_id)
    await _show_trainers(cb, session)
    await cb.answer("Назначен. Добавьте его в чат: 💬 Чаты")


# ---------- админы ----------

async def _show_admins(cb: CallbackQuery, session: AsyncSession) -> None:
    admins = await RoleService(session).list_by_role(Role.ADMIN)
    rows = [[(f"❌ {a.name}", f"ad:ra:{a.telegram_id}")] for a in admins]
    rows.append([("➕ Назначить админа", "ad:adda")])
    rows.append([("⬅️ Меню", "ad:menu")])
    await cb.message.edit_text("Администраторы (нажмите, чтобы снять):", reply_markup=markup(*rows))


@router.callback_query(F.data == "ad:admins")
async def on_admins(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await _show_admins(cb, session)
    await cb.answer()


@router.callback_query(F.data.startswith("ad:ra:"))
async def on_revoke_admin(cb: CallbackQuery, session: AsyncSession, user: User, admin_ids: list[int]) -> None:
    await RoleService(session, admin_ids).revoke_admin(user, int(cb.data.split(":")[2]))
    await _show_admins(cb, session)
    await cb.answer("Роль снята")


@router.callback_query(F.data == "ad:adda")
async def on_add_admin(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    await start_picker(cb, state, purpose="grant_admin", ctx={}, prompt="Кого назначить администратором?", roles={Role.MEMBER, Role.TRAINER})


@on_pick("grant_admin")
async def picked_admin(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, ctx: dict, picked_id: int) -> None:
    await RoleService(session).grant_admin(user, picked_id)
    await _show_admins(cb, session)
    await cb.answer("Назначен")


# ---------- уровни ----------

@router.callback_query(F.data == "ad:levels")
async def on_levels(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    names = list(await session.scalars(select(Level.name).order_by(Level.position, Level.id)))
    await cb.message.edit_text(
        "Уровни:\n" + ("\n".join(f"• {n}" for n in names) or "пока нет"),
        reply_markup=markup([("➕ Добавить уровень", "ad:addlvl")], [("⬅️ Меню", "ad:menu")]),
    )
    await cb.answer()


@router.callback_query(F.data == "ad:addlvl")
async def on_add_level(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    await state.clear()
    await state.set_state(AdminInput.value)
    await state.update_data(purpose="add_level")
    await cb.message.answer("Название нового уровня:")
    await cb.answer()


# ---------- приглашение ----------

@router.callback_query(F.data == "ad:invite")
async def on_invite(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    invite = await RoleService(session).create_invite(user, utcnow())
    username = (await cb.bot.me()).username
    await cb.message.answer(
        "Одноразовая ссылка для тренера (действует 24 часа):\n"
        f"https://t.me/{username}?start=trainer_{invite.token}"
    )
    await cb.answer()


# ---------- настройки ----------

@router.callback_query(F.data == "ad:settings")
async def on_settings(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    rows = []
    for key, label in SETTING_LABELS.items():
        rows.append([(f"{label}: {await get_setting(session, key)}", f"ad:set:{key}")])
    rows.append([("⬅️ Меню", "ad:menu")])
    await cb.message.edit_text("Настройки (нажмите, чтобы изменить):", reply_markup=markup(*rows))
    await cb.answer()


@router.callback_query(F.data.startswith("ad:set:"))
async def on_setting(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    key = cb.data.split(":", 2)[2]
    if key not in DEFAULTS:
        raise InvalidAction("Неизвестная настройка")
    await state.clear()
    await state.set_state(AdminInput.value)
    await state.update_data(purpose=f"setting:{key}")
    await cb.message.answer(f"{SETTING_LABELS.get(key, key)} — новое значение:")
    await cb.answer()


@router.message(AdminInput.value, F.text)
async def on_admin_input(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    require_admin(user)
    purpose = (await state.get_data())["purpose"]
    if purpose == "add_level":
        level = await ScheduleService(session).add_level(user, message.text)
        await message.answer(f"Уровень «{level.name}» добавлен. Привяжите чат: /bind_level {level.name}")
    else:
        key = purpose.split(":", 1)[1]
        await set_setting(session, key, validate_setting(key, message.text))  # при ошибке состояние остаётся
        await message.answer("Сохранено ✅")
    await state.clear()
```

- [ ] **Step 3: Обновить `bot/main.py`**

Заменить целиком:
```python
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import ExceptionTypeFilter

from bot.bootstrap import bootstrap
from bot.core.config import Config
from bot.core.db import make_engine, make_session_factory, utcnow
from bot.core.errors import DomainError
from bot.jobs.runner import scheduler_loop
from bot.telegram.errors import on_domain_error
from bot.telegram.handlers import admin, chats, common, participation, picker, schedule, trainer, trainer_forms
from bot.telegram.membership import AiogramMembership
from bot.telegram.messenger import AiogramMessenger
from bot.telegram.middleware import AccessMiddleware, DbSessionMiddleware


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = Config()
    engine = make_engine(cfg.database_url)
    session_factory = make_session_factory(engine)
    await bootstrap(session_factory, cfg, utcnow())

    bot = Bot(cfg.bot_token)
    messenger = AiogramMessenger(bot)
    access = AccessMiddleware(AiogramMembership(bot), cfg.admin_ids)

    dp = Dispatcher()
    dp.update.outer_middleware(DbSessionMiddleware(session_factory, cfg.admin_ids))
    dp.message.middleware(access)
    dp.callback_query.middleware(access)
    dp.errors.register(on_domain_error, ExceptionTypeFilter(DomainError))
    dp.include_routers(
        common.router, admin.router, chats.router, picker.router, trainer_forms.router,
        trainer.router, schedule.router, participation.router,
    )

    scheduler = asyncio.create_task(scheduler_loop(session_factory, messenger))
    try:
        await dp.start_polling(bot, messenger=messenger, admin_ids=cfg.admin_ids)
    finally:
        scheduler.cancel()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```
Порядок роутеров важен: состояния FSM (`trainer_forms`, `picker`, `admin` с `AdminInput`) должны сработать раньше общих обработчиков; текстовые хендлеры с фильтром состояния не пересекаются с командами.

- [ ] **Step 4: Прогнать полный набор и закоммитить**

Run: `python -c "import bot.main"` → без ошибок; затем `python -m pytest -v` → всё PASS.
```bash
git add -A
git commit -m "feat(telegram): chats screen, admin menu, settings, wiring"
```

---

### Task 11: Ручная проверка

- [ ] **Step 1: Подготовка.** `alembic upgrade head`, `python -m bot.main` с реальным токеном; в `.env` ваш ID в `ADMIN_IDS`. Тестовая группа с ботом и ещё один Telegram-аккаунт.
- [ ] **Step 2: Роли.** `/admin` → «Приглашение тренера» → открыть ссылку со второго аккаунта: «Вы назначены тренером». Тот же аккаунт повторно: «Приглашение уже использовано». `/trainer` у второго аккаунта: «Вы тренер, но пока не добавлены ни в один чат».
- [ ] **Step 3: Чаты.** В группе `/bind_level Новички` (после `/add_level Новички` или «🎚 Уровни → Добавить»). `/admin` → «💬 Чаты» → чат → «➕ Добавить тренера» → найти второго по имени: он становится главным (⭐). Добавить третьего тренера — он обычный. «⭐ Главным» переносит звезду.
- [ ] **Step 4: Создание.** Со второго аккаунта `/trainer` → «Разовая»: ввести тип, место, уровень, дату `31.02` (ожидается подсказка формата, состояние остаётся), затем корректную дату, время `25:00` (ошибка), `19:00`, лимит `1`, описание `-`, «Создать». `/schedule` показывает карточку с «Откроется …». Если старт < 24 ч — пост в группе появится в течение 30 с.
- [ ] **Step 5: Запись и ручная запись.** Записаться кнопкой; затем `/trainer` → тренировка → «👥 Участники» → «➕ Записать» → выбрать другого человека: «Лимит превышен. Записать всё равно?» → «Да»: счётчик «Идут: 2/1», записанный получил личное сообщение (если запускал бота).
- [ ] **Step 6: Правка.** «✏️ Лимит» → `1` при двух записанных: ошибка «Уже записано 2». «🔁 Изменить серию» → «Место» → новое значение → «Применить»: старый пост «Отменена», записанным пришло «Тренировка изменена…» со ссылкой на новый пост.
- [ ] **Step 7: Отмена нескольких.** «🗑 Отменить несколько» → отметить две → подтвердить: оба поста «Отменена». Попытка отменить прошедшую тренировку в списке: её там нет.
- [ ] **Step 8: Доступ.** Третий аккаунт, не состоящий ни в одном привязанном чате: `/start`, `/schedule` → «Бот доступен участникам клуба»; `/whoami` работает.
- [ ] **Step 9: Закоммитить заметки, если что-то поправили.**

---

## Self-Review

**Покрытие спецификации (область плана 2):**
- Роли, `ADMIN_IDS` нельзя снять, админов назначает админ из списка, приглашения 24 ч → Задачи 1, 2, 7, 10.
- Тренеры чатов, главный (один, обязателен, снимается только с заменой, назначает админ, главный управляет остальными) → Задача 2, UI в Задаче 10.
- Тренер без чатов: сообщение и запрет создавать → Задачи 3 (сервис), 8 (`ensure_trainer`).
- Права на тренировки (все целевые чаты под началом главного) → план 1, используются в Задачах 3, 4.
- Правка серии (пересоздание неопубликованных, замена опубликованных с уведомлением и ссылкой, начатые не трогаем), правка одной тренировки → Задачи 1, 3, 4.
- Отмена нескольких тренировок с подтверждением → Задача 8 (сервис `cancel` — план 1).
- Ручная запись тренером с предупреждением о лимите, `added_by`, уведомление → Задачи 4, 8.
- Доступ через `getChatMember` с кэшем, исключения для админов, кнопки в группе не проверяются → Задачи 5, 7.
- UX: `/trainer` (мои тренировки, форма серии/разовой, изменить, отменить, участники), `/admin` (тренеры, админы, приглашения, уровни, чаты, настройки) → Задачи 8–10. «Парк инвентаря» и «сводка по инвентарю» — план 3.
- Тесты: сервисы, права, роли, приглашения → Задачи 2–6; формы/парсинг → Задача 6; интеграционные сценарии на хендлерах заменены usecase-тестами с фейковым Messenger и ручной проверкой.

**Сканирование на заглушки:** TBD/TODO нет. Компактный черновик результата в Задаче 3 заменён развёрнутым кодом.

**Согласованность типов:** `ReplacedTraining(old_id, new_id)`, `TrainingEdit`, `series_to_input`, `JoinOutcome.OVER_LIMIT`, `manual_add/manual_remove`, `RoleService` — одинаковы в Задачах 3, 4, 8, 10. Callback-префиксы `tr:`, `ch:`, `ad:`, `nf:`, `pk:` не пересекаются с `join:/decline:/who:/noop` плана 1.

**Review Focus:** п.1 — Задачи 1, 3 (тест замены места); п.2 — Задача 3; п.3 — Задачи 3, 4; п.4 — Задача 4 (usecase); п.5 — Задача 6.
