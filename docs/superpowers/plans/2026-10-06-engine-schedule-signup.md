# Coach's Shadow — Plan 1: Engine, Schedule, Publishing, Sign-up

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A running Telegram bot that generates trainings from series, publishes posts into level chats 24 h before start, accepts «Пойду / Не пойду» with a seat limit, sends 2 h reminders, handles cancellation and closes finished trainings.

**Architecture:** One process: aiogram long polling + a 30-second scheduler loop. Code is split by feature (`users`, `schedule`, `participation`, `publishing`, `jobs`); services take an `AsyncSession` directly (no repo layer) and talk to Telegram only through the `Messenger` protocol. Operations that span features live in `bot/usecases.py`; `bot/telegram/` is the only package that imports aiogram.

**Tech Stack:** Python 3.11+, aiogram 3, SQLAlchemy 2 (async) + aiosqlite, Alembic, pydantic-settings, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-06-coachs-shadow-design.md`

## Scope of this plan (1 of 3)

The spec covers three independent subsystems; each gets its own plan and produces working software.

| Plan | Content |
|---|---|
| **1 (this)** | Foundation, data model, migrations, schedule generation, publishing, sign-up, reminders, cancellation (service level), jobs, minimal Telegram layer (`/start`, `/whoami`, `/schedule`, buttons, `/add_level`, `/bind_level`) |
| 2 | Roles, invites, chat trainers, `/trainer` and `/admin` FSM UI (create series/one-off, edit series with re-publish, cancel UI, manual add over limit), access control via `getChatMember` |
| 3 | Inventory: models, `matching.py`, `InventoryService`, ordering flow, trainer summary, park admin, `/profile`, `/my`; hooks into decline/cancel/series edit |

Until Plan 2 lands, trainings are created through `ScheduleService` (see the seed script in Task 13) — there is no Telegram form for it yet.

## Global Constraints

- Interface language: Russian (all user-visible strings).
- Stack: Python, aiogram 3, SQLAlchemy 2 async, Alembic, SQLite (later PostgreSQL). Single process, long polling.
- `starts_at` is stored in UTC. Recurrence rules («вт и чт в 19:00») are evaluated in `club_timezone` (IANA, default `Europe/Moscow`), and the UI shows time in that zone.
- Features depend on `core`; `telegram/` depends on features; features never import aiogram or `telegram/`.
- No `repo/` layer: services use the async session directly; complex queries are named functions next to the service.
- Telegram is reached only through `core/messenger.py` (`send`, `edit_post`, `notify`); tests use a fake.
- Permissions are policy functions in `core/permissions.py`, called by the services themselves inside the operation.
- Publish window 24 h before start; DM reminder 2 h before start; `duration_min` default 90; scheduler tick every 30 s; generation horizon 4 weeks; generation job runs once a day.
- Post = reminder at 24 h; no separate 24 h notification.
- Tests mirror `bot/` under `tests/`; SQLite is used for service tests.
- Not in MVP: waitlist, inventory queue, payments, issue/return marks, attendance stats, `.ics`, weather/map, Mini App, subscriptions.

## Deliberate deviations from the spec (small, additive)

- `User.started_bot` (bool): the spec needs «запускал ли бота» for DM notifications but lists no field.
- `ScheduledJob.training_id` (nullable FK) replaces the generic `payload`: every job except `generate` targets one training, and a real column lets cancellation remove jobs with a plain `UPDATE`.
- Rights for a lead trainer are evaluated over the chats the training **targets** (chats whose level is in the training's levels), so the rule also works before publication. For a published training this equals the set of chats with posts.
- Service tests use a SQLite **temp file** instead of `:memory:` because the scheduler opens several sessions at once.

## Review Focus

Input classes the spec implies but no obvious task covers; each has a pinning test in the owning task.

1. **Join at full capacity by someone already going / by someone who declined** — already-going stays idempotent (not «Мест нет»); a decliner at a full training gets «Мест нет» and stays `declined` (Task 8).
2. **Generation run twice, or after a training was cancelled** — no duplicates; the cancelled training is not resurrected (Task 7).
3. **Lead trainer on a training spanning two chats where he leads only one** — must be denied; leading all target chats allows (Task 7).
4. **Very long participant list** — Telegram alerts cap at 200 chars; the text must be trimmed or sent to DM, never raise (Task 9).
5. **Recipient never started the bot** — reminders/cancel notices return `False` from `notify` and must not fail the job or the post update (Task 10).

---

## File Structure

```
pyproject.toml, alembic.ini, .env.example, .gitignore
alembic/                       # env.py (async), versions/
bot/
  registry.py                  # imports every models module (Alembic + tests)
  usecases.py                  # cross-feature operations (join/decline/cancel)
  bootstrap.py                 # startup: admins, timezone setting, generate job
  main.py
  core/ config.py db.py errors.py messenger.py permissions.py settings.py
  users/ models.py service.py                 # User, ChatTrainer; UserService
  schedule/ models.py recurrence.py queries.py service.py
  participation/ models.py service.py
  publishing/ models.py render.py service.py
  jobs/ models.py queue.py handlers.py runner.py
  telegram/ messenger.py keyboards.py middleware.py errors.py
            handlers/ common.py admin.py schedule.py participation.py
scripts/seed_demo.py
tests/ (mirrors bot/)  conftest.py fakes.py factories.py
```

---

### Task 1: Project scaffold and config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`
- Create: empty `__init__.py` in `bot/`, `bot/core/`, `bot/users/`, `bot/schedule/`, `bot/participation/`, `bot/publishing/`, `bot/jobs/`, `bot/telegram/`, `bot/telegram/handlers/`, `tests/` and every `tests/<package>/` mirror (`core, users, schedule, participation, publishing, jobs, telegram`)
- Create: `bot/core/config.py`
- Test: `tests/core/test_config.py`

**Interfaces:**
- Produces: `Config(BaseSettings)` with `bot_token: str`, `database_url: str`, `club_timezone: str`, property `admin_ids: list[int]`.

- [ ] **Step 1: Write the project files**

`pyproject.toml`:
```toml
[project]
name = "coachs-shadow"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "aiogram>=3.13,<4",
  "sqlalchemy[asyncio]>=2.0.30,<3",
  "aiosqlite>=0.20",
  "alembic>=1.13",
  "pydantic-settings>=2.4",
  "tzdata>=2024.1",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["bot*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
.env
*.db
```

`.env.example`:
```
BOT_TOKEN=123456:replace-me
ADMIN_IDS=111111111,222222222
DATABASE_URL=sqlite+aiosqlite:///coachs_shadow.db
CLUB_TIMEZONE=Europe/Moscow
```

- [ ] **Step 2: Create the environment and empty packages**

Run (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
$pk = "bot","bot/core","bot/users","bot/schedule","bot/participation","bot/publishing","bot/jobs","bot/telegram","bot/telegram/handlers","tests","tests/core","tests/users","tests/schedule","tests/participation","tests/publishing","tests/jobs","tests/telegram"
foreach ($p in $pk) { New-Item -ItemType Directory -Force $p | Out-Null; New-Item -ItemType File -Force "$p/__init__.py" | Out-Null }
```
Expected: install succeeds; all directories contain `__init__.py`.

- [ ] **Step 3: Write the failing test**

`tests/core/test_config.py`:
```python
from bot.core.config import Config


def test_admin_ids_are_parsed_from_comma_list(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.setenv("ADMIN_IDS", "1, 2 ,3")
    assert Config(_env_file=None).admin_ids == [1, 2, 3]


def test_defaults(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    cfg = Config(_env_file=None)
    assert cfg.admin_ids == []
    assert cfg.club_timezone == "Europe/Moscow"
    assert cfg.database_url.startswith("sqlite+aiosqlite")
```

- [ ] **Step 4: Run test, expect failure**

Run: `python -m pytest tests/core/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: bot.core.config`.

- [ ] **Step 5: Implement**

`bot/core/config.py`:
```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    admin_ids_raw: str = Field("", validation_alias="ADMIN_IDS")
    database_url: str = "sqlite+aiosqlite:///coachs_shadow.db"
    club_timezone: str = "Europe/Moscow"

    @property
    def admin_ids(self) -> list[int]:
        return [int(x) for x in self.admin_ids_raw.split(",") if x.strip()]
```

- [ ] **Step 6: Run test, expect pass, commit**

Run: `python -m pytest tests/core/test_config.py -v` → PASS (2 tests).
```bash
git add -A
git commit -m "chore: project scaffold and config"
```

---

### Task 2: DB core, errors, settings store

**Files:**
- Create: `bot/core/db.py`, `bot/core/errors.py`, `bot/core/settings.py`, `bot/registry.py`
- Create: `tests/conftest.py`
- Test: `tests/core/test_settings.py`

**Interfaces:**
- Produces:
  - `Base` (DeclarativeBase), `UTCDateTime` (TypeDecorator, aware UTC in/out), `make_engine(url) -> AsyncEngine` (enables SQLite foreign keys), `make_session_factory(engine) -> async_sessionmaker[AsyncSession]` (`expire_on_commit=False`), `utcnow() -> datetime` (aware).
  - `DomainError(message)`, `PermissionDenied`, `NotFound`, `InvalidAction` — all with `.message: str`.
  - `Setting` model; `DEFAULTS: dict[str, str]`; `get_setting(session, key) -> str`; `set_setting(session, key, value)`; `set_setting_if_absent(session, key, value)`. Keys: `publish_window_h="24"`, `reminder_h="2"`, `duration_min="90"`, `inventory_deadline_h=""`, `club_timezone="Europe/Moscow"`.
  - Fixtures: `engine`, `session_factory`, `session`.

- [ ] **Step 1: Write the failing test**

`tests/core/test_settings.py`:
```python
import pytest

from bot.core.settings import get_setting, set_setting, set_setting_if_absent


async def test_default_is_returned_when_unset(session):
    assert await get_setting(session, "publish_window_h") == "24"


async def test_set_overrides_default(session):
    await set_setting(session, "publish_window_h", "12")
    assert await get_setting(session, "publish_window_h") == "12"


async def test_set_if_absent_does_not_overwrite(session):
    await set_setting(session, "club_timezone", "Asia/Yekaterinburg")
    await set_setting_if_absent(session, "club_timezone", "Europe/Moscow")
    assert await get_setting(session, "club_timezone") == "Asia/Yekaterinburg"


async def test_unknown_key_rejected(session):
    with pytest.raises(KeyError):
        await set_setting(session, "nope", "1")
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/core/test_settings.py -v` → FAIL (import error / missing fixture).

- [ ] **Step 3: Implement**

`bot/core/db.py`:
```python
from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, always returns timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed, use aware UTC")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url)
    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

`bot/core/errors.py`:
```python
class DomainError(Exception):
    """Expected business error; `message` is shown to the user as is."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PermissionDenied(DomainError):
    pass


class NotFound(DomainError):
    pass


class InvalidAction(DomainError):
    pass
```

`bot/core/settings.py`:
```python
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base

DEFAULTS: dict[str, str] = {
    "publish_window_h": "24",
    "reminder_h": "2",
    "duration_min": "90",
    "inventory_deadline_h": "",
    "club_timezone": "Europe/Moscow",
}


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


async def get_setting(session: AsyncSession, key: str) -> str:
    row = await session.get(Setting, key)
    return row.value if row is not None else DEFAULTS[key]


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.commit()


async def set_setting_if_absent(session: AsyncSession, key: str, value: str) -> None:
    if await session.get(Setting, key) is None:
        await set_setting(session, key, value)
```

`bot/registry.py`:
```python
"""Imports every models module so Base.metadata is complete (Alembic, tests)."""
import bot.core.settings  # noqa: F401
```

`tests/conftest.py`:
```python
import pytest

import bot.registry  # noqa: F401  (registers all models)
from bot.core.db import Base, make_engine, make_session_factory


@pytest.fixture
async def engine(tmp_path):
    eng = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s
```

- [ ] **Step 4: Run, expect pass, commit**

Run: `python -m pytest tests/core -v` → PASS.
```bash
git add -A
git commit -m "feat(core): db base, UTC datetime type, errors, settings store"
```

---

### Task 3: Data model

**Files:**
- Create: `bot/core/permissions.py` (only the `Role` constants and `Actor` protocol for now)
- Create: `bot/users/models.py`, `bot/schedule/models.py`, `bot/participation/models.py`, `bot/publishing/models.py`, `bot/jobs/models.py`
- Modify: `bot/registry.py`
- Create: `tests/factories.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces (string-constant classes, not Enums — columns are plain `String`):
  - `Role.ADMIN/TRAINER/MEMBER = "admin"/"trainer"/"member"`; `Actor` Protocol (`telegram_id: int`, `role: str`).
  - `User(telegram_id, name, role, height_cm, foot_eu, helmet_size, cleat_type, started_bot, created_at)`; `ChatTrainer(chat_id, user_id, is_lead)` (composite PK; partial unique index `uq_chat_one_lead`).
  - `Level(id, name, position)`, `Chat(chat_id, title, level_id)`, `TrainingSeries(...)`, `Training(...)` with `.ends_at`, constants `TrainingKind.ALL`, `TrainingStatus.SCHEDULED/CANCELLED/DONE`.
  - `Participation(training_id, user_id, status, updated_at, added_by)` + `ParticipationStatus.GOING/DECLINED`.
  - `TrainingPost(training_id, chat_id, message_id)`.
  - `ScheduledJob(id, kind, run_at, training_id, done_at)`.
  - Factories: `NOW`, `add_user`, `add_level`, `add_chat`, `add_chat_trainer`, `add_training`.

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from bot.participation.models import Participation
from bot.schedule.models import Training
from bot.users.models import ChatTrainer
from tests.factories import add_chat, add_chat_trainer, add_level, add_training, add_user


async def test_datetime_roundtrip_is_aware_utc(session):
    trainer = await add_user(session, 1, "trainer")
    level = await add_level(session)
    starts = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=starts)
    session.expire_all()
    fresh = await session.get(Training, t.id)
    assert fresh.starts_at == starts
    assert fresh.starts_at.tzinfo is not None


async def test_only_one_lead_per_chat(session):
    level = await add_level(session)
    chat = await add_chat(session, level)
    await add_user(session, 1, "trainer")
    await add_user(session, 2, "trainer")
    await add_chat_trainer(session, chat, 1, is_lead=True)
    session.add(ChatTrainer(chat_id=chat.chat_id, user_id=2, is_lead=True))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_many_non_lead_trainers_allowed(session):
    level = await add_level(session)
    chat = await add_chat(session, level)
    for uid in (1, 2, 3):
        await add_user(session, uid, "trainer")
    await add_chat_trainer(session, chat, 1, is_lead=True)
    await add_chat_trainer(session, chat, 2)
    await add_chat_trainer(session, chat, 3)


async def test_participation_is_unique_per_training_and_user(session):
    trainer = await add_user(session, 1, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
    )
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    session.add(Participation(training_id=t.id, user_id=1, status="going", updated_at=now))
    await session.commit()
    session.add(Participation(training_id=t.id, user_id=1, status="declined", updated_at=now))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_foreign_keys_are_enforced(session):
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    session.add(Participation(training_id=999, user_id=999, status="going", updated_at=now))
    with pytest.raises(IntegrityError):
        await session.commit()
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_models.py -v` → FAIL (`bot.participation.models` missing).

- [ ] **Step 3: Implement models**

`bot/core/permissions.py` (extended in Task 5):
```python
from typing import Protocol


class Role:
    ADMIN = "admin"
    TRAINER = "trainer"
    MEMBER = "member"


class Actor(Protocol):
    telegram_id: int
    role: str
```

`bot/users/models.py`:
```python
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base, UTCDateTime, utcnow
from bot.core.permissions import Role


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default=Role.MEMBER)
    height_cm: Mapped[int | None] = mapped_column(Integer)
    foot_eu: Mapped[int | None] = mapped_column(Integer)
    helmet_size: Mapped[str | None] = mapped_column(String(2))  # S/M/L
    cleat_type: Mapped[str | None] = mapped_column(String(16))  # none/spd/look/spd_sl
    started_bot: Mapped[bool] = mapped_column(Boolean, default=False)  # may we DM this user?
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChatTrainer(Base):
    __tablename__ = "chat_trainers"
    __table_args__ = (
        Index(
            "uq_chat_one_lead",
            "chat_id",
            unique=True,
            sqlite_where=text("is_lead = 1"),
            postgresql_where=text("is_lead"),
        ),
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), primary_key=True)
    is_lead: Mapped[bool] = mapped_column(Boolean, default=False)
```

`bot/schedule/models.py`:
```python
from datetime import date, datetime, time, timedelta

from sqlalchemy import BigInteger, Column, Date, ForeignKey, Integer, String, Table, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.core.db import Base, UTCDateTime


class TrainingKind:
    INTERVALS = "intervals"
    ENDURANCE = "endurance"
    TECHNIQUE = "technique"
    ALL = (INTERVALS, ENDURANCE, TECHNIQUE)


class TrainingStatus:
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    DONE = "done"


class Level(Base):
    __tablename__ = "levels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(200))
    level_id: Mapped[int] = mapped_column(ForeignKey("levels.id"))  # not unique: several chats per level


series_level = Table(
    "series_levels",
    Base.metadata,
    Column("series_id", ForeignKey("training_series.id"), primary_key=True),
    Column("level_id", ForeignKey("levels.id"), primary_key=True),
)

training_level = Table(
    "training_levels",
    Base.metadata,
    Column("training_id", ForeignKey("trainings.id"), primary_key=True),
    Column("level_id", ForeignKey("levels.id"), primary_key=True),
)


class TrainingSeries(Base):
    """Template. One-off training = series with empty `weekdays` that fires on `start_date`."""

    __tablename__ = "training_series"

    id: Mapped[int] = mapped_column(primary_key=True)
    trainer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    place: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text, default="")
    capacity: Mapped[int | None] = mapped_column(Integer)
    weekdays: Mapped[str] = mapped_column(String(20), default="")  # ISO weekdays "2,4"; "" = one-off
    start_date: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time] = mapped_column(Time)  # local club time
    end_date: Mapped[date | None] = mapped_column(Date)
    duration_min: Mapped[int] = mapped_column(Integer)
    levels: Mapped[list[Level]] = relationship(secondary=series_level, lazy="selectin")


class Training(Base):
    """Snapshot of the series fields, so editing a series never rewrites existing trainings."""

    __tablename__ = "trainings"
    __table_args__ = (UniqueConstraint("series_id", "starts_at", name="uq_training_series_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("training_series.id"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    duration_min: Mapped[int] = mapped_column(Integer)
    trainer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    place: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text, default="")
    capacity: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=TrainingStatus.SCHEDULED, index=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    levels: Mapped[list[Level]] = relationship(secondary=training_level, lazy="selectin")

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(minutes=self.duration_min)
```

`bot/participation/models.py`:
```python
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base, UTCDateTime


class ParticipationStatus:
    GOING = "going"
    DECLINED = "declined"


class Participation(Base):
    __tablename__ = "participations"

    training_id: Mapped[int] = mapped_column(ForeignKey("trainings.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    added_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
```

`bot/publishing/models.py`:
```python
from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base


class TrainingPost(Base):
    __tablename__ = "training_posts"

    training_id: Mapped[int] = mapped_column(ForeignKey("trainings.id"), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id"), primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
```

`bot/jobs/models.py`:
```python
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base, UTCDateTime


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_jobs_pending", "done_at", "run_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    run_at: Mapped[datetime] = mapped_column(UTCDateTime)
    training_id: Mapped[int | None] = mapped_column(ForeignKey("trainings.id"), index=True)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
```

`bot/registry.py` (replace):
```python
"""Imports every models module so Base.metadata is complete (Alembic, tests)."""
import bot.core.settings  # noqa: F401
import bot.jobs.models  # noqa: F401
import bot.participation.models  # noqa: F401
import bot.publishing.models  # noqa: F401
import bot.schedule.models  # noqa: F401
import bot.users.models  # noqa: F401
```

`tests/factories.py`:
```python
from datetime import datetime, timezone

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
        kind="intervals",
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
        kind="intervals",
        description="",
        capacity=capacity,
        status=status,
        published_at=published_at,
        levels=list(levels),
    )
    session.add(training)
    await session.commit()
    return training
```

- [ ] **Step 4: Run, expect pass, commit**

Run: `python -m pytest tests/test_models.py -v` → PASS (5 tests).
```bash
git add -A
git commit -m "feat: data model for users, schedule, participation, posts, jobs"
```

---

### Task 4: Alembic migrations

**Files:**
- Create: `alembic.ini`, `alembic/` (via `alembic init`), `alembic/versions/<rev>_initial_schema.py`
- Modify: `alembic/env.py`, `alembic/script.py.mako`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `bot.registry`, `Base.metadata`.
- Produces: migration `initial schema`; `DATABASE_URL` env var selects the DB (default `sqlite+aiosqlite:///coachs_shadow.db`).

- [ ] **Step 1: Write the failing test**

`tests/test_migrations.py` (sync test: Alembic's async env runs its own event loop):
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
    lead_index_sql = con.execute("select sql from sqlite_master where name='uq_chat_one_lead'").fetchone()[0]
    con.close()
    assert {
        "users", "levels", "chats", "chat_trainers", "training_series", "trainings",
        "series_levels", "training_levels", "participations", "training_posts",
        "scheduled_jobs", "settings",
    } <= tables
    assert "WHERE" in lead_index_sql.upper()
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_migrations.py -v` → FAIL (`alembic.ini` not found / no script location).

- [ ] **Step 3: Initialise Alembic**

Run: `alembic init -t async alembic`

Replace `alembic/env.py` with:
```python
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import bot.registry  # noqa: F401
from bot.core.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option(
    "sqlalchemy.url", os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///coachs_shadow.db")
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

In `alembic/script.py.mako`, add the line `import bot.core.db` directly after `import sqlalchemy as sa` (autogenerated migrations reference `bot.core.db.UTCDateTime`).

- [ ] **Step 4: Generate the initial migration**

Run (PowerShell):
```powershell
$env:DATABASE_URL = "sqlite+aiosqlite:///alembic_tmp.db"
alembic revision --autogenerate -m "initial schema"
Remove-Item alembic_tmp.db -ErrorAction SilentlyContinue
Remove-Item Env:DATABASE_URL
```
Open the generated file in `alembic/versions/` and check: (a) it creates all 12 tables; (b) `uq_chat_one_lead` is created with `unique=True` and `sqlite_where=sa.text('is_lead = 1')` (if the `where` kwargs are missing, add them by hand); (c) `UTCDateTime` columns render as `bot.core.db.UTCDateTime()`.

- [ ] **Step 5: Run, expect pass, commit**

Run: `python -m pytest tests/test_migrations.py -v` → PASS.
```bash
git add -A
git commit -m "feat: alembic setup and initial schema migration"
```

---

### Task 5: Messenger protocol, fake, permissions, UserService

**Files:**
- Create: `bot/core/messenger.py`, `bot/users/service.py`
- Modify: `bot/core/permissions.py`, `tests/conftest.py`
- Create: `tests/fakes.py`
- Test: `tests/core/test_permissions.py`, `tests/users/test_service.py`

**Interfaces:**
- Consumes: `Role`, `Actor`, `User`, `PermissionDenied`.
- Produces:
  - `Button(text: str, data: str | None = None, url: str | None = None)` (frozen dataclass); `Keyboard = list[list[Button]]`; `ChatUnavailable(Exception)` with `.chat_id`.
  - `Messenger` Protocol: `async send(chat_id: int, text: str, keyboard: Keyboard | None = None) -> int`; `async edit_post(chat_id: int, message_id: int, text: str, keyboard: Keyboard | None = None) -> int` (returns the **current** message_id — a new one if the old post was gone); `async notify(user_id: int, text: str, keyboard: Keyboard | None = None) -> bool` (False = could not deliver).
  - `require_admin(actor)`, `can_manage_training(actor, trainer_id, target_chat_ids: set[int], lead_chat_ids: set[int]) -> bool`, `can_create_for_levels(actor, level_ids: set[int], own_level_ids: set[int]) -> bool`.
  - `UserService(session)`: `upsert_from_telegram(telegram_id, name, *, is_private, admin_ids) -> User`; `sync_admins(admin_ids)`.
  - `FakeMessenger` with `.sent`, `.edits`, `.notifications`, `.unreachable: set[int]`, `.unavailable_chats: set[int]`, `.deleted_posts: set[tuple[int,int]]`; fixture `messenger`.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_permissions.py`:
```python
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
```

`tests/users/test_service.py`:
```python
from bot.users.models import User
from bot.users.service import UserService


async def test_new_user_is_member_and_private_chat_marks_started(session):
    user = await UserService(session).upsert_from_telegram(5, "Анна", is_private=True, admin_ids=[])
    assert (user.role, user.started_bot, user.name) == ("member", True, "Анна")


async def test_group_interaction_does_not_mark_started(session):
    user = await UserService(session).upsert_from_telegram(5, "Анна", is_private=False, admin_ids=[])
    assert user.started_bot is False


async def test_started_flag_is_sticky_and_name_updates(session):
    svc = UserService(session)
    await svc.upsert_from_telegram(5, "Анна", is_private=True, admin_ids=[])
    user = await svc.upsert_from_telegram(5, "Анна К.", is_private=False, admin_ids=[])
    assert user.started_bot is True and user.name == "Анна К."


async def test_configured_admin_gets_admin_role(session):
    user = await UserService(session).upsert_from_telegram(7, "Игорь", is_private=True, admin_ids=[7])
    assert user.role == "admin"


async def test_sync_admins_creates_and_promotes(session):
    svc = UserService(session)
    await svc.upsert_from_telegram(8, "Борис", is_private=True, admin_ids=[])
    await svc.sync_admins([8, 9])
    assert (await session.get(User, 8)).role == "admin"
    assert (await session.get(User, 9)).role == "admin"
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/core/test_permissions.py tests/users -v` → FAIL (imports).

- [ ] **Step 3: Implement**

`bot/core/messenger.py`:
```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Button:
    text: str
    data: str | None = None  # callback data
    url: str | None = None


Keyboard = list[list[Button]]


class ChatUnavailable(Exception):
    """The bot was removed from the chat or lost the right to write there."""

    def __init__(self, chat_id: int) -> None:
        super().__init__(f"chat {chat_id} is unavailable")
        self.chat_id = chat_id


class Messenger(Protocol):
    async def send(self, chat_id: int, text: str, keyboard: Keyboard | None = None) -> int:
        """Send a message; returns message_id. Raises ChatUnavailable."""

    async def edit_post(self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None = None) -> int:
        """Edit a post; returns the current message_id (new if the old one was deleted).
        An empty keyboard removes the buttons. Raises ChatUnavailable."""

    async def notify(self, user_id: int, text: str, keyboard: Keyboard | None = None) -> bool:
        """DM a user. Returns False if it could not be delivered (never raises for that)."""
```

`bot/core/permissions.py` (replace):
```python
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
```

`bot/users/service.py`:
```python
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.permissions import Role
from bot.users.models import User


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_from_telegram(
        self, telegram_id: int, name: str, *, is_private: bool, admin_ids: list[int]
    ) -> User:
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

    async def sync_admins(self, admin_ids: list[int]) -> None:
        for telegram_id in admin_ids:
            user = await self.session.get(User, telegram_id)
            if user is None:
                self.session.add(User(telegram_id=telegram_id, name="Админ", role=Role.ADMIN))
            elif user.role != Role.ADMIN:
                user.role = Role.ADMIN
        await self.session.commit()
```

`tests/fakes.py`:
```python
from dataclasses import dataclass

from bot.core.messenger import ChatUnavailable, Keyboard


@dataclass
class Message:
    chat_id: int
    message_id: int
    text: str
    keyboard: Keyboard | None


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.edits: list[Message] = []
        self.notifications: list[tuple[int, str]] = []
        self.unreachable: set[int] = set()
        self.unavailable_chats: set[int] = set()
        self.deleted_posts: set[tuple[int, int]] = set()
        self._next_id = 100

    async def send(self, chat_id, text, keyboard=None) -> int:
        if chat_id in self.unavailable_chats:
            raise ChatUnavailable(chat_id)
        self._next_id += 1
        self.sent.append(Message(chat_id, self._next_id, text, keyboard))
        return self._next_id

    async def edit_post(self, chat_id, message_id, text, keyboard=None) -> int:
        if chat_id in self.unavailable_chats:
            raise ChatUnavailable(chat_id)
        if (chat_id, message_id) in self.deleted_posts:
            return await self.send(chat_id, text, keyboard)
        self.edits.append(Message(chat_id, message_id, text, keyboard))
        return message_id

    async def notify(self, user_id, text, keyboard=None) -> bool:
        if user_id in self.unreachable:
            return False
        self.notifications.append((user_id, text))
        return True
```

Append to `tests/conftest.py`:
```python
from tests.fakes import FakeMessenger


@pytest.fixture
def messenger():
    return FakeMessenger()
```

- [ ] **Step 4: Run, expect pass, commit**

Run: `python -m pytest tests/core tests/users -v` → PASS.
```bash
git add -A
git commit -m "feat: Messenger protocol, permission policies, UserService"
```

---

### Task 6: Recurrence rules (pure logic)

**Files:**
- Create: `bot/schedule/recurrence.py`
- Test: `tests/schedule/test_recurrence.py`

**Interfaces:**
- Produces: `occurrences(*, weekdays: set[int], start_date: date, start_time: time, end_date: date | None, tz: ZoneInfo, window_start: datetime, window_end: datetime) -> list[datetime]` — aware UTC starts in `[window_start, window_end)`, ascending. ISO weekdays (Mon=1). Empty `weekdays` = single occurrence on `start_date`.

- [ ] **Step 1: Write the failing test**

`tests/schedule/test_recurrence.py`:
```python
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from bot.schedule.recurrence import occurrences

UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")
BERLIN = ZoneInfo("Europe/Berlin")


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_weekly_rule_in_club_timezone():
    got = occurrences(
        weekdays={2, 4}, start_date=date(2026, 10, 1), start_time=time(19, 0), end_date=None, tz=MSK,
        window_start=utc(2026, 10, 12), window_end=utc(2026, 10, 19),
    )
    assert got == [utc(2026, 10, 13, 16), utc(2026, 10, 15, 16)]  # Tue, Thu 19:00 MSK


def test_dst_change_keeps_local_wall_time():
    got = occurrences(
        weekdays={2}, start_date=date(2026, 10, 1), start_time=time(19, 0), end_date=None, tz=BERLIN,
        window_start=utc(2026, 10, 19), window_end=utc(2026, 10, 29),
    )
    # DST ends on 2026-10-25: 19:00 local is 17:00 UTC before, 18:00 UTC after
    assert got == [utc(2026, 10, 20, 17), utc(2026, 10, 27, 18)]


def test_start_date_and_end_date_bound_the_series():
    got = occurrences(
        weekdays={2}, start_date=date(2026, 10, 13), start_time=time(19, 0), end_date=date(2026, 10, 20), tz=MSK,
        window_start=utc(2026, 10, 1), window_end=utc(2026, 12, 1),
    )
    assert got == [utc(2026, 10, 13, 16), utc(2026, 10, 20, 16)]


def test_one_off_fires_once_on_start_date():
    got = occurrences(
        weekdays=set(), start_date=date(2026, 10, 20), start_time=time(19, 0), end_date=None, tz=MSK,
        window_start=utc(2026, 10, 1), window_end=utc(2026, 12, 1),
    )
    assert got == [utc(2026, 10, 20, 16)]


def test_occurrence_before_window_start_is_skipped():
    got = occurrences(
        weekdays=set(), start_date=date(2026, 10, 20), start_time=time(19, 0), end_date=None, tz=MSK,
        window_start=utc(2026, 10, 20, 17), window_end=utc(2026, 12, 1),
    )
    assert got == []
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/schedule/test_recurrence.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

`bot/schedule/recurrence.py`:
```python
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def occurrences(
    *,
    weekdays: set[int],
    start_date: date,
    start_time: time,
    end_date: date | None,
    tz: ZoneInfo,
    window_start: datetime,
    window_end: datetime,
) -> list[datetime]:
    """UTC start times inside [window_start, window_end). Wall-clock time is evaluated in `tz`
    so a DST change does not shift the training."""
    first = max(start_date, window_start.astimezone(tz).date())
    last = window_end.astimezone(tz).date()
    if end_date is not None:
        last = min(last, end_date)
    result: list[datetime] = []
    day = first
    while day <= last:
        hit = (day == start_date) if not weekdays else (day.isoweekday() in weekdays)
        if hit:
            local = datetime.combine(day, start_time, tzinfo=tz)
            start = local.astimezone(timezone.utc)
            if window_start <= start < window_end:
                result.append(start)
        day += timedelta(days=1)
    return result
```

- [ ] **Step 4: Run, expect pass, commit**

Run: `python -m pytest tests/schedule/test_recurrence.py -v` → PASS (5 tests).
```bash
git add -A
git commit -m "feat(schedule): recurrence rules evaluated in club timezone"
```

---

### Task 7: Job queue and ScheduleService

**Files:**
- Create: `bot/jobs/queue.py`, `bot/schedule/queries.py`, `bot/schedule/service.py`
- Test: `tests/jobs/test_queue.py`, `tests/schedule/test_service.py`

**Interfaces:**
- Consumes: models (Task 3), `occurrences` (Task 6), permissions (Task 5), `get_setting`.
- Produces:
  - `JobKind.GENERATE/PUBLISH/REMIND_2H/CLOSE = "generate"/"publish"/"remind_2h"/"close"`.
  - `enqueue(session, kind, run_at, training_id=None) -> ScheduledJob` (flush only, no commit); `ensure_generate_job(session, run_at)`; `cancel_for_training(session, training_id, now)`; `due_job_ids(session, now, limit=100) -> list[int]`.
  - `trainer_level_ids(session, user_id) -> set[int]`; `lead_chat_ids(session, user_id) -> set[int]`; `target_chat_ids(session, training) -> list[int]`.
  - `SeriesInput(kind, place, level_ids, start_date, start_time, weekdays=set(), end_date=None, capacity=None, description="", duration_min=None, trainer_id=None)`.
  - `ScheduleService(session)`: `add_level(actor, name, position=0) -> Level`; `bind_chat(actor, chat_id, title, level_name) -> Chat`; `create_series(actor, data, now) -> TrainingSeries` (also generates immediately); `generate(now) -> list[Training]`; `upcoming(now, limit=10) -> list[Training]`; `can_manage(actor, training) -> bool`; `cancel(actor, training_ids, now) -> list[Training]` (all-or-nothing; commits).

- [ ] **Step 1: Write the failing queue test**

`tests/jobs/test_queue.py`:
```python
from datetime import timedelta

from sqlalchemy import select

from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind, cancel_for_training, due_job_ids, enqueue, ensure_generate_job
from tests.factories import NOW, add_level, add_training, add_user


async def test_due_jobs_are_ordered_and_exclude_done_and_future(session):
    a = await enqueue(session, JobKind.GENERATE, NOW - timedelta(hours=2))
    b = await enqueue(session, JobKind.GENERATE, NOW - timedelta(hours=3))
    await enqueue(session, JobKind.GENERATE, NOW + timedelta(hours=1))
    done = await enqueue(session, JobKind.GENERATE, NOW - timedelta(hours=5))
    done.done_at = NOW
    await session.commit()
    assert await due_job_ids(session, NOW) == [b.id, a.id]


async def test_ensure_generate_job_is_idempotent(session):
    await ensure_generate_job(session, NOW)
    await ensure_generate_job(session, NOW)
    await session.commit()
    rows = list(await session.scalars(select(ScheduledJob)))
    assert len(rows) == 1


async def test_cancel_for_training_marks_only_its_pending_jobs(session):
    trainer = await add_user(session, 1, "trainer")
    level = await add_level(session)
    t1 = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(days=1))
    t2 = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(days=2))
    j1 = await enqueue(session, JobKind.PUBLISH, NOW, t1.id)
    j2 = await enqueue(session, JobKind.PUBLISH, NOW, t2.id)
    await cancel_for_training(session, t1.id, NOW)
    await session.commit()
    await session.refresh(j1)
    await session.refresh(j2)
    assert j1.done_at == NOW
    assert j2.done_at is None
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/jobs/test_queue.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement the queue**

`bot/jobs/queue.py`:
```python
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.jobs.models import ScheduledJob


class JobKind:
    GENERATE = "generate"
    PUBLISH = "publish"
    REMIND_2H = "remind_2h"
    CLOSE = "close"


async def enqueue(
    session: AsyncSession, kind: str, run_at: datetime, training_id: int | None = None
) -> ScheduledJob:
    job = ScheduledJob(kind=kind, run_at=run_at, training_id=training_id)
    session.add(job)
    await session.flush()
    return job


async def ensure_generate_job(session: AsyncSession, run_at: datetime) -> None:
    pending = await session.scalar(
        select(ScheduledJob.id)
        .where(ScheduledJob.kind == JobKind.GENERATE, ScheduledJob.done_at.is_(None))
        .limit(1)
    )
    if pending is None:
        await enqueue(session, JobKind.GENERATE, run_at)


async def cancel_for_training(session: AsyncSession, training_id: int, now: datetime) -> None:
    await session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.training_id == training_id, ScheduledJob.done_at.is_(None))
        .values(done_at=now)
    )


async def due_job_ids(session: AsyncSession, now: datetime, limit: int = 100) -> list[int]:
    rows = await session.scalars(
        select(ScheduledJob.id)
        .where(ScheduledJob.done_at.is_(None), ScheduledJob.run_at <= now)
        .order_by(ScheduledJob.run_at, ScheduledJob.id)
        .limit(limit)
    )
    return list(rows)
```

- [ ] **Step 4: Run queue tests, expect pass**

Run: `python -m pytest tests/jobs/test_queue.py -v` → PASS (3 tests).

- [ ] **Step 5: Write the failing ScheduleService tests**

`tests/schedule/test_service.py` (NOW = Mon 2026-10-19 16:00 UTC; club timezone defaults to Europe/Moscow = UTC+3):
```python
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput
from tests.factories import NOW, add_chat, add_chat_trainer, add_level, add_training, add_user


def inp(level_ids, **kw):
    base = dict(
        kind="intervals", place="Парк", level_ids=level_ids,
        start_date=date(2026, 10, 20), start_time=time(19, 0),
    )
    base.update(kw)
    return SeriesInput(**base)


async def setup_admin_and_level(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    return admin, level


async def trainings(session):
    return list(await session.scalars(select(Training).order_by(Training.starts_at)))


# ---------- levels and chats ----------

async def test_only_admin_adds_levels_and_binds_chats(session):
    admin = await add_user(session, 99, "admin")
    member = await add_user(session, 1, "member")
    svc = ScheduleService(session)
    with pytest.raises(PermissionDenied):
        await svc.add_level(member, "Новички")
    level = await svc.add_level(admin, "Новички")
    chat = await svc.bind_chat(admin, -1001, "Новички клуба", "Новички")
    assert chat.level_id == level.id
    with pytest.raises(NotFound):
        await svc.bind_chat(admin, -1002, "x", "Нет такого")


async def test_rebinding_a_chat_changes_its_level(session):
    admin = await add_user(session, 99, "admin")
    svc = ScheduleService(session)
    await svc.add_level(admin, "A")
    b = await svc.add_level(admin, "B")
    await svc.bind_chat(admin, -1001, "чат", "A")
    chat = await svc.bind_chat(admin, -1001, "чат", "B")
    assert chat.level_id == b.id


# ---------- creating series ----------

async def test_one_off_creates_one_training_with_jobs(session):
    admin, level = await setup_admin_and_level(session)
    await ScheduleService(session).create_series(admin, inp([level.id]), NOW)
    (t,) = await trainings(session)
    assert t.starts_at == datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    assert t.duration_min == 90
    jobs = {j.kind: j for j in await session.scalars(select(ScheduledJob).where(ScheduledJob.training_id == t.id))}
    assert jobs[JobKind.PUBLISH].run_at == t.starts_at - timedelta(hours=24)  # exactly 24 h ahead
    assert jobs[JobKind.REMIND_2H].run_at == t.starts_at - timedelta(hours=2)
    assert jobs[JobKind.CLOSE].run_at == t.starts_at


async def test_training_less_than_24h_ahead_is_published_immediately(session):
    admin, level = await setup_admin_and_level(session)
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)  # starts in 6 h
    await ScheduleService(session).create_series(admin, inp([level.id]), now)
    (t,) = await trainings(session)
    publish = await session.scalar(
        select(ScheduledJob).where(ScheduledJob.training_id == t.id, ScheduledJob.kind == JobKind.PUBLISH)
    )
    assert publish.run_at == now


async def test_weekly_series_generates_four_weeks(session):
    admin, level = await setup_admin_and_level(session)
    now = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)  # Tuesday
    await ScheduleService(session).create_series(
        admin, inp([level.id], weekdays={2, 4}, start_date=date(2026, 10, 6)), now
    )
    assert len(await trainings(session)) == 8  # Oct 6,8,13,15,20,22,27,29


async def test_generate_twice_creates_no_duplicates_and_adds_new_days(session):
    admin, level = await setup_admin_and_level(session)
    now = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
    svc = ScheduleService(session)
    await svc.create_series(admin, inp([level.id], weekdays={2, 4}, start_date=date(2026, 10, 6)), now)
    assert await svc.generate(now) == []
    created = await svc.generate(now + timedelta(days=1))
    assert len(created) == 1  # Tue Nov 3 entered the 4-week window
    assert len(await trainings(session)) == 9


async def test_cancelled_training_is_not_resurrected_by_generation(session):
    admin, level = await setup_admin_and_level(session)
    svc = ScheduleService(session)
    await svc.create_series(admin, inp([level.id]), NOW)
    (t,) = await trainings(session)
    await svc.cancel(admin, [t.id], NOW)
    await svc.generate(NOW + timedelta(hours=1))
    rows = await trainings(session)
    assert len(rows) == 1 and rows[0].status == TrainingStatus.CANCELLED


async def test_series_validation(session):
    admin, level = await setup_admin_and_level(session)
    svc = ScheduleService(session)
    for bad in (
        inp([]),
        inp([level.id], place="  "),
        inp([level.id], kind="yoga"),
        inp([level.id], capacity=0),
        inp([level.id], weekdays={0, 8}),
        inp([level.id], end_date=date(2026, 10, 1)),
        inp([level.id], start_date=date(2026, 10, 1)),  # one-off in the past
    ):
        with pytest.raises(InvalidAction):
            await svc.create_series(admin, bad, NOW)
    with pytest.raises(NotFound):
        await svc.create_series(admin, inp([level.id, 12345]), NOW)
    assert await trainings(session) == []


async def test_trainer_can_create_only_for_levels_of_own_chats(session):
    level_a = await add_level(session, "A")
    level_b = await add_level(session, "B", 1)
    chat_a = await add_chat(session, level_a, -1001)
    trainer = await add_user(session, 10, "trainer")
    await add_chat_trainer(session, chat_a, 10)
    svc = ScheduleService(session)
    await svc.create_series(trainer, inp([level_a.id]), NOW)
    with pytest.raises(PermissionDenied):
        await svc.create_series(trainer, inp([level_a.id, level_b.id], start_date=date(2026, 10, 21)), NOW)


async def test_trainer_without_chats_gets_explanatory_error(session):
    level = await add_level(session)
    trainer = await add_user(session, 10, "trainer")
    with pytest.raises(PermissionDenied, match="не добавлены ни в один чат"):
        await ScheduleService(session).create_series(trainer, inp([level.id]), NOW)


async def test_member_cannot_create(session):
    level = await add_level(session)
    member = await add_user(session, 1, "member")
    with pytest.raises(PermissionDenied):
        await ScheduleService(session).create_series(member, inp([level.id]), NOW)


# ---------- cancel ----------

async def test_cancel_sets_status_and_removes_pending_jobs(session):
    admin, level = await setup_admin_and_level(session)
    svc = ScheduleService(session)
    await svc.create_series(admin, inp([level.id]), NOW)
    (t,) = await trainings(session)
    await svc.cancel(admin, [t.id], NOW)
    await session.refresh(t)
    assert t.status == TrainingStatus.CANCELLED
    pending = list(
        await session.scalars(
            select(ScheduledJob).where(ScheduledJob.training_id == t.id, ScheduledJob.done_at.is_(None))
        )
    )
    assert pending == []


async def test_cancel_is_all_or_nothing(session):
    admin, level = await setup_admin_and_level(session)
    future = await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=1))
    started = await add_training(session, trainer=admin, levels=[level], starts_at=NOW - timedelta(hours=1))
    svc = ScheduleService(session)
    with pytest.raises(InvalidAction):
        await svc.cancel(admin, [future.id, started.id], NOW)
    await session.refresh(future)
    assert future.status == TrainingStatus.SCHEDULED


async def test_cancelling_twice_is_rejected(session):
    admin, level = await setup_admin_and_level(session)
    t = await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=1))
    svc = ScheduleService(session)
    await svc.cancel(admin, [t.id], NOW)
    with pytest.raises(InvalidAction):
        await svc.cancel(admin, [t.id], NOW)


async def test_trainer_cancels_own_but_not_foreign(session):
    level = await add_level(session)
    me = await add_user(session, 10, "trainer")
    other = await add_user(session, 11, "trainer")
    mine = await add_training(session, trainer=me, levels=[level], starts_at=NOW + timedelta(days=1))
    theirs = await add_training(session, trainer=other, levels=[level], starts_at=NOW + timedelta(days=2))
    svc = ScheduleService(session)
    await svc.cancel(me, [mine.id], NOW)
    with pytest.raises(PermissionDenied):
        await svc.cancel(me, [theirs.id], NOW)


async def test_lead_must_lead_every_target_chat(session):
    level_a = await add_level(session, "A")
    level_b = await add_level(session, "B", 1)
    chat_a = await add_chat(session, level_a, -1001)
    chat_b = await add_chat(session, level_b, -1002)
    owner = await add_user(session, 10, "trainer")
    lead = await add_user(session, 20, "trainer")
    await add_chat_trainer(session, chat_a, 20, is_lead=True)  # leads A only
    await add_chat_trainer(session, chat_b, 10, is_lead=True)
    only_a = await add_training(session, trainer=owner, levels=[level_a], starts_at=NOW + timedelta(days=1))
    a_and_b = await add_training(session, trainer=owner, levels=[level_a, level_b], starts_at=NOW + timedelta(days=2))
    svc = ScheduleService(session)
    with pytest.raises(PermissionDenied):
        await svc.cancel(lead, [a_and_b.id], NOW)
    await svc.cancel(lead, [only_a.id], NOW)


async def test_upcoming_lists_only_future_scheduled_within_horizon(session):
    admin, level = await setup_admin_and_level(session)
    soon = await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=1))
    await add_training(session, trainer=admin, levels=[level], starts_at=NOW - timedelta(days=1))
    await add_training(session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=60))
    cancelled = await add_training(
        session, trainer=admin, levels=[level], starts_at=NOW + timedelta(days=2), status=TrainingStatus.CANCELLED
    )
    got = await ScheduleService(session).upcoming(NOW)
    assert [t.id for t in got] == [soon.id]
    assert cancelled.id not in [t.id for t in got]
```

- [ ] **Step 6: Run, expect failure**

Run: `python -m pytest tests/schedule/test_service.py -v` → FAIL (module missing).

- [ ] **Step 7: Implement queries and service**

`bot/schedule/queries.py`:
```python
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
```

`bot/schedule/service.py`:
```python
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.permissions import Role, can_create_for_levels, can_manage_training, require_admin
from bot.core.settings import get_setting
from bot.jobs.queue import JobKind, cancel_for_training, enqueue
from bot.schedule.models import Chat, Level, Training, TrainingKind, TrainingSeries, TrainingStatus
from bot.schedule.queries import lead_chat_ids, target_chat_ids, trainer_level_ids
from bot.schedule.recurrence import occurrences
from bot.users.models import User

GENERATION_WEEKS = 4


@dataclass
class SeriesInput:
    kind: str
    place: str
    level_ids: list[int]
    start_date: date
    start_time: time
    weekdays: set[int] = field(default_factory=set)  # ISO 1..7; empty = one-off
    end_date: date | None = None
    capacity: int | None = None
    description: str = ""
    duration_min: int | None = None
    trainer_id: int | None = None  # only an admin may set someone else


class ScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- levels and chats (admin) ----

    async def add_level(self, actor: User, name: str, position: int = 0) -> Level:
        require_admin(actor)
        name = name.strip()
        if not name:
            raise InvalidAction("Название уровня не может быть пустым")
        level = Level(name=name, position=position)
        self.session.add(level)
        await self.session.commit()
        return level

    async def bind_chat(self, actor: User, chat_id: int, title: str, level_name: str) -> Chat:
        require_admin(actor)
        level = await self.session.scalar(select(Level).where(Level.name == level_name.strip()))
        if level is None:
            raise NotFound(f"Уровень «{level_name.strip()}» не найден")
        chat = await self.session.get(Chat, chat_id)
        if chat is None:
            chat = Chat(chat_id=chat_id, title=title, level_id=level.id)
            self.session.add(chat)
        else:
            chat.title = title
            chat.level_id = level.id
        await self.session.commit()
        return chat

    # ---- series ----

    async def create_series(self, actor: User, data: SeriesInput, now: datetime) -> TrainingSeries:
        self._validate(data)
        await self._check_can_create(actor, set(data.level_ids))
        trainer_id = data.trainer_id or actor.telegram_id
        if trainer_id != actor.telegram_id:
            require_admin(actor)
        if await self.session.get(User, trainer_id) is None:
            raise NotFound("Тренер не найден")
        levels = list(await self.session.scalars(select(Level).where(Level.id.in_(data.level_ids))))
        if len(levels) != len(set(data.level_ids)):
            raise NotFound("Уровень не найден")

        tz = await self._tz()
        if not data.weekdays:
            first = datetime.combine(data.start_date, data.start_time, tzinfo=tz)
            if first <= now:
                raise InvalidAction("Тренировка должна быть в будущем")
        duration = data.duration_min or int(await get_setting(self.session, "duration_min"))

        series = TrainingSeries(
            trainer_id=trainer_id,
            place=data.place.strip(),
            kind=data.kind,
            description=data.description,
            capacity=data.capacity,
            weekdays=",".join(str(d) for d in sorted(data.weekdays)),
            start_date=data.start_date,
            start_time=data.start_time,
            end_date=data.end_date,
            duration_min=duration,
            levels=levels,
        )
        self.session.add(series)
        await self.session.flush()
        await self._generate_for_series(series, now, tz)
        await self.session.commit()
        return series

    async def generate(self, now: datetime) -> list[Training]:
        tz = await self._tz()
        today = now.astimezone(tz).date()
        all_series = await self.session.scalars(
            select(TrainingSeries).where(or_(TrainingSeries.end_date.is_(None), TrainingSeries.end_date >= today))
        )
        created: list[Training] = []
        for series in list(all_series):
            created += await self._generate_for_series(series, now, tz)
        await self.session.commit()
        return created

    async def upcoming(self, now: datetime, limit: int = 10) -> list[Training]:
        rows = await self.session.scalars(
            select(Training)
            .where(
                Training.status == TrainingStatus.SCHEDULED,
                Training.starts_at > now,
                Training.starts_at < now + timedelta(weeks=GENERATION_WEEKS),
            )
            .order_by(Training.starts_at)
            .limit(limit)
        )
        return list(rows)

    # ---- cancel ----

    async def can_manage(self, actor: User, training: Training) -> bool:
        if actor.role == Role.ADMIN:
            return True
        return can_manage_training(
            actor,
            training.trainer_id,
            set(await target_chat_ids(self.session, training)),
            await lead_chat_ids(self.session, actor.telegram_id),
        )

    async def cancel(self, actor: User, training_ids: list[int], now: datetime) -> list[Training]:
        """Cancel several trainings atomically: validate all first, then mutate."""
        if not training_ids:
            raise InvalidAction("Выберите тренировки для отмены")
        trainings: list[Training] = []
        for training_id in dict.fromkeys(training_ids):
            training = await self.session.get(Training, training_id)
            if training is None:
                raise NotFound("Тренировка не найдена")
            if training.status != TrainingStatus.SCHEDULED:
                raise InvalidAction("Тренировка уже отменена или прошла")
            if training.starts_at <= now:
                raise InvalidAction("Нельзя отменить начатую или прошедшую тренировку")
            if not await self.can_manage(actor, training):
                raise PermissionDenied("Нет прав на эту тренировку")
            trainings.append(training)
        for training in trainings:
            training.status = TrainingStatus.CANCELLED
            await cancel_for_training(self.session, training.id, now)
        await self.session.commit()
        return trainings

    # ---- internals ----

    async def _tz(self) -> ZoneInfo:
        return ZoneInfo(await get_setting(self.session, "club_timezone"))

    @staticmethod
    def _validate(data: SeriesInput) -> None:
        if not data.place.strip():
            raise InvalidAction("Укажите место тренировки")
        if data.kind not in TrainingKind.ALL:
            raise InvalidAction("Неизвестный тип тренировки")
        if not data.level_ids:
            raise InvalidAction("Выберите хотя бы один уровень")
        if not data.weekdays <= {1, 2, 3, 4, 5, 6, 7}:
            raise InvalidAction("Дни недели должны быть от 1 до 7")
        if data.capacity is not None and data.capacity < 1:
            raise InvalidAction("Лимит мест должен быть больше нуля")
        if data.duration_min is not None and data.duration_min < 1:
            raise InvalidAction("Длительность должна быть больше нуля")
        if data.end_date is not None and data.end_date < data.start_date:
            raise InvalidAction("Дата окончания раньше даты начала")

    async def _check_can_create(self, actor: User, level_ids: set[int]) -> None:
        if actor.role == Role.ADMIN:
            return
        if actor.role != Role.TRAINER:
            raise PermissionDenied("Создавать тренировки могут только тренеры")
        own = await trainer_level_ids(self.session, actor.telegram_id)
        if not own:
            raise PermissionDenied("Вы тренер, но пока не добавлены ни в один чат. Обратитесь к администратору")
        if not can_create_for_levels(actor, level_ids, own):
            raise PermissionDenied("Можно создавать тренировки только для уровней своих чатов")

    async def _generate_for_series(self, series: TrainingSeries, now: datetime, tz: ZoneInfo) -> list[Training]:
        horizon = now + timedelta(weeks=GENERATION_WEEKS)
        starts = occurrences(
            weekdays={int(x) for x in series.weekdays.split(",") if x},
            start_date=series.start_date,
            start_time=series.start_time,
            end_date=series.end_date,
            tz=tz,
            window_start=now,
            window_end=horizon,
        )
        existing = set(
            await self.session.scalars(
                select(Training.starts_at).where(
                    Training.series_id == series.id, Training.starts_at >= now, Training.starts_at < horizon
                )
            )
        )
        created: list[Training] = []
        for starts_at in starts:
            if starts_at in existing:
                continue
            training = Training(
                series_id=series.id,
                starts_at=starts_at,
                duration_min=series.duration_min,
                trainer_id=series.trainer_id,
                place=series.place,
                kind=series.kind,
                description=series.description,
                capacity=series.capacity,
                status=TrainingStatus.SCHEDULED,
                levels=list(series.levels),
            )
            self.session.add(training)
            created.append(training)
        await self.session.flush()
        publish_h = int(await get_setting(self.session, "publish_window_h"))
        remind_h = int(await get_setting(self.session, "reminder_h"))
        for training in created:
            await enqueue(
                self.session, JobKind.PUBLISH, max(training.starts_at - timedelta(hours=publish_h), now), training.id
            )
            remind_at = training.starts_at - timedelta(hours=remind_h)
            if remind_at > now:
                await enqueue(self.session, JobKind.REMIND_2H, remind_at, training.id)
            await enqueue(self.session, JobKind.CLOSE, training.starts_at, training.id)
        return created
```

- [ ] **Step 8: Run, expect pass, commit**

Run: `python -m pytest tests/jobs/test_queue.py tests/schedule -v` → PASS.
```bash
git add -A
git commit -m "feat(schedule): job queue, series creation, generation, cancellation with rights"
```

---

### Task 8: ParticipationService

**Files:**
- Create: `bot/participation/service.py`
- Test: `tests/participation/test_service.py`

**Interfaces:**
- Consumes: `Training`, `Participation`, `NotFound`, `InvalidAction`.
- Produces: `JoinOutcome(str, Enum)`: `JOINED`, `ALREADY`, `FULL`. `ParticipationService(session)`: `join(training_id, user_id, now) -> JoinOutcome`; `decline(training_id, user_id, now) -> str | None` (previous status: `"going"`, `"declined"` or `None`); `going_count(training_id) -> int`; `list_participants(training_id) -> tuple[list[User], list[User]]` (going, declined; ordered by `updated_at`). Both `join` and `decline` require: training exists, `scheduled`, published, not started. Commits.

- [ ] **Step 1: Write the failing test**

`tests/participation/test_service.py`:
```python
from datetime import timedelta

import pytest

from bot.core.errors import InvalidAction, NotFound
from bot.participation.service import JoinOutcome, ParticipationService
from bot.schedule.models import TrainingStatus
from tests.factories import NOW, add_level, add_training, add_user


async def published(session, capacity=None, **kw):
    trainer = await add_user(session, 1000, "trainer")
    level = await add_level(session)
    return await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20),
        capacity=capacity, published_at=NOW - timedelta(hours=4), **kw
    )


async def test_join_and_count(session):
    t = await published(session, capacity=2)
    await add_user(session, 1)
    svc = ParticipationService(session)
    assert await svc.join(t.id, 1, NOW) == JoinOutcome.JOINED
    assert await svc.going_count(t.id) == 1


async def test_join_twice_is_idempotent(session):
    t = await published(session)
    await add_user(session, 1)
    svc = ParticipationService(session)
    await svc.join(t.id, 1, NOW)
    assert await svc.join(t.id, 1, NOW) == JoinOutcome.ALREADY
    assert await svc.going_count(t.id) == 1


async def test_already_going_gets_already_even_when_full(session):
    t = await published(session, capacity=1)
    await add_user(session, 1)
    svc = ParticipationService(session)
    await svc.join(t.id, 1, NOW)
    assert await svc.join(t.id, 1, NOW) == JoinOutcome.ALREADY


async def test_full_training_rejects_newcomer_and_keeps_status(session):
    t = await published(session, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    svc = ParticipationService(session)
    await svc.join(t.id, 1, NOW)
    assert await svc.join(t.id, 2, NOW) == JoinOutcome.FULL
    going, declined = await svc.list_participants(t.id)
    assert [u.telegram_id for u in going] == [1] and declined == []


async def test_decliner_cannot_rejoin_a_full_training_and_stays_declined(session):
    t = await published(session, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    svc = ParticipationService(session)
    await svc.join(t.id, 2, NOW)
    await svc.decline(t.id, 1, NOW)
    assert await svc.join(t.id, 1, NOW) == JoinOutcome.FULL
    _, declined = await svc.list_participants(t.id)
    assert [u.telegram_id for u in declined] == [1]


async def test_decline_frees_a_seat_and_reports_previous_status(session):
    t = await published(session, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    svc = ParticipationService(session)
    await svc.join(t.id, 1, NOW)
    assert await svc.decline(t.id, 1, NOW) == "going"
    assert await svc.join(t.id, 2, NOW) == JoinOutcome.JOINED
    assert await svc.decline(t.id, 1, NOW) == "declined"
    await add_user(session, 3)
    assert await svc.decline(t.id, 3, NOW) is None


async def test_no_signup_for_unpublished_cancelled_started_or_missing(session):
    trainer = await add_user(session, 1000, "trainer")
    level = await add_level(session)
    await add_user(session, 1)
    svc = ParticipationService(session)
    unpublished = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(days=3))
    cancelled = await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20),
        published_at=NOW, status=TrainingStatus.CANCELLED,
    )
    started = await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW - timedelta(minutes=5), published_at=NOW - timedelta(days=1)
    )
    for t in (unpublished, cancelled, started):
        with pytest.raises(InvalidAction):
            await svc.join(t.id, 1, NOW)
    with pytest.raises(NotFound):
        await svc.join(9999, 1, NOW)


async def test_list_participants_orders_by_time(session):
    t = await published(session)
    for uid in (1, 2, 3):
        await add_user(session, uid)
    svc = ParticipationService(session)
    await svc.join(t.id, 2, NOW)
    await svc.join(t.id, 1, NOW + timedelta(minutes=1))
    await svc.decline(t.id, 3, NOW)
    going, declined = await svc.list_participants(t.id)
    assert [u.telegram_id for u in going] == [2, 1]
    assert [u.telegram_id for u in declined] == [3]
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/participation -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

`bot/participation/service.py`:
```python
from datetime import datetime
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction, NotFound
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Training, TrainingStatus
from bot.users.models import User


class JoinOutcome(str, Enum):
    JOINED = "joined"
    ALREADY = "already"
    FULL = "full"


class ParticipationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def join(self, training_id: int, user_id: int, now: datetime) -> JoinOutcome:
        training = await self._open_training(training_id, now)
        row = await self.session.get(Participation, (training_id, user_id))
        if row is not None and row.status == ParticipationStatus.GOING:
            return JoinOutcome.ALREADY
        if training.capacity is not None and await self.going_count(training_id) >= training.capacity:
            return JoinOutcome.FULL
        if row is None:
            self.session.add(
                Participation(training_id=training_id, user_id=user_id, status=ParticipationStatus.GOING, updated_at=now)
            )
        else:
            row.status = ParticipationStatus.GOING
            row.updated_at = now
        await self.session.commit()
        return JoinOutcome.JOINED

    async def decline(self, training_id: int, user_id: int, now: datetime) -> str | None:
        """Returns the previous status ("going", "declined") or None if there was no record."""
        await self._open_training(training_id, now)
        row = await self.session.get(Participation, (training_id, user_id))
        previous = row.status if row is not None else None
        if row is None:
            self.session.add(
                Participation(training_id=training_id, user_id=user_id, status=ParticipationStatus.DECLINED, updated_at=now)
            )
        elif row.status != ParticipationStatus.DECLINED:
            row.status = ParticipationStatus.DECLINED
            row.updated_at = now
        await self.session.commit()
        return previous

    async def going_count(self, training_id: int) -> int:
        return await self.session.scalar(
            select(func.count())
            .select_from(Participation)
            .where(Participation.training_id == training_id, Participation.status == ParticipationStatus.GOING)
        )

    async def list_participants(self, training_id: int) -> tuple[list[User], list[User]]:
        rows = await self.session.execute(
            select(Participation.status, User)
            .join(User, User.telegram_id == Participation.user_id)
            .where(Participation.training_id == training_id)
            .order_by(Participation.updated_at, Participation.user_id)
        )
        going: list[User] = []
        declined: list[User] = []
        for status, user in rows:
            (going if status == ParticipationStatus.GOING else declined).append(user)
        return going, declined

    async def _open_training(self, training_id: int, now: datetime) -> Training:
        # FOR UPDATE serialises concurrent sign-ups on PostgreSQL (ignored by SQLite).
        training = (
            await self.session.execute(select(Training).where(Training.id == training_id).with_for_update())
        ).scalar_one_or_none()
        if training is None:
            raise NotFound("Тренировка не найдена")
        if training.status == TrainingStatus.CANCELLED:
            raise InvalidAction("Тренировка отменена")
        if training.published_at is None:
            raise InvalidAction("Запись ещё не открыта")
        if training.status != TrainingStatus.SCHEDULED or training.starts_at <= now:
            raise InvalidAction("Тренировка уже началась")
        return training
```

- [ ] **Step 4: Run, expect pass, commit**

Run: `python -m pytest tests/participation -v` → PASS (8 tests).
```bash
git add -A
git commit -m "feat(participation): join/decline with seat limit"
```

---

### Task 9: Card rendering (pure)

**Files:**
- Create: `bot/publishing/render.py`
- Test: `tests/publishing/test_render.py`

**Interfaces:**
- Consumes: `Button`, `Keyboard`.
- Produces:
  - `KIND_NAMES`; `format_when(starts_at, duration_min, tz) -> str`.
  - `CardData(training_id, status, starts_at, duration_min, kind, place, trainer_name, level_names: list[str], capacity: int | None, going_count: int, description: str, published: bool, opens_at: datetime)` (frozen).
  - `render_card(card, tz) -> tuple[str, Keyboard]`: buttons `join:<id>`, `decline:<id>`, `who:<id>` only for a published `scheduled` card; unpublished card has one button `Button("🕒 Откроется dd.mm HH:MM", "noop")`; cancelled/done cards have `[]`.
  - `render_participants(going: list[str], declined: list[str]) -> str`; `fit_alert(text, limit=190) -> str`; `ALERT_LIMIT = 190`.

- [ ] **Step 1: Write the failing test**

`tests/publishing/test_render.py`:
```python
from dataclasses import replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.core.messenger import Button
from bot.publishing.render import CardData, fit_alert, render_card, render_participants

MSK = ZoneInfo("Europe/Moscow")


def card(**kw):
    base = dict(
        training_id=7, status="scheduled", starts_at=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc),
        duration_min=90, kind="intervals", place="Парк Горького", trainer_name="Иван",
        level_names=["Новички", "Любители"], capacity=10, going_count=3, description="",
        published=True, opens_at=datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return CardData(**base)


def test_published_card_text_and_buttons():
    text, kb = render_card(card(description="Взять воду"), MSK)
    assert "Интервалы" in text
    assert "вторник, 20.10 19:00–20:30" in text  # shown in club time
    assert "Парк Горького" in text and "Иван" in text and "Новички, Любители" in text
    assert "Идут: 3/10" in text
    assert "Взять воду" in text
    assert kb == [
        [Button("✅ Пойду", "join:7"), Button("❌ Не пойду", "decline:7")],
        [Button("👥 Участники", "who:7")],
    ]


def test_card_without_limit_shows_plain_count():
    text, _ = render_card(card(capacity=None, going_count=4), MSK)
    assert "Идут: 4" in text and "/" not in text.split("Идут:")[1].split("\n")[0]


def test_unpublished_card_shows_opening_time_instead_of_join_buttons():
    text, kb = render_card(card(published=False), MSK)
    assert kb == [[Button("🕒 Откроется 19.10 19:00", "noop")]]
    assert "Идут" not in text


def test_cancelled_card_has_marker_and_no_buttons():
    text, kb = render_card(card(status="cancelled"), MSK)
    assert text.startswith("❌ Отменена")
    assert kb == []


def test_done_card_has_marker_and_no_buttons():
    text, kb = render_card(card(status="done"), MSK)
    assert text.startswith("🏁 Прошла")
    assert kb == []


def test_participants_text():
    assert render_participants(["Анна", "Борис"], ["Вера"]) == (
        "Идут (2):\n1. Анна\n2. Борис\n\nОтказались (1):\n– Вера"
    )
    assert render_participants([], []) == "Идут (0)"


def test_fit_alert_trims_long_text_and_keeps_short():
    assert fit_alert("коротко") == "коротко"
    long = render_participants([f"Участник {i}" for i in range(60)], [])
    trimmed = fit_alert(long)
    assert len(trimmed) <= 190 and trimmed.endswith("…")
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/publishing/test_render.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

`bot/publishing/render.py`:
```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.core.messenger import Button, Keyboard

KIND_NAMES = {"intervals": "Интервалы", "endurance": "Длительная", "technique": "Техника"}
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
ALERT_LIMIT = 190  # Telegram callback alerts are capped at 200 characters


@dataclass(frozen=True)
class CardData:
    training_id: int
    status: str
    starts_at: datetime
    duration_min: int
    kind: str
    place: str
    trainer_name: str
    level_names: list[str]
    capacity: int | None
    going_count: int
    description: str
    published: bool
    opens_at: datetime


def format_when(starts_at: datetime, duration_min: int, tz: ZoneInfo) -> str:
    start = starts_at.astimezone(tz)
    end = (starts_at + timedelta(minutes=duration_min)).astimezone(tz)
    return f"{WEEKDAYS[start.weekday()]}, {start:%d.%m} {start:%H:%M}–{end:%H:%M}"


def render_card(card: CardData, tz: ZoneInfo) -> tuple[str, Keyboard]:
    lines: list[str] = []
    if card.status == "cancelled":
        lines.append("❌ Отменена")
    elif card.status == "done":
        lines.append("🏁 Прошла")
    lines.append(f"🚴 {KIND_NAMES.get(card.kind, card.kind)} · {format_when(card.starts_at, card.duration_min, tz)}")
    lines.append(f"📍 {card.place}")
    lines.append(f"👤 Тренер: {card.trainer_name}")
    lines.append(f"🎚 Уровни: {', '.join(card.level_names)}")
    if card.published:
        going = f"{card.going_count}/{card.capacity}" if card.capacity else str(card.going_count)
        lines.append(f"✅ Идут: {going}")
    if card.description:
        lines.append("")
        lines.append(card.description)

    keyboard: Keyboard = []
    if card.status == "scheduled":
        if card.published:
            tid = card.training_id
            keyboard = [
                [Button("✅ Пойду", f"join:{tid}"), Button("❌ Не пойду", f"decline:{tid}")],
                [Button("👥 Участники", f"who:{tid}")],
            ]
        else:
            opens = card.opens_at.astimezone(tz)
            keyboard = [[Button(f"🕒 Откроется {opens:%d.%m %H:%M}", "noop")]]
    return "\n".join(lines), keyboard


def render_participants(going: list[str], declined: list[str]) -> str:
    parts = [f"Идут ({len(going)})" + (":" if going else "")]
    parts += [f"{i}. {name}" for i, name in enumerate(going, 1)]
    text = "\n".join(parts)
    if declined:
        text += f"\n\nОтказались ({len(declined)}):\n" + "\n".join(f"– {name}" for name in declined)
    return text


def fit_alert(text: str, limit: int = ALERT_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
```

- [ ] **Step 4: Run, expect pass, commit**

Run: `python -m pytest tests/publishing/test_render.py -v` → PASS (7 tests).
```bash
git add -A
git commit -m "feat(publishing): card and participant list rendering"
```

---

### Task 10: PublishService

**Files:**
- Create: `bot/publishing/service.py`
- Test: `tests/publishing/test_service.py`

**Interfaces:**
- Consumes: `Messenger`, `ChatUnavailable`, `render_card`, `CardData`, `target_chat_ids`, models, `get_setting`.
- Produces `PublishService(session, messenger, *, send_delay: float = 0.05)`:
  - `build_card(training, published: bool | None = None) -> CardData`
  - `render(training, published: bool | None = None) -> tuple[str, Keyboard]` (club timezone applied)
  - `publish(training_id, now)` — idempotent per `(training_id, chat_id)`; sets `published_at` after all chats; warns admins when no chats or a chat is unavailable.
  - `refresh_posts(training_id)` — re-renders all posts; stores a new `message_id` if the post was replaced; skips unavailable chats with admin warning.
  - `close(training_id, now)` — `scheduled` and started → `done`, refresh.
  - `send_reminders(training_id, now)` — DM to `going` while scheduled and not started.
  - `announce_cancelled(training_id)` — refresh posts + DM `going`.

- [ ] **Step 1: Write the failing test**

`tests/publishing/test_service.py`:
```python
from datetime import timedelta

from sqlalchemy import select

from bot.participation.service import ParticipationService
from bot.publishing.models import TrainingPost
from bot.publishing.service import PublishService
from bot.schedule.models import Training, TrainingStatus
from tests.factories import NOW, add_chat, add_level, add_training, add_user

STARTS = NOW + timedelta(hours=24)


async def world(session, chats=(-1001,), published_at=None):
    await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer", name="Иван")
    level = await add_level(session)
    for chat_id in chats:
        await add_chat(session, level, chat_id)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=STARTS, capacity=5, published_at=published_at
    )
    return t


def svc(session, messenger):
    return PublishService(session, messenger, send_delay=0)


async def posts(session):
    return list(await session.scalars(select(TrainingPost).order_by(TrainingPost.chat_id)))


async def test_publish_posts_to_every_chat_of_the_levels_once(session, messenger):
    t = await world(session, chats=(-1001, -1002))
    await svc(session, messenger).publish(t.id, NOW)
    assert sorted(m.chat_id for m in messenger.sent) == [-1002, -1001]
    assert "Интервалы" in messenger.sent[0].text and messenger.sent[0].keyboard  # buttons are live
    assert len(await posts(session)) == 2
    await session.refresh(t)
    assert t.published_at == NOW
    await svc(session, messenger).publish(t.id, NOW)  # rerun
    assert len(messenger.sent) == 2


async def test_publish_resumes_after_partial_failure_without_duplicates(session, messenger):
    t = await world(session, chats=(-1001, -1002))
    messenger.unavailable_chats.add(-1002)
    await svc(session, messenger).publish(t.id, NOW)
    assert [p.chat_id for p in await posts(session)] == [-1001]
    assert any("-1002" in text for uid, text in messenger.notifications if uid == 99)  # admin warned
    await session.refresh(t)
    assert t.published_at == NOW


async def test_publish_without_chats_still_opens_signup_and_warns_admin(session, messenger):
    t = await world(session, chats=())
    await svc(session, messenger).publish(t.id, NOW)
    await session.refresh(t)
    assert t.published_at == NOW
    assert messenger.sent == []
    assert [uid for uid, _ in messenger.notifications] == [99]


async def test_publish_skips_cancelled_and_started_trainings(session, messenger):
    t = await world(session)
    t.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc(session, messenger).publish(t.id, NOW)
    assert messenger.sent == []
    t.status = TrainingStatus.SCHEDULED
    await session.commit()
    await svc(session, messenger).publish(t.id, STARTS + timedelta(minutes=1))
    assert messenger.sent == []


async def test_refresh_edits_posts_with_new_count(session, messenger):
    t = await world(session)
    await svc(session, messenger).publish(t.id, NOW)
    await add_user(session, 1, name="Анна")
    await ParticipationService(session).join(t.id, 1, NOW)
    await svc(session, messenger).refresh_posts(t.id)
    assert "Идут: 1/5" in messenger.edits[-1].text


async def test_refresh_replaces_manually_deleted_post_and_stores_new_message_id(session, messenger):
    t = await world(session)
    await svc(session, messenger).publish(t.id, NOW)
    (post,) = await posts(session)
    old_id = post.message_id
    messenger.deleted_posts.add((post.chat_id, old_id))
    await svc(session, messenger).refresh_posts(t.id)
    await session.refresh(post)
    assert post.message_id != old_id
    assert len(messenger.sent) == 2


async def test_refresh_survives_unavailable_chat(session, messenger):
    t = await world(session, chats=(-1001, -1002))
    await svc(session, messenger).publish(t.id, NOW)
    messenger.unavailable_chats.add(-1002)
    await svc(session, messenger).refresh_posts(t.id)
    assert [e.chat_id for e in messenger.edits] == [-1001]


async def test_close_marks_done_and_removes_buttons(session, messenger):
    t = await world(session)
    await svc(session, messenger).publish(t.id, NOW)
    await svc(session, messenger).close(t.id, STARTS - timedelta(minutes=1))  # too early: no-op
    await session.refresh(t)
    assert t.status == TrainingStatus.SCHEDULED
    await svc(session, messenger).close(t.id, STARTS)
    await session.refresh(t)
    assert t.status == TrainingStatus.DONE
    assert "Прошла" in messenger.edits[-1].text and messenger.edits[-1].keyboard == []


async def test_close_does_not_touch_cancelled(session, messenger):
    t = await world(session)
    t.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc(session, messenger).close(t.id, STARTS)
    await session.refresh(t)
    assert t.status == TrainingStatus.CANCELLED


async def test_reminders_go_to_going_users_and_unreachable_ones_do_not_break(session, messenger):
    t = await world(session, published_at=NOW)
    for uid in (1, 2, 3):
        await add_user(session, uid)
    p = ParticipationService(session)
    await p.join(t.id, 1, NOW)
    await p.join(t.id, 2, NOW)
    await p.decline(t.id, 3, NOW)
    messenger.unreachable.add(2)  # never pressed /start
    await svc(session, messenger).send_reminders(t.id, STARTS - timedelta(hours=2))
    assert [uid for uid, _ in messenger.notifications] == [1]


async def test_no_reminder_after_start_or_for_cancelled(session, messenger):
    t = await world(session, published_at=NOW)
    await add_user(session, 1)
    await ParticipationService(session).join(t.id, 1, NOW)
    await svc(session, messenger).send_reminders(t.id, STARTS + timedelta(minutes=1))
    assert messenger.notifications == []


async def test_announce_cancelled_updates_posts_and_notifies_going(session, messenger):
    t = await world(session)
    await svc(session, messenger).publish(t.id, NOW)
    await add_user(session, 1)
    await add_user(session, 2)
    p = ParticipationService(session)
    await p.join(t.id, 1, NOW)
    await p.join(t.id, 2, NOW)
    messenger.unreachable.add(2)  # never pressed /start: must not break the update
    t.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc(session, messenger).announce_cancelled(t.id)
    assert "Отменена" in messenger.edits[-1].text and messenger.edits[-1].keyboard == []
    assert [uid for uid, _ in messenger.notifications if uid in (1, 2)] == [1]
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/publishing/test_service.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

`bot/publishing/service.py`:
```python
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
```

- [ ] **Step 4: Run, expect pass, commit**

Run: `python -m pytest tests/publishing -v` → PASS.
```bash
git add -A
git commit -m "feat(publishing): publish, refresh, close, reminders, cancel announcement"
```

---

### Task 11: Job handlers, runner, bootstrap

**Files:**
- Create: `bot/jobs/handlers.py`, `bot/jobs/runner.py`, `bot/bootstrap.py`
- Test: `tests/jobs/test_runner.py`

**Interfaces:**
- Consumes: `ScheduleService.generate`, `PublishService.*`, queue functions, `UserService.sync_admins`, `set_setting_if_absent`.
- Produces:
  - `handle_job(session, messenger, job, now)` — dispatch by `job.kind`; unknown kind raises `ValueError`.
  - `run_due_jobs(session_factory, messenger, now) -> int` — each job in its own session/transaction; on exception: log, roll back, leave `done_at` unset (retried next tick); returns number of jobs completed. After a `generate` job completes, enqueues the next one for `now + 24 h`.
  - `scheduler_loop(session_factory, messenger, interval=30.0, now_fn=utcnow)`.
  - `bootstrap(session_factory, cfg, now)` — syncs admins, sets `club_timezone` if absent, ensures a pending `generate` job.

- [ ] **Step 1: Write the failing test**

`tests/jobs/test_runner.py`:
```python
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from bot.bootstrap import bootstrap
from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind, enqueue
from bot.jobs.runner import run_due_jobs
from bot.participation.service import ParticipationService
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput
from bot.users.models import User
from tests.factories import add_chat, add_level, add_user

T0 = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)  # created here
STARTS = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)  # Tue 19:00 MSK


async def setup(session_factory):
    async with session_factory() as s:
        admin = await add_user(s, 99, "admin")
        level = await add_level(s)
        await add_chat(s, level, -1001)
        await ScheduleService(s).create_series(
            admin,
            SeriesInput(kind="intervals", place="Парк", level_ids=[level.id],
                        start_date=date(2026, 10, 20), start_time=time(19, 0)),
            T0,
        )


async def get_training(session_factory):
    async with session_factory() as s:
        return await s.scalar(select(Training))


async def test_publish_job_runs_when_due_and_only_once(session_factory, messenger):
    await setup(session_factory)
    await run_due_jobs(session_factory, messenger, T0)  # publish is due at STARTS-24h = T0+6h: not yet
    assert messenger.sent == []
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=24))
    assert len(messenger.sent) == 1
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=23))
    assert len(messenger.sent) == 1  # idempotent


async def test_full_lifecycle_reminder_and_close(session_factory, messenger):
    await setup(session_factory)
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=24))
    t = await get_training(session_factory)
    async with session_factory() as s:
        await add_user(s, 1)
        await ParticipationService(s).join(t.id, 1, STARTS - timedelta(hours=10))
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=2))
    assert [uid for uid, _ in messenger.notifications] == [1]
    await run_due_jobs(session_factory, messenger, STARTS)
    assert (await get_training(session_factory)).status == TrainingStatus.DONE


async def test_catch_up_after_downtime_runs_everything_in_order(session_factory, messenger):
    await setup(session_factory)
    # the bot was down until after the start: publish is skipped (started), close is applied
    await run_due_jobs(session_factory, messenger, STARTS + timedelta(minutes=10))
    assert messenger.sent == []
    assert (await get_training(session_factory)).status == TrainingStatus.DONE
    async with session_factory() as s:
        pending = list(await s.scalars(select(ScheduledJob).where(ScheduledJob.done_at.is_(None))))
    assert pending == []


async def test_failing_job_is_retried_next_tick(session_factory, messenger):
    await setup(session_factory)
    original = messenger.send
    calls = {"n": 0}

    async def flaky(chat_id, text, keyboard=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network down")
        return await original(chat_id, text, keyboard)

    messenger.send = flaky
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=24))
    assert messenger.sent == []
    await run_due_jobs(session_factory, messenger, STARTS - timedelta(hours=23, minutes=59))
    assert len(messenger.sent) == 1


async def test_generate_job_reschedules_itself(session_factory, messenger):
    async with session_factory() as s:
        await enqueue(s, JobKind.GENERATE, T0)
        await s.commit()
    assert await run_due_jobs(session_factory, messenger, T0) == 1
    async with session_factory() as s:
        jobs = list(await s.scalars(select(ScheduledJob).order_by(ScheduledJob.id)))
    assert [j.done_at is not None for j in jobs] == [True, False]
    assert jobs[1].run_at == T0 + timedelta(hours=24)


async def test_bootstrap_is_idempotent(session_factory):
    cfg = SimpleNamespace(admin_ids=[7], club_timezone="Asia/Yekaterinburg")
    await bootstrap(session_factory, cfg, T0)
    await bootstrap(session_factory, cfg, T0)
    async with session_factory() as s:
        assert (await s.get(User, 7)).role == "admin"
        jobs = list(await s.scalars(select(ScheduledJob).where(ScheduledJob.kind == JobKind.GENERATE)))
        from bot.core.settings import get_setting

        assert await get_setting(s, "club_timezone") == "Asia/Yekaterinburg"
    assert len(jobs) == 1
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/jobs/test_runner.py -v` → FAIL (modules missing).

- [ ] **Step 3: Implement**

`bot/jobs/handlers.py`:
```python
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import Messenger
from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind
from bot.publishing.service import PublishService
from bot.schedule.service import ScheduleService


async def handle_job(session: AsyncSession, messenger: Messenger, job: ScheduledJob, now: datetime) -> None:
    """Handlers are idempotent: they re-check state, so a retry or a late run is safe."""
    if job.kind == JobKind.GENERATE:
        await ScheduleService(session).generate(now)
    elif job.kind == JobKind.PUBLISH:
        await PublishService(session, messenger).publish(job.training_id, now)
    elif job.kind == JobKind.REMIND_2H:
        await PublishService(session, messenger).send_reminders(job.training_id, now)
    elif job.kind == JobKind.CLOSE:
        await PublishService(session, messenger).close(job.training_id, now)
    else:
        raise ValueError(f"unknown job kind {job.kind!r}")
```

`bot/jobs/runner.py`:
```python
import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.core.db import utcnow
from bot.core.messenger import Messenger
from bot.jobs.handlers import handle_job
from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind, due_job_ids, ensure_generate_job

log = logging.getLogger(__name__)


async def run_due_jobs(
    session_factory: async_sessionmaker[AsyncSession], messenger: Messenger, now: datetime
) -> int:
    async with session_factory() as session:
        job_ids = await due_job_ids(session, now)
    completed = 0
    for job_id in job_ids:
        async with session_factory() as session:
            job = await session.get(ScheduledJob, job_id)
            if job is None or job.done_at is not None:
                continue
            try:
                await handle_job(session, messenger, job, now)
                job.done_at = now
                await session.flush()
                if job.kind == JobKind.GENERATE:
                    await ensure_generate_job(session, now + timedelta(hours=24))
                await session.commit()
                completed += 1
            except Exception:
                log.exception("job %s (%s) failed; will retry", job_id, job.kind)
                await session.rollback()
    return completed


async def scheduler_loop(
    session_factory: async_sessionmaker[AsyncSession],
    messenger: Messenger,
    interval: float = 30.0,
    now_fn: Callable[[], datetime] = utcnow,
) -> None:
    while True:
        try:
            await run_due_jobs(session_factory, messenger, now_fn())
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)
```

`bot/bootstrap.py`:
```python
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.core.config import Config
from bot.core.settings import set_setting_if_absent
from bot.jobs.queue import ensure_generate_job
from bot.users.service import UserService


async def bootstrap(session_factory: async_sessionmaker[AsyncSession], cfg: Config, now: datetime) -> None:
    async with session_factory() as session:
        await UserService(session).sync_admins(cfg.admin_ids)
        await set_setting_if_absent(session, "club_timezone", cfg.club_timezone)
        await ensure_generate_job(session, now)
        await session.commit()
```

- [ ] **Step 4: Run, expect pass, commit**

Run: `python -m pytest tests/jobs -v` → PASS.
```bash
git add -A
git commit -m "feat(jobs): scheduler runner, handlers, bootstrap"
```

---

### Task 12: Use cases

**Files:**
- Create: `bot/usecases.py`
- Test: `tests/test_usecases.py`

**Interfaces:**
- Consumes: `ParticipationService`, `PublishService`, `ScheduleService`.
- Produces (all commit via the services; `now` is passed in):
  - `join_training(session, messenger, training_id, user_id, now) -> JoinOutcome` — refreshes posts only on `JOINED`.
  - `decline_training(session, messenger, training_id, user_id, now) -> str | None` — refreshes posts only if the user was `going`. *(Plan 3 adds cancelling the user's inventory orders here.)*
  - `cancel_trainings(session, messenger, actor, training_ids, now) -> list[Training]` — cancels, then `announce_cancelled` for each. *(Plan 3 adds order cancellation.)*

- [ ] **Step 1: Write the failing test**

`tests/test_usecases.py`:
```python
from datetime import timedelta

import pytest

from bot.core.errors import PermissionDenied
from bot.participation.service import JoinOutcome
from bot.publishing.service import PublishService
from bot.usecases import cancel_trainings, decline_training, join_training
from tests.factories import NOW, add_chat, add_level, add_training, add_user


async def published_training(session, messenger, capacity=1):
    await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_chat(session, level, -1001)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20), capacity=capacity
    )
    await PublishService(session, messenger, send_delay=0).publish(t.id, NOW)
    return t


async def test_join_button_flow_updates_the_post(session, messenger):
    t = await published_training(session, messenger)
    await add_user(session, 1)
    assert await join_training(session, messenger, t.id, 1, NOW) == JoinOutcome.JOINED
    assert "Идут: 1/1" in messenger.edits[-1].text


async def test_full_or_repeated_join_does_not_touch_the_post(session, messenger):
    t = await published_training(session, messenger)
    await add_user(session, 1)
    await add_user(session, 2)
    await join_training(session, messenger, t.id, 1, NOW)
    edits = len(messenger.edits)
    assert await join_training(session, messenger, t.id, 2, NOW) == JoinOutcome.FULL
    assert await join_training(session, messenger, t.id, 1, NOW) == JoinOutcome.ALREADY
    assert len(messenger.edits) == edits


async def test_decline_frees_the_seat_in_the_post(session, messenger):
    t = await published_training(session, messenger)
    await add_user(session, 1)
    await join_training(session, messenger, t.id, 1, NOW)
    assert await decline_training(session, messenger, t.id, 1, NOW) == "going"
    assert "Идут: 0/1" in messenger.edits[-1].text


async def test_cancel_announces_to_posts_and_going_users(session, messenger):
    t = await published_training(session, messenger, capacity=5)
    admin = await add_user(session, 98, "admin")
    await add_user(session, 1)
    await join_training(session, messenger, t.id, 1, NOW)
    await cancel_trainings(session, messenger, admin, [t.id], NOW)
    assert "Отменена" in messenger.edits[-1].text
    assert (1, ) == tuple(uid for uid, text in messenger.notifications if "отменена" in text)


async def test_cancel_by_stranger_changes_nothing(session, messenger):
    t = await published_training(session, messenger)
    member = await add_user(session, 5, "member")
    edits = len(messenger.edits)
    with pytest.raises(PermissionDenied):
        await cancel_trainings(session, messenger, member, [t.id], NOW)
    assert len(messenger.edits) == edits
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_usecases.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

`bot/usecases.py`:
```python
"""Operations that span several features. Telegram handlers call these, never the services directly
for state-changing actions that must also update posts."""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import Messenger
from bot.participation.models import ParticipationStatus
from bot.participation.service import JoinOutcome, ParticipationService
from bot.publishing.service import PublishService
from bot.schedule.models import Training
from bot.schedule.service import ScheduleService
from bot.users.models import User


async def join_training(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int, now: datetime
) -> JoinOutcome:
    outcome = await ParticipationService(session).join(training_id, user_id, now)
    if outcome == JoinOutcome.JOINED:
        await PublishService(session, messenger).refresh_posts(training_id)
    return outcome


async def decline_training(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int, now: datetime
) -> str | None:
    previous = await ParticipationService(session).decline(training_id, user_id, now)
    if previous == ParticipationStatus.GOING:
        await PublishService(session, messenger).refresh_posts(training_id)
    return previous


async def cancel_trainings(
    session: AsyncSession, messenger: Messenger, actor: User, training_ids: list[int], now: datetime
) -> list[Training]:
    cancelled = await ScheduleService(session).cancel(actor, training_ids, now)
    publisher = PublishService(session, messenger)
    for training in cancelled:
        await publisher.announce_cancelled(training.id)
    return cancelled
```

- [ ] **Step 4: Run, expect pass, commit**

Run: `python -m pytest tests/test_usecases.py -v` → PASS (5 tests).
```bash
git add -A
git commit -m "feat: use cases joining services with post updates"
```

---

### Task 13: Telegram layer, entrypoint, seed script

**Files:**
- Create: `bot/telegram/keyboards.py`, `bot/telegram/messenger.py`, `bot/telegram/middleware.py`, `bot/telegram/errors.py`
- Create: `bot/telegram/handlers/common.py`, `admin.py`, `schedule.py`, `participation.py`
- Create: `bot/main.py`, `scripts/seed_demo.py`
- Test: `tests/telegram/test_keyboards.py`

**Interfaces:**
- Consumes: everything above. Handlers receive `session`, `user`, `messenger` by aiogram dependency injection (`dp.start_polling(bot, messenger=..., session_factory=...)` and the middleware's `data["session"]` / `data["user"]`).
- Produces: `to_markup(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None` (`None` → `None`; `[]` → empty markup that removes buttons); `AiogramMessenger(bot)` implementing `Messenger`; `python -m bot.main`.

- [ ] **Step 1: Write the failing test**

`tests/telegram/test_keyboards.py`:
```python
from aiogram.types import InlineKeyboardMarkup

from bot.core.messenger import Button
from bot.telegram.keyboards import to_markup


def test_none_stays_none():
    assert to_markup(None) is None


def test_empty_keyboard_becomes_empty_markup_that_removes_buttons():
    markup = to_markup([])
    assert isinstance(markup, InlineKeyboardMarkup) and markup.inline_keyboard == []


def test_callback_and_url_buttons():
    markup = to_markup([[Button("A", "join:1"), Button("B", url="https://t.me/x")]])
    a, b = markup.inline_keyboard[0]
    assert (a.text, a.callback_data, a.url) == ("A", "join:1", None)
    assert (b.text, b.callback_data, b.url) == ("B", None, "https://t.me/x")
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/telegram -v` → FAIL (module missing).

- [ ] **Step 3: Implement adapter pieces**

`bot/telegram/keyboards.py`:
```python
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core.messenger import Keyboard


def to_markup(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    if keyboard is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=b.text, callback_data=b.data, url=b.url) for b in row] for row in keyboard
        ]
    )
```

`bot/telegram/messenger.py`:
```python
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.core.messenger import ChatUnavailable, Keyboard
from bot.telegram.keyboards import to_markup


class AiogramMessenger:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send(self, chat_id: int, text: str, keyboard: Keyboard | None = None) -> int:
        try:
            message = await self.bot.send_message(chat_id, text, reply_markup=to_markup(keyboard))
        except (TelegramForbiddenError, TelegramBadRequest) as exc:  # kicked / chat not found
            raise ChatUnavailable(chat_id) from exc
        return message.message_id

    async def edit_post(self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None = None) -> int:
        try:
            await self.bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=to_markup(keyboard)
            )
        except TelegramBadRequest as exc:
            reason = str(exc)
            if "message is not modified" in reason:
                return message_id
            if "message to edit not found" in reason or "can't be edited" in reason:
                return await self.send(chat_id, text, keyboard)  # post was deleted by hand
            raise ChatUnavailable(chat_id) from exc
        except TelegramForbiddenError as exc:
            raise ChatUnavailable(chat_id) from exc
        return message_id

    async def notify(self, user_id: int, text: str, keyboard: Keyboard | None = None) -> bool:
        try:
            await self.bot.send_message(user_id, text, reply_markup=to_markup(keyboard))
        except (TelegramForbiddenError, TelegramBadRequest):
            return False  # user never pressed /start or blocked the bot
        return True
```

`bot/telegram/middleware.py`:
```python
from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.users.service import UserService


class DbSessionMiddleware(BaseMiddleware):
    """Opens a DB session per update and loads/creates the acting user."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], admin_ids: list[int]) -> None:
        self.session_factory = session_factory
        self.admin_ids = admin_ids

    async def __call__(self, handler, event, data):
        async with self.session_factory() as session:
            data["session"] = session
            tg_user = data.get("event_from_user")
            if tg_user is not None and not tg_user.is_bot:
                chat = data.get("event_chat")
                data["user"] = await UserService(session).upsert_from_telegram(
                    tg_user.id,
                    tg_user.full_name,
                    is_private=chat is not None and chat.type == "private",
                    admin_ids=self.admin_ids,
                )
            return await handler(event, data)
```

`bot/telegram/errors.py`:
```python
from aiogram.types import ErrorEvent

from bot.core.errors import DomainError


async def on_domain_error(event: ErrorEvent) -> bool:
    """Expected business errors become a plain reply; nothing is changed in the DB."""
    update = event.update
    message = event.exception.message if isinstance(event.exception, DomainError) else "Ошибка"
    if update.callback_query is not None:
        await update.callback_query.answer(message, show_alert=True)
    elif update.message is not None:
        await update.message.answer(message)
    return True
```

- [ ] **Step 4: Implement handlers**

`bot/telegram/handlers/common.py`:
```python
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.users.models import User

router = Router()


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, user: User) -> None:
    await message.answer(f"Привет, {user.name}! Я бот велоклуба.\nБлижайшие тренировки: /schedule")


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")
```

`bot/telegram/handlers/admin.py`:
```python
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction
from bot.schedule.service import ScheduleService
from bot.users.models import User

router = Router()


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
```

`bot/telegram/handlers/schedule.py`:
```python
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.messenger import Messenger
from bot.publishing.service import PublishService
from bot.schedule.service import ScheduleService
from bot.telegram.keyboards import to_markup

router = Router()


@router.message(Command("schedule"), F.chat.type == "private")
async def cmd_schedule(message: Message, session: AsyncSession, messenger: Messenger) -> None:
    trainings = await ScheduleService(session).upcoming(utcnow())
    if not trainings:
        await message.answer("Ближайших тренировок нет")
        return
    publisher = PublishService(session, messenger)
    for training in trainings:
        text, keyboard = await publisher.render(training)
        await message.answer(text, reply_markup=to_markup(keyboard))


@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()
```

`bot/telegram/handlers/participation.py`:
```python
from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.messenger import Messenger
from bot.participation.service import JoinOutcome, ParticipationService
from bot.publishing.render import fit_alert, render_participants
from bot.publishing.service import PublishService
from bot.schedule.models import Training
from bot.usecases import decline_training, join_training
from bot.users.models import User

router = Router()

JOIN_TEXT = {
    JoinOutcome.JOINED: "Вы записаны ✅",
    JoinOutcome.ALREADY: "Вы уже записаны",
    JoinOutcome.FULL: "Мест нет",
}


def _training_id(callback: CallbackQuery) -> int:
    return int(callback.data.split(":", 1)[1])


async def _finish(
    callback: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User, training_id: int, text: str
) -> None:
    """Re-render the card in a private chat (group posts are refreshed by the use case) and answer."""
    message = callback.message
    if message is not None and message.chat.type == "private":
        training = await session.get(Training, training_id)
        if training is not None:
            new_text, keyboard = await PublishService(session, messenger).render(training)
            await messenger.edit_post(message.chat.id, message.message_id, new_text, keyboard)
    if not user.started_bot:
        username = (await callback.bot.me()).username
        await callback.answer(f"{text}\nОткройте бота, чтобы получать напоминания: t.me/{username}", show_alert=True)
    else:
        await callback.answer(text)


@router.callback_query(F.data.startswith("join:"))
async def on_join(callback: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    training_id = _training_id(callback)
    outcome = await join_training(session, messenger, training_id, user.telegram_id, utcnow())
    await _finish(callback, session, messenger, user, training_id, JOIN_TEXT[outcome])


@router.callback_query(F.data.startswith("decline:"))
async def on_decline(callback: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    training_id = _training_id(callback)
    await decline_training(session, messenger, training_id, user.telegram_id, utcnow())
    await _finish(callback, session, messenger, user, training_id, "Записали: не идёте")


@router.callback_query(F.data.startswith("who:"))
async def on_who(callback: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    going, declined = await ParticipationService(session).list_participants(_training_id(callback))
    text = render_participants([u.name for u in going], [u.name for u in declined])
    if len(text) <= 190:
        await callback.answer(text, show_alert=True)
    elif user.started_bot and await messenger.notify(user.telegram_id, text):
        await callback.answer("Полный список отправил вам в личные сообщения")
    else:
        await callback.answer(fit_alert(text), show_alert=True)
```

- [ ] **Step 5: Implement the entrypoint and seed script**

`bot/main.py`:
```python
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import ExceptionTypeFilter

from bot.core.config import Config
from bot.core.db import make_engine, make_session_factory, utcnow
from bot.core.errors import DomainError
from bot.bootstrap import bootstrap
from bot.jobs.runner import scheduler_loop
from bot.telegram.errors import on_domain_error
from bot.telegram.handlers import admin, common, participation, schedule
from bot.telegram.messenger import AiogramMessenger
from bot.telegram.middleware import DbSessionMiddleware


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = Config()
    engine = make_engine(cfg.database_url)
    session_factory = make_session_factory(engine)
    await bootstrap(session_factory, cfg, utcnow())

    bot = Bot(cfg.bot_token)
    messenger = AiogramMessenger(bot)
    dp = Dispatcher()
    dp.update.outer_middleware(DbSessionMiddleware(session_factory, cfg.admin_ids))
    dp.errors.register(on_domain_error, ExceptionTypeFilter(DomainError))
    dp.include_routers(common.router, admin.router, schedule.router, participation.router)

    scheduler = asyncio.create_task(scheduler_loop(session_factory, messenger))
    try:
        await dp.start_polling(bot, messenger=messenger)
    finally:
        scheduler.cancel()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

`scripts/seed_demo.py` (manual smoke test: a one-off training tomorrow 19:00 club time, for level «Новички»):
```python
"""Usage: python -m scripts.seed_demo  (after `alembic upgrade head`, with .env filled in)."""
import asyncio
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from bot.bootstrap import bootstrap
from bot.core.config import Config
from bot.core.db import make_engine, make_session_factory, utcnow
from bot.core.settings import get_setting
from bot.schedule.models import Level
from bot.schedule.service import ScheduleService, SeriesInput
from bot.users.models import User


async def main() -> None:
    cfg = Config()
    engine = make_engine(cfg.database_url)
    factory = make_session_factory(engine)
    now = utcnow()
    await bootstrap(factory, cfg, now)
    async with factory() as session:
        admin = await session.get(User, cfg.admin_ids[0])
        level = await session.scalar(select(Level).where(Level.name == "Новички"))
        if level is None:
            level = await ScheduleService(session).add_level(admin, "Новички")
        tz = ZoneInfo(await get_setting(session, "club_timezone"))
        tomorrow = (now.astimezone(tz) + timedelta(days=1)).date()
        from datetime import time

        await ScheduleService(session).create_series(
            admin,
            SeriesInput(kind="intervals", place="Парк", level_ids=[level.id], start_date=tomorrow,
                        start_time=time(19, 0), capacity=10, description="Демо-тренировка"),
            now,
        )
    await engine.dispose()
    print("Seeded. Bind a group with /bind_level Новички; the post appears within 30 s (starts < 24 h ahead).")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -v`
Expected: all tests PASS (no network access is needed anywhere).

- [ ] **Step 7: Manual smoke test (needs a real bot token; do this once)**

1. Copy `.env.example` → `.env`, fill `BOT_TOKEN` and your Telegram ID in `ADMIN_IDS` (`/whoami` shows it).
2. `alembic upgrade head`, then `python -m scripts.seed_demo`, then `python -m bot.main`.
3. Add the bot to a test group, make it admin-less but allowed to post; in the group send `/bind_level Новички`.
4. Within ~30 s the training post appears in the group; press «Пойду» → counter changes; «Участники» → alert with the list; in private `/schedule` shows the card.
5. Stop the bot, wait past the scheduled time (or edit `run_at` in the DB), start again → the training is closed with «Прошла» and no buttons.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(telegram): aiogram adapter, handlers, entrypoint, demo seed"
```

---

## Self-Review

**Spec coverage (Plan 1 scope):**
- Section 3 model: User, Level, Chat, ChatTrainer (incl. partial unique lead index), TrainingSeries/Training with snapshot + levels, TrainingPost, Participation, Setting, ScheduledJob → Tasks 2–4. `InventoryItem/Order` and `Invite` → Plans 3 and 2.
- Section 4 structure and rules (feature split, no repo layer, Messenger, policies called by services, middleware) → Tasks 5, 7, 13. `inventory/` package and `matching.py` → Plan 3.
- Section 5: join/decline with idempotency and limit (Task 8), generate/publish/remind/close with per-chat idempotency and no-chat warning (Tasks 7, 10, 11), DM limits (Tasks 10, 13), cancel (Tasks 7, 12), lead-trainer rights and trainer-without-chat message (Tasks 5, 7), planner semantics and catch-up (Task 11). Series editing, manual add over limit, access control via `getChatMember`, invites, inventory flow → Plans 2 and 3 (explicitly listed above).
- Section 6 UX: `/start`, `/schedule` (with «Откроется …»), group post, «Участники» → Task 9, 13. `/profile`, `/my`, `/trainer`, `/admin` → Plan 2/3.
- Section 7 errors: unavailable chats, deleted posts, rate-limit pacing, invalid actions → Tasks 10, 13.
- Section 8 testing: services, scheduler idempotency and catch-up, rendering, migrations → covered; handler-level mock scenarios are replaced by use-case tests with a fake Messenger (Task 12) plus the manual smoke test.

**Placeholder scan:** no TBD/TODO; the one editorial instruction inside Task 10 (rewriting the last test) is resolved in Step 2 with the exact final code.

**Type consistency:** `Messenger.notify -> bool`, `edit_post -> int`, `JoinOutcome`, `decline -> str | None`, `PublishService.render(training, published=None)`, `ScheduleService.upcoming(now, limit)`, `SeriesInput` fields, `JobKind` constants and `enqueue(session, kind, run_at, training_id)` are used identically across Tasks 7–13.

**Review Focus:** items 1–5 are pinned by tests in Tasks 8, 7, 7, 9 and 10 respectively.
