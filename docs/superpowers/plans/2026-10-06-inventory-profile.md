# Coach's Shadow — План 3: инвентарь, профиль, «Мои записи»

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Цель:** Участник заказывает инвентарь (велосипед, педали, туфли, шлем) из ограниченного парка под параметры своего профиля; тренер видит сводку; админ ведёт парк; `/profile` и `/my` работают.

**Архитектура:** Новая фича `bot/inventory/`: модели, чистая функция подбора `matching.py`, `InventoryService` (подбор, подтверждение с повторной проверкой в транзакции, отмена, сводка). Чистые вспомогательные модули без aiogram: `items.py` (разбор строки единицы парка), `summary.py` (сводка тренера), `flow.py`. Стыки с другими фичами (отказ участника, отмена тренировки, замена при правке серии) делает `bot/usecases.py`. Весь Telegram-код в `bot/telegram/handlers/`.

**Tech Stack:** как в планах 1–2.

**Spec:** `docs/superpowers/specs/2026-10-06-coachs-shadow-design.md`
**Предыдущие планы (должны быть выполнены):** `2026-10-06-engine-schedule-signup.md`, `2026-10-06-roles-trainer-admin-ui.md`.

## Global Constraints

- Язык интерфейса: русский.
- Слоя `repo/` нет; сервис работает с сессией напрямую; запрос свободных единиц — именованная функция рядом с сервисом.
- Подбор — чистая функция `inventory/matching.py`: на входе профиль, кандидаты и совместимость, на выходе предложения с пояснениями. Запрос свободных единиц отдельно.
- Единица занята, если у неё есть активный заказ на пересекающейся по времени тренировке: `a.starts_at < b.ends_at AND b.starts_at < a.ends_at`, где `ends_at = starts_at + duration_min`. Тренировки «встык» (конец одной = начало другой) не пересекаются.
- Диапазоны роста рам и совместимость шипов и педалей хранятся данными (колонки единиц парка и таблица `cleat_compat`), а не в коде.
- Проверки перед заказом: тренировка `scheduled`, не началась, дедлайн (`inventory_deadline_h`, пусто = нет) не прошёл, участник записан (`going`); иначе бот предлагает сначала записаться.
- Недостающие параметры профиля спрашиваются только для выбранных позиций. Тип шипов своих туфель сохраняется в профиль.
- Велосипед: рост в диапазоне рамы, иначе ближайшая допустимая рама с подтверждением. Туфли: размер ноги, шипы совместимы с педалями, иначе ±1 размер. Шлем: размер головы, иначе ближайший. Нет подходящего: «Нет в наличии на это время» и совет написать тренеру.
- Проверка совместимости набора: педали и туфли из парка должны подходить друг другу; свои туфли — тоже проверяются с выбранными педалями.
- Подтверждение итогом; в транзакции повторная проверка свободы единиц; если единицу забрали, замена подбирается заново только для неё, уже подтверждённые позиции сохраняются.
- Отмена заказа доступна до старта, единицы освобождаются сразу. Отказ участника, отмена тренировки и замена тренировки при правке серии отменяют его активные заказы; при отказе — с уведомлением.
- Не входит: оплата (только поля `price`, `paid_status`, `price_per_use`), отметка выдачи/возврата, очередь на инвентарь.
- Группа: кнопка «🚲 Инвентарь» отвечает ссылкой `t.me/<бот>?start=inv_<id>` («Продолжим в личке»).

## Отступления от спецификации (мелкие)

- Цены `price_per_use` и `price` хранятся целым числом в минимальных единицах (копейках): `Numeric` в SQLite даёт предупреждения и неточности.
- Параметры единицы лежат в явных колонках (`frame`, `height_min`, `height_max`, `shoe_eu`, `cleat_type`, `pedal_type`, `helmet_size`); не используемые видом колонки пусты.
- Совместимость хранится в таблице `cleat_compat(cleat_type, pedal_type)`; начальные пары (`none–flat`, `spd–spd`, `look–look`, `spd_sl–spd_sl`) вставляются при старте бота, если их нет.
- Добавление единицы парка — одной строкой через `;` (например, `Велосипед №7; M; 165; 178`), а не многошаговой формой: парк ведёт админ, и это короче.
- Один активный заказ на вид инвентаря на тренировку; чтобы заменить, нужно сначала отменить заказ в `/my`.

## Review Focus

1. **Двое одновременно берут последнюю единицу** — второй не получает заказ, видит замену только для этой позиции, подтверждённые остаются (Задача 4).
2. **Тренировки встык** — конец первой равен началу второй; единица не должна считаться занятой (Задача 4).
3. **Отказ участника / отмена тренировки** — единицы освобождаются сразу, отказавшемуся приходит уведомление (Задача 6).
4. **Свои туфли не подходят к выбранным педалям** — бот говорит об этом сразу и просит выбрать педали заново (Задачи 3, 8).
5. **Мусор в профиле** (рост `17`/`1700`/«высокий», размер ноги `7`, шлем `XXL`) — вежливая отказ-подсказка, профиль не портится (Задача 2).

---

## Структура файлов (новое и изменяемое)

```
bot/
  users/ profile.py(новый) service.py(+update_profile) models.py (без изменений)
  inventory/ models.py items.py matching.py service.py summary.py flow.py
  publishing/ render.py (+кнопка «🚲 Инвентарь»)
  usecases.py (хуки инвентаря)
  bootstrap.py (+начальная совместимость)
  core/settings.py (validate_setting: «-» для дедлайна)
  telegram/ states.py(+) handlers/ profile.py my.py inventory.py park.py ; common.py(+inv_), admin.py(+меню парка), trainer.py(+кнопка сводки)
alembic/versions/<rev>_inventory.py
tests/ (зеркалит; factories.add_item)
```

---

### Task 1: Модели инвентаря, миграция, совместимость

**Files:**
- Create: `bot/inventory/__init__.py`, `bot/inventory/models.py`, `tests/inventory/__init__.py`
- Modify: `bot/registry.py`, `tests/factories.py`, `bot/bootstrap.py`
- Create: `alembic/versions/<rev>_inventory.py`
- Test: `tests/inventory/test_models.py`, `tests/test_migrations.py` (дополнить)

**Interfaces:**
- Produces: `ItemKind.BIKE/PEDALS/SHOES/HELMET = "bike"/"pedals"/"shoes"/"helmet"`, `ItemKind.ALL`; `OrderStatus.ACTIVE/CANCELLED = "active"/"cancelled"`;
  `InventoryItem(id, kind, label, active, frame, height_min, height_max, shoe_eu, cleat_type, pedal_type, helmet_size, price_per_use)`;
  `InventoryOrder(id, training_id, user_id, item_id, status, price, paid_status, created_at)`;
  `CleatCompat(cleat_type, pedal_type)` (составной PK); `DEFAULT_COMPAT`; `ensure_cleat_compat(session)` (вставляет недостающие пары; идемпотентно);
  фабрика `add_item(session, kind, label, **params) -> InventoryItem`.

- [ ] **Step 1: Написать падающие тесты**

`tests/inventory/test_models.py`:
```python
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.inventory.models import CleatCompat, InventoryOrder, ensure_cleat_compat
from tests.factories import NOW, add_item, add_level, add_training, add_user
from datetime import timedelta


async def test_item_roundtrip_and_defaults(session):
    item = await add_item(session, "bike", "Велосипед №7", frame="M", height_min=165, height_max=178)
    session.expire_all()
    fresh = await session.get(type(item), item.id)
    assert (fresh.kind, fresh.active, fresh.frame, fresh.price_per_use) == ("bike", True, "M", None)


async def test_order_requires_existing_item_and_training(session):
    await add_user(session, 1)
    session.add(InventoryOrder(training_id=1, user_id=1, item_id=1, status="active", created_at=NOW))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_ensure_cleat_compat_is_idempotent(session):
    await ensure_cleat_compat(session)
    await ensure_cleat_compat(session)
    pairs = {(r.cleat_type, r.pedal_type) for r in await session.scalars(select(CleatCompat))}
    assert pairs == {("none", "flat"), ("spd", "spd"), ("look", "look"), ("spd_sl", "spd_sl")}


async def test_order_can_be_created(session):
    trainer = await add_user(session, 10, "trainer")
    rider = await add_user(session, 1)
    level = await add_level(session)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20))
    item = await add_item(session, "helmet", "Шлем №2", helmet_size="M")
    session.add(InventoryOrder(training_id=t.id, user_id=rider.telegram_id, item_id=item.id, status="active", created_at=NOW))
    await session.commit()
```

В `tests/test_migrations.py` в проверку множества таблиц добавить `"inventory_items", "inventory_orders", "cleat_compat"`.

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/inventory/test_models.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

Создать пустые `bot/inventory/__init__.py` и `tests/inventory/__init__.py`.

`bot/inventory/models.py`:
```python
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base, UTCDateTime, utcnow


class ItemKind:
    BIKE = "bike"
    PEDALS = "pedals"
    SHOES = "shoes"
    HELMET = "helmet"
    ALL = (BIKE, PEDALS, SHOES, HELMET)


class OrderStatus:
    ACTIVE = "active"
    CANCELLED = "cancelled"


class InventoryItem(Base):
    """Конкретная единица парка («Велосипед №7»). Неиспользуемые видом колонки пусты."""

    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    label: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    frame: Mapped[str | None] = mapped_column(String(8))  # велосипед: размер рамы
    height_min: Mapped[int | None] = mapped_column(Integer)  # велосипед: диапазон роста, см
    height_max: Mapped[int | None] = mapped_column(Integer)
    shoe_eu: Mapped[int | None] = mapped_column(Integer)  # туфли
    cleat_type: Mapped[str | None] = mapped_column(String(16))  # туфли: none/spd/look/spd_sl
    pedal_type: Mapped[str | None] = mapped_column(String(16))  # педали: flat/spd/look/spd_sl
    helmet_size: Mapped[str | None] = mapped_column(String(2))  # шлем: S/M/L
    price_per_use: Mapped[int | None] = mapped_column(Integer)  # в копейках, под оплату


class InventoryOrder(Base):
    __tablename__ = "inventory_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    training_id: Mapped[int] = mapped_column(ForeignKey("trainings.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.ACTIVE)
    price: Mapped[int | None] = mapped_column(Integer)
    paid_status: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CleatCompat(Base):
    """Какие туфли (по типу шипов) подходят к каким педалям. Данные, а не код."""

    __tablename__ = "cleat_compat"

    cleat_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    pedal_type: Mapped[str] = mapped_column(String(16), primary_key=True)


DEFAULT_COMPAT = (("none", "flat"), ("spd", "spd"), ("look", "look"), ("spd_sl", "spd_sl"))


async def ensure_cleat_compat(session: AsyncSession) -> None:
    existing = {(r.cleat_type, r.pedal_type) for r in await session.scalars(select(CleatCompat))}
    for cleat, pedal in DEFAULT_COMPAT:
        if (cleat, pedal) not in existing:
            session.add(CleatCompat(cleat_type=cleat, pedal_type=pedal))
    await session.commit()
```

В `bot/registry.py` добавить `import bot.inventory.models  # noqa: F401`.

В `tests/factories.py` добавить:
```python
from bot.inventory.models import InventoryItem


async def add_item(session, kind, label, **params) -> InventoryItem:
    item = InventoryItem(kind=kind, label=label, **params)
    session.add(item)
    await session.commit()
    return item
```

В `bot/bootstrap.py` добавить импорт `from bot.inventory.models import ensure_cleat_compat` и вызов `await ensure_cleat_compat(session)` внутри `bootstrap` перед `await session.commit()`.

- [ ] **Step 4: Миграция**

Run (PowerShell):
```powershell
$env:DATABASE_URL = "sqlite+aiosqlite:///alembic_tmp.db"
alembic upgrade head
alembic revision --autogenerate -m "inventory"
Remove-Item alembic_tmp.db -ErrorAction SilentlyContinue
Remove-Item Env:DATABASE_URL
```
Проверить сгенерированный файл: создаются три таблицы `inventory_items`, `inventory_orders`, `cleat_compat`, индексы; никаких изменений существующих таблиц (если autogenerate предлагает трогать `trainings` или индекс `uq_training_series_start` — удалить эти операции вручную: Alembic путает частичный индекс).

- [ ] **Step 5: Прогнать, закоммитить**

Run: `python -m pytest tests/inventory tests/test_migrations.py -v` → PASS.
```bash
git add -A
git commit -m "feat(inventory): models, cleat compatibility data, migration"
```

---

### Task 2: Профиль участника: разбор и сохранение

**Files:**
- Create: `bot/users/profile.py`
- Modify: `bot/users/service.py` (`update_profile`)
- Test: `tests/users/test_profile.py`

**Interfaces:**
- Produces:
  - `CLEAT_TYPES = ("none", "spd", "look", "spd_sl")`, `CLEAT_LABELS`, `HELMET_SIZES = ("S", "M", "L")`;
  - `parse_height(text) -> int` (100–230), `parse_foot(text) -> int` (30–52), `parse_helmet(text) -> str` (S/M/L, принимает латиницу и кириллицу `с/м/л`), `parse_cleat(text) -> str`;
  - `PROFILE_FIELDS = {"height_cm": parse_height, "foot_eu": parse_foot, "helmet_size": parse_helmet, "cleat_type": parse_cleat}`; `PROFILE_PROMPTS`;
  - `UserService.update_profile(user, field, raw: str) -> User` (разбор + сохранение; неизвестное поле → `InvalidAction`).
  - `render_profile(user) -> str`.

- [ ] **Step 1: Написать падающий тест**

`tests/users/test_profile.py`:
```python
import pytest

from bot.core.errors import InvalidAction
from bot.users.profile import parse_cleat, parse_foot, parse_height, parse_helmet, render_profile
from bot.users.service import UserService
from tests.factories import add_user


def test_parse_height():
    assert parse_height(" 175 ") == 175
    assert parse_height("175 см") == 175


@pytest.mark.parametrize("bad", ["17", "1700", "высокий", "", "-170", "175.5"])
def test_parse_height_rejects_garbage(bad):
    with pytest.raises(InvalidAction, match="рост"):
        parse_height(bad)


def test_parse_foot():
    assert parse_foot("42") == 42
    for bad in ("7", "99", "сорок два", ""):
        with pytest.raises(InvalidAction, match="размер"):
            parse_foot(bad)


def test_parse_helmet_accepts_latin_and_cyrillic():
    assert parse_helmet("m") == "M"
    assert parse_helmet("Л") == "L"
    assert parse_helmet("с") == "S"
    for bad in ("XXL", "", "большой"):
        with pytest.raises(InvalidAction, match="S, M или L"):
            parse_helmet(bad)


def test_parse_cleat():
    assert parse_cleat("spd_sl") == "spd_sl"
    assert parse_cleat("NONE") == "none"
    with pytest.raises(InvalidAction):
        parse_cleat("xyz")


async def test_update_profile_saves_only_valid_values(session):
    user = await add_user(session, 1)
    svc = UserService(session)
    await svc.update_profile(user, "height_cm", "180")
    assert user.height_cm == 180
    with pytest.raises(InvalidAction):
        await svc.update_profile(user, "height_cm", "1700")
    assert user.height_cm == 180  # профиль не испорчен
    with pytest.raises(InvalidAction):
        await svc.update_profile(user, "name", "Хакер")


def test_render_profile():
    from bot.users.models import User

    empty = User(telegram_id=1, name="Анна")
    assert "не указан" in render_profile(empty)
    full = User(telegram_id=1, name="Анна", height_cm=170, foot_eu=39, helmet_size="M", cleat_type="spd")
    text = render_profile(full)
    assert "170" in text and "39" in text and "M" in text and "SPD" in text
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/users/test_profile.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

`bot/users/profile.py`:
```python
import re

from bot.core.errors import InvalidAction
from bot.users.models import User

CLEAT_TYPES = ("none", "spd", "look", "spd_sl")
CLEAT_LABELS = {"none": "без шипов (плоские педали)", "spd": "SPD", "look": "Look", "spd_sl": "SPD-SL"}
HELMET_SIZES = ("S", "M", "L")
_CYRILLIC_HELMET = {"с": "S", "м": "M", "л": "L"}


def parse_height(text: str) -> int:
    match = re.fullmatch(r"(\d{2,3})(?:\s*см)?", text.strip().lower())
    if match and 100 <= int(match[1]) <= 230:
        return int(match[1])
    raise InvalidAction("Введите рост в сантиметрах, например 175")


def parse_foot(text: str) -> int:
    value = text.strip()
    if value.isdigit() and 30 <= int(value) <= 52:
        return int(value)
    raise InvalidAction("Введите размер ноги (EU), например 42")


def parse_helmet(text: str) -> str:
    value = text.strip().lower()
    value = _CYRILLIC_HELMET.get(value, value.upper())
    if value in HELMET_SIZES:
        return value
    raise InvalidAction("Размер шлема: S, M или L")


def parse_cleat(text: str) -> str:
    value = text.strip().lower()
    if value in CLEAT_TYPES:
        return value
    raise InvalidAction("Тип шипов: none, spd, look или spd_sl")


PROFILE_FIELDS = {
    "height_cm": parse_height,
    "foot_eu": parse_foot,
    "helmet_size": parse_helmet,
    "cleat_type": parse_cleat,
}
PROFILE_PROMPTS = {
    "height_cm": "Ваш рост в сантиметрах, например 175:",
    "foot_eu": "Размер ноги (EU), например 42:",
}


def render_profile(user: User) -> str:
    def show(value, fmt=str):
        return fmt(value) if value is not None else "не указан"

    return (
        f"Профиль: {user.name}\n"
        f"Рост: {show(user.height_cm, lambda v: f'{v} см')}\n"
        f"Размер ноги: {show(user.foot_eu)}\n"
        f"Размер шлема: {show(user.helmet_size)}\n"
        f"Шипы своих туфель: {show(user.cleat_type, lambda v: CLEAT_LABELS[v])}"
    )
```

В `bot/users/service.py` добавить импорты `from bot.core.errors import InvalidAction`, `from bot.users.profile import PROFILE_FIELDS` и метод:
```python
    async def update_profile(self, user: User, field: str, raw: str) -> User:
        parser = PROFILE_FIELDS.get(field)
        if parser is None:
            raise InvalidAction("Это поле профиля нельзя изменить")
        setattr(user, field, parser(raw))  # сначала разбор: при ошибке профиль не меняется
        await self.session.commit()
        return user
```

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/users -v` → PASS.
```bash
git add -A
git commit -m "feat(users): profile parsing and update"
```

---

### Task 3: Подбор инвентаря (чистая логика) и разбор строки парка

**Files:**
- Create: `bot/inventory/matching.py`, `bot/inventory/items.py`
- Test: `tests/inventory/test_matching.py`, `tests/inventory/test_items.py`

**Interfaces:**
- Consumes: `ItemKind`, `CLEAT_TYPES`, `HELMET_SIZES`.
- Produces (`matching.py`, без БД и aiogram):
  - `Candidate(id, kind, label, frame=None, height_min=None, height_max=None, shoe_eu=None, cleat_type=None, pedal_type=None, helmet_size=None)`; `Profile(height_cm, foot_eu, helmet_size)`; `Wanted(kinds: frozenset[str], pedal_type: str | None = None, own_shoes_cleat: str | None = None)` — `kinds` это позиции **из парка** (свои туфли в `kinds` не входят, их тип шипов в `own_shoes_cleat`);
  - `MatchStatus.EXACT/NEAREST/NONE`; `Suggestion(kind, status, item_id | None, text)`;
  - `suggest(profile, wanted, candidates, compat: set[tuple[str, str]]) -> list[Suggestion]` (порядок: велосипед, педали, туфли, шлем; только запрошенные);
  - `missing_profile_fields(profile, kinds) -> list[str]` (`height_cm`, `foot_eu`, `helmet_size` — только нужные выбранным позициям);
  - `own_shoes_problem(own_cleat, pedal_type, compat) -> str | None`; `set_problems(items: list[Candidate], compat) -> list[str]`.
  - `PEDAL_TYPES = ("flat", "spd", "look", "spd_sl")`, `PEDAL_LABELS`, `KIND_LABELS`.
- Produces (`items.py`): `ITEM_FORMATS: dict[kind, str]`; `parse_item_line(kind, text) -> tuple[str, dict]` (метка и параметры, проверенные); бросает `InvalidAction` с примером формата.

- [ ] **Step 1: Написать падающие тесты**

`tests/inventory/test_matching.py`:
```python
import pytest

from bot.inventory.matching import (
    Candidate, MatchStatus, Profile, Wanted, missing_profile_fields, own_shoes_problem, set_problems, suggest,
)

COMPAT = {("none", "flat"), ("spd", "spd"), ("look", "look"), ("spd_sl", "spd_sl")}


def bike(id, frame, lo, hi):
    return Candidate(id=id, kind="bike", label=f"Велосипед №{id}", frame=frame, height_min=lo, height_max=hi)


def shoes(id, size, cleat):
    return Candidate(id=id, kind="shoes", label=f"Туфли №{id}", shoe_eu=size, cleat_type=cleat)


def pedals(id, kind):
    return Candidate(id=id, kind="pedals", label=f"Педали №{id}", pedal_type=kind)


def helmet(id, size):
    return Candidate(id=id, kind="helmet", label=f"Шлем №{id}", helmet_size=size)


def one(kinds, profile, items, pedal_type=None):
    result = suggest(profile, Wanted(frozenset(kinds), pedal_type=pedal_type), items, COMPAT)
    assert len(result) == 1
    return result[0]


# ---- велосипед ----

def test_bike_exact_by_height_range_lowest_id_wins():
    s = one({"bike"}, Profile(172, None, None), [bike(2, "M", 165, 178), bike(1, "M", 165, 178), bike(3, "L", 178, 190)])
    assert (s.status, s.item_id) == (MatchStatus.EXACT, 1)


def test_bike_nearest_when_no_range_fits_with_explanation():
    s = one({"bike"}, Profile(195, None, None), [bike(1, "M", 165, 178), bike(2, "L", 178, 190)])
    assert (s.status, s.item_id) == (MatchStatus.NEAREST, 2)
    assert "195" in s.text and "178" in s.text and "190" in s.text


def test_bike_none_when_park_is_empty():
    s = one({"bike"}, Profile(172, None, None), [])
    assert (s.status, s.item_id) == (MatchStatus.NONE, None)
    assert "нет в наличии" in s.text.lower() and "тренер" in s.text.lower()


# ---- педали ----

def test_pedals_by_type_only():
    s = one({"pedals"}, Profile(None, None, None), [pedals(1, "spd"), pedals(2, "look")], pedal_type="look")
    assert (s.status, s.item_id) == (MatchStatus.EXACT, 2)
    s = one({"pedals"}, Profile(None, None, None), [pedals(1, "spd")], pedal_type="look")
    assert s.status == MatchStatus.NONE


# ---- туфли ----

def test_shoes_exact_size_and_compatible_cleats_with_chosen_pedals():
    items = [shoes(1, 42, "look"), shoes(2, 42, "spd")]
    s = one({"shoes"}, Profile(None, 42, None), items, pedal_type="spd")
    assert (s.status, s.item_id) == (MatchStatus.EXACT, 2)


def test_shoes_nearest_is_plus_minus_one_prefers_larger():
    items = [shoes(1, 41, "spd"), shoes(2, 43, "spd")]
    s = one({"shoes"}, Profile(None, 42, None), items, pedal_type="spd")
    assert (s.status, s.item_id) == (MatchStatus.NEAREST, 2)
    assert "42" in s.text


def test_shoes_two_sizes_off_is_not_a_match():
    s = one({"shoes"}, Profile(None, 42, None), [shoes(1, 44, "spd")], pedal_type="spd")
    assert s.status == MatchStatus.NONE


def test_shoes_without_pedal_choice_ignore_cleat_type():
    s = one({"shoes"}, Profile(None, 42, None), [shoes(1, 42, "look")])
    assert s.status == MatchStatus.EXACT


# ---- шлем ----

def test_helmet_exact_nearest_and_tie_prefers_larger():
    assert one({"helmet"}, Profile(None, None, "M"), [helmet(1, "S"), helmet(2, "M")]).item_id == 2
    s = one({"helmet"}, Profile(None, None, "M"), [helmet(1, "S"), helmet(2, "L")])
    assert (s.status, s.item_id) == (MatchStatus.NEAREST, 2)
    s = one({"helmet"}, Profile(None, None, "S"), [helmet(1, "L")])
    assert (s.status, s.item_id) == (MatchStatus.NEAREST, 1)
    assert one({"helmet"}, Profile(None, None, "M"), []).status == MatchStatus.NONE


# ---- общее ----

def test_only_wanted_kinds_in_fixed_order():
    items = [bike(1, "M", 160, 180), helmet(2, "M")]
    result = suggest(Profile(170, None, "M"), Wanted(frozenset({"helmet", "bike"})), items, COMPAT)
    assert [s.kind for s in result] == ["bike", "helmet"]


def test_missing_profile_fields_only_for_selected_kinds():
    empty = Profile(None, None, None)
    assert missing_profile_fields(empty, {"pedals"}) == []
    assert missing_profile_fields(empty, {"bike", "helmet"}) == ["height_cm", "helmet_size"]
    assert missing_profile_fields(Profile(170, None, None), {"bike", "shoes"}) == ["foot_eu"]


def test_own_shoes_problem():
    assert own_shoes_problem("spd", "spd", COMPAT) is None
    assert own_shoes_problem("spd", None, COMPAT) is None
    assert own_shoes_problem(None, "spd", COMPAT) is None
    message = own_shoes_problem("spd_sl", "spd", COMPAT)
    assert "SPD-SL" in message and "SPD" in message


def test_set_problems_flags_incompatible_park_pedals_and_shoes():
    assert set_problems([pedals(1, "spd"), shoes(2, 42, "spd")], COMPAT) == []
    problems = set_problems([pedals(1, "look"), shoes(2, 42, "spd")], COMPAT)
    assert len(problems) == 1 and "Педали №1" in problems[0] and "Туфли №2" in problems[0]
    assert set_problems([bike(1, "M", 1, 2), shoes(2, 42, "spd")], COMPAT) == []
```

`tests/inventory/test_items.py`:
```python
import pytest

from bot.core.errors import InvalidAction
from bot.inventory.items import parse_item_line


def test_each_kind_parses_into_label_and_params():
    assert parse_item_line("bike", "Велосипед №7; M; 165; 178") == (
        "Велосипед №7", {"frame": "M", "height_min": 165, "height_max": 178})
    assert parse_item_line("pedals", "Педали №3; SPD") == ("Педали №3", {"pedal_type": "spd"})
    assert parse_item_line("shoes", "Туфли №5; 42; spd_sl") == ("Туфли №5", {"shoe_eu": 42, "cleat_type": "spd_sl"})
    assert parse_item_line("helmet", "Шлем №2; m") == ("Шлем №2", {"helmet_size": "M"})


@pytest.mark.parametrize("kind,line", [
    ("bike", "Велосипед; M; 178; 165"),   # min > max
    ("bike", "Велосипед; M; 165"),        # мало полей
    ("bike", "; M; 165; 178"),            # пустая метка
    ("pedals", "Педали; round"),
    ("shoes", "Туфли; 7; spd"),           # размер вне диапазона
    ("shoes", "Туфли; 42; xyz"),
    ("helmet", "Шлем; XXL"),
    ("kayak", "Лодка; 1"),
])
def test_invalid_lines_are_rejected_with_a_format_hint(kind, line):
    with pytest.raises(InvalidAction):
        parse_item_line(kind, line)


def test_error_contains_example_format():
    with pytest.raises(InvalidAction, match="Велосипед №7; M; 165; 178"):
        parse_item_line("bike", "что-то")
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/inventory/test_matching.py tests/inventory/test_items.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

`bot/inventory/matching.py`:
```python
from dataclasses import dataclass

from bot.inventory.models import ItemKind
from bot.users.profile import CLEAT_LABELS, HELMET_SIZES

PEDAL_TYPES = ("flat", "spd", "look", "spd_sl")
PEDAL_LABELS = {"flat": "плоские", "spd": "SPD", "look": "Look", "spd_sl": "SPD-SL"}
KIND_LABELS = {"bike": "Велосипед", "pedals": "Педали", "shoes": "Туфли", "helmet": "Шлем"}
_NO_STOCK = "нет в наличии на это время — напишите тренеру"


class MatchStatus:
    EXACT = "exact"
    NEAREST = "nearest"
    NONE = "none"


@dataclass(frozen=True)
class Candidate:
    id: int
    kind: str
    label: str
    frame: str | None = None
    height_min: int | None = None
    height_max: int | None = None
    shoe_eu: int | None = None
    cleat_type: str | None = None
    pedal_type: str | None = None
    helmet_size: str | None = None


@dataclass(frozen=True)
class Profile:
    height_cm: int | None
    foot_eu: int | None
    helmet_size: str | None


@dataclass(frozen=True)
class Wanted:
    kinds: frozenset[str]  # позиции из парка; свои туфли сюда не входят
    pedal_type: str | None = None
    own_shoes_cleat: str | None = None


@dataclass(frozen=True)
class Suggestion:
    kind: str
    status: str
    item_id: int | None
    text: str


def suggest(profile: Profile, wanted: Wanted, candidates: list[Candidate], compat: set[tuple[str, str]]) -> list[Suggestion]:
    by_kind = {kind: [c for c in candidates if c.kind == kind] for kind in ItemKind.ALL}
    result: list[Suggestion] = []
    if ItemKind.BIKE in wanted.kinds:
        result.append(_bike(profile.height_cm, by_kind[ItemKind.BIKE]))
    if ItemKind.PEDALS in wanted.kinds:
        result.append(_pedals(wanted.pedal_type, by_kind[ItemKind.PEDALS]))
    if ItemKind.SHOES in wanted.kinds:
        result.append(_shoes(profile.foot_eu, wanted.pedal_type, by_kind[ItemKind.SHOES], compat))
    if ItemKind.HELMET in wanted.kinds:
        result.append(_helmet(profile.helmet_size, by_kind[ItemKind.HELMET]))
    return result


def _bike(height: int, items: list[Candidate]) -> Suggestion:
    pool = [i for i in items if i.height_min is not None and i.height_max is not None]
    exact = sorted((i for i in pool if i.height_min <= height <= i.height_max), key=lambda i: i.id)
    if exact:
        item = exact[0]
        return Suggestion(ItemKind.BIKE, MatchStatus.EXACT, item.id, f"🚲 {item.label} (рама {item.frame})")
    if pool:
        item = min(pool, key=lambda i: (min(abs(height - i.height_min), abs(height - i.height_max)), i.id))
        return Suggestion(
            ItemKind.BIKE, MatchStatus.NEAREST, item.id,
            f"🚲 {item.label}: рама {item.frame} рассчитана на рост {item.height_min}–{item.height_max} см, "
            f"ваш рост {height} см — ближайшая из свободных",
        )
    return Suggestion(ItemKind.BIKE, MatchStatus.NONE, None, f"🚲 Велосипед: {_NO_STOCK}")


def _pedals(pedal_type: str | None, items: list[Candidate]) -> Suggestion:
    matching = sorted((i for i in items if i.pedal_type == pedal_type), key=lambda i: i.id)
    if matching:
        item = matching[0]
        return Suggestion(ItemKind.PEDALS, MatchStatus.EXACT, item.id, f"🦶 {item.label} ({PEDAL_LABELS[item.pedal_type]})")
    label = PEDAL_LABELS.get(pedal_type, "")
    return Suggestion(ItemKind.PEDALS, MatchStatus.NONE, None, f"🦶 Педали {label}: {_NO_STOCK}")


def _shoes(foot: int, pedal_type: str | None, items: list[Candidate], compat: set[tuple[str, str]]) -> Suggestion:
    pool = [
        i for i in items
        if i.shoe_eu is not None and (pedal_type is None or (i.cleat_type, pedal_type) in compat)
    ]
    exact = sorted((i for i in pool if i.shoe_eu == foot), key=lambda i: i.id)
    if exact:
        item = exact[0]
        return Suggestion(ItemKind.SHOES, MatchStatus.EXACT, item.id, f"👟 {item.label}, размер {item.shoe_eu}")
    near = [i for i in pool if abs(i.shoe_eu - foot) == 1]
    if near:
        item = min(near, key=lambda i: (-i.shoe_eu, i.id))  # при равенстве лучше чуть больше
        return Suggestion(
            ItemKind.SHOES, MatchStatus.NEAREST, item.id,
            f"👟 {item.label}: размер {item.shoe_eu} (ваш {foot}) — ближайший из свободных",
        )
    return Suggestion(ItemKind.SHOES, MatchStatus.NONE, None, f"👟 Туфли: {_NO_STOCK}")


def _helmet(size: str, items: list[Candidate]) -> Suggestion:
    pool = [i for i in items if i.helmet_size in HELMET_SIZES]
    want = HELMET_SIZES.index(size)

    def key(item: Candidate):
        idx = HELMET_SIZES.index(item.helmet_size)
        return (abs(idx - want), -idx, item.id)

    if pool:
        item = min(pool, key=key)
        if item.helmet_size == size:
            return Suggestion(ItemKind.HELMET, MatchStatus.EXACT, item.id, f"⛑ {item.label}, размер {size}")
        return Suggestion(
            ItemKind.HELMET, MatchStatus.NEAREST, item.id,
            f"⛑ {item.label}: размер {item.helmet_size} (ваш {size}) — ближайший из свободных",
        )
    return Suggestion(ItemKind.HELMET, MatchStatus.NONE, None, f"⛑ Шлем: {_NO_STOCK}")


def missing_profile_fields(profile: Profile, kinds: set[str] | frozenset[str]) -> list[str]:
    missing: list[str] = []
    if ItemKind.BIKE in kinds and profile.height_cm is None:
        missing.append("height_cm")
    if ItemKind.SHOES in kinds and profile.foot_eu is None:
        missing.append("foot_eu")
    if ItemKind.HELMET in kinds and profile.helmet_size is None:
        missing.append("helmet_size")
    return missing


def own_shoes_problem(own_cleat: str | None, pedal_type: str | None, compat: set[tuple[str, str]]) -> str | None:
    if own_cleat is None or pedal_type is None or (own_cleat, pedal_type) in compat:
        return None
    return (
        f"Ваши туфли ({CLEAT_LABELS[own_cleat]}) не подходят к педалям {PEDAL_LABELS[pedal_type]}."
    )


def set_problems(items: list[Candidate], compat: set[tuple[str, str]]) -> list[str]:
    pedals = next((i for i in items if i.kind == ItemKind.PEDALS), None)
    shoes = next((i for i in items if i.kind == ItemKind.SHOES), None)
    if pedals is not None and shoes is not None and (shoes.cleat_type, pedals.pedal_type) not in compat:
        return [f"«{shoes.label}» не подходят к «{pedals.label}»"]
    return []
```
Проверьте сообщение в тесте `set_problems`: оно должно содержать `Педали №1` и `Туфли №2`; формат `«{shoes.label}» не подходят к «{pedals.label}»` даёт «Туфли №2» и «Педали №1» — условие выполнено.

`bot/inventory/items.py`:
```python
from bot.core.errors import InvalidAction
from bot.inventory.matching import PEDAL_TYPES
from bot.inventory.models import ItemKind
from bot.users.profile import CLEAT_TYPES, HELMET_SIZES

ITEM_FORMATS = {
    ItemKind.BIKE: "Велосипед №7; M; 165; 178  (метка; рама; рост от; рост до)",
    ItemKind.PEDALS: "Педали №3; spd  (метка; тип: flat, spd, look, spd_sl)",
    ItemKind.SHOES: "Туфли №5; 42; spd  (метка; размер EU; шипы: none, spd, look, spd_sl)",
    ItemKind.HELMET: "Шлем №2; M  (метка; размер S, M или L)",
}
_FIELDS = {ItemKind.BIKE: 4, ItemKind.PEDALS: 2, ItemKind.SHOES: 3, ItemKind.HELMET: 2}


def parse_item_line(kind: str, text: str) -> tuple[str, dict]:
    if kind not in ITEM_FORMATS:
        raise InvalidAction("Неизвестный вид инвентаря")
    error = InvalidAction(f"Формат: {ITEM_FORMATS[kind]}")
    parts = [p.strip() for p in text.split(";")]
    if len(parts) != _FIELDS[kind] or not parts[0]:
        raise error
    label = parts[0]
    try:
        if kind == ItemKind.BIKE:
            lo, hi = int(parts[2]), int(parts[3])
            if not parts[1] or lo > hi or lo < 100 or hi > 230:
                raise error
            return label, {"frame": parts[1].upper(), "height_min": lo, "height_max": hi}
        if kind == ItemKind.PEDALS:
            pedal = parts[1].lower()
            if pedal not in PEDAL_TYPES:
                raise error
            return label, {"pedal_type": pedal}
        if kind == ItemKind.SHOES:
            size, cleat = int(parts[1]), parts[2].lower()
            if not 30 <= size <= 52 or cleat not in CLEAT_TYPES:
                raise error
            return label, {"shoe_eu": size, "cleat_type": cleat}
        helmet = parts[1].upper()
        if helmet not in HELMET_SIZES:
            raise error
        return label, {"helmet_size": helmet}
    except ValueError:
        raise error from None
```

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/inventory -v` → PASS.
```bash
git add -A
git commit -m "feat(inventory): matching rules and park item parsing (pure)"
```

---

### Task 4: InventoryService

**Files:**
- Create: `bot/inventory/service.py`
- Test: `tests/inventory/test_service.py`

**Interfaces:**
- Consumes: модели, `matching.*`, `ScheduleService.can_manage`, `Participation`, `get_setting`, `require_admin`, `parse_item_line` (не нужен сервису), `ensure_cleat_compat`.
- Produces:
  - `NotGoing(InvalidAction)`; `ConfirmResult(orders: list[InventoryOrder], conflicts: list[str])` (`conflicts` — виды единиц, которые успели забрать); `OrderView(order, item, training)`.
  - `InventoryService(session)`:
    - парк: `add_item(actor, kind, label, **params) -> InventoryItem` (админ), `set_active(actor, item_id, active)`, `list_items(include_inactive=True) -> list[InventoryItem]`;
    - `check_can_order(training_id, user_id, now) -> Training`;
    - `suggest(training_id, user, wanted, now) -> list[Suggestion]`;
    - `confirm(training_id, user_id, item_ids, now) -> ConfirmResult`;
    - `own_shoes_problem(own_cleat, pedal_type) -> str | None` (с совместимостью из БД);
    - `my_orders(user_id, now) -> list[OrderView]` (активные на будущие тренировки);
    - `cancel_order(order_id, user_id, now) -> InventoryOrder`;
    - `cancel_for_participant(training_id, user_id) -> list[InventoryOrder]`; `cancel_for_training(training_id) -> list[InventoryOrder]` (возвращают отменённые, коммитят);
    - `training_summary(actor, training_id) -> list[OrderLine]` (права: `ScheduleService.can_manage`).
  - `busy_item_ids(session, training) -> set[int]` (функция модуля).

- [ ] **Step 1: Написать падающие тесты**

`tests/inventory/test_service.py`:
```python
from datetime import timedelta

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.settings import set_setting
from bot.inventory.matching import MatchStatus, Wanted
from bot.inventory.models import InventoryOrder, OrderStatus, ensure_cleat_compat
from bot.inventory.service import InventoryService, NotGoing, busy_item_ids
from bot.participation.service import ParticipationService
from bot.schedule.models import TrainingStatus
from tests.factories import NOW, add_item, add_level, add_training, add_user

STARTS = NOW + timedelta(hours=20)


async def world(session):
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=STARTS, published_at=NOW - timedelta(hours=1)
    )
    await ensure_cleat_compat(session)
    return trainer, level, t


async def rider(session, uid, t, going=True, **profile):
    user = await add_user(session, uid)
    for key, value in profile.items():
        setattr(user, key, value)
    await session.commit()
    if going:
        await ParticipationService(session).join(t.id, uid, NOW)
    return user


BIKE = Wanted(frozenset({"bike"}))


# ---------- парк (админ) ----------

async def test_only_admin_manages_the_park_and_params_are_validated(session):
    admin = await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer")
    svc = InventoryService(session)
    with pytest.raises(PermissionDenied):
        await svc.add_item(trainer, "helmet", "Шлем №1", helmet_size="M")
    item = await svc.add_item(admin, "helmet", "Шлем №1", helmet_size="M")
    assert item.active is True
    with pytest.raises(InvalidAction):
        await svc.add_item(admin, "kayak", "Лодка")
    with pytest.raises(InvalidAction):
        await svc.add_item(admin, "helmet", "Шлем №2", helmet_size="XXL")
    await svc.set_active(admin, item.id, False)
    assert item.active is False
    assert [i.id for i in await svc.list_items(include_inactive=False)] == []
    assert [i.id for i in await svc.list_items()] == [item.id]


# ---------- проверки перед заказом ----------

async def test_must_be_going(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t, going=False, height_cm=170)
    with pytest.raises(NotGoing):
        await InventoryService(session).suggest(t.id, user, BIKE, NOW)


async def test_not_for_cancelled_or_started_trainings(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t, height_cm=170)
    svc = InventoryService(session)
    with pytest.raises(InvalidAction):
        await svc.check_can_order(t.id, user.telegram_id, STARTS + timedelta(minutes=1))
    t.status = TrainingStatus.CANCELLED
    await session.commit()
    with pytest.raises(InvalidAction):
        await svc.check_can_order(t.id, user.telegram_id, NOW)
    with pytest.raises(NotFound):
        await svc.check_can_order(9999, 1, NOW)


async def test_deadline_setting_closes_ordering(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t, height_cm=170)
    await set_setting(session, "inventory_deadline_h", "3")
    svc = InventoryService(session)
    await svc.check_can_order(t.id, user.telegram_id, STARTS - timedelta(hours=4))
    with pytest.raises(InvalidAction, match="3"):
        await svc.check_can_order(t.id, user.telegram_id, STARTS - timedelta(hours=2))


async def test_empty_deadline_means_no_limit(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t)
    await InventoryService(session).check_can_order(t.id, user.telegram_id, STARTS - timedelta(minutes=1))


async def test_suggest_needs_profile_pedal_choice_and_no_existing_order(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t)  # профиль пуст
    svc = InventoryService(session)
    with pytest.raises(InvalidAction, match="профиля"):
        await svc.suggest(t.id, user, BIKE, NOW)
    with pytest.raises(InvalidAction, match="педал"):
        await svc.suggest(t.id, user, Wanted(frozenset({"pedals"})), NOW)
    user.height_cm = 170
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    await svc.confirm(t.id, 1, [bike.id], NOW)
    with pytest.raises(InvalidAction, match="уже есть"):
        await svc.suggest(t.id, user, BIKE, NOW)


# ---------- свободные единицы и пересечения ----------

async def test_suggest_matches_profile_and_skips_busy_items(session):
    _, level, t = await world(session)
    first = await rider(session, 1, t, height_cm=170)
    second = await rider(session, 2, t, height_cm=170)
    b1 = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    b2 = await add_item(session, "bike", "Велосипед №2", frame="M", height_min=160, height_max=180)
    svc = InventoryService(session)
    (s,) = await svc.suggest(t.id, first, BIKE, NOW)
    assert (s.status, s.item_id) == (MatchStatus.EXACT, b1.id)
    await svc.confirm(t.id, 1, [b1.id], NOW)
    (s,) = await svc.suggest(t.id, second, BIKE, NOW)
    assert s.item_id == b2.id  # первый занят


async def test_overlapping_training_blocks_item_but_adjacent_one_does_not(session):
    trainer, level, a = await world(session)
    item = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    other = await add_user(session, 5)
    overlapping = await add_training(
        session, trainer=trainer, levels=[level], starts_at=STARTS + timedelta(minutes=30),
        published_at=NOW,
    )
    adjacent = await add_training(
        session, trainer=trainer, levels=[level], starts_at=STARTS + timedelta(minutes=90),  # ровно конец A
        published_at=NOW,
    )
    session.add(InventoryOrder(training_id=overlapping.id, user_id=5, item_id=item.id, status="active", created_at=NOW))
    await session.commit()
    assert await busy_item_ids(session, a) == {item.id}
    overlapping_order = await session.scalar(select(InventoryOrder))
    overlapping_order.status = OrderStatus.CANCELLED  # отмена освобождает сразу
    session.add(InventoryOrder(training_id=adjacent.id, user_id=5, item_id=item.id, status="active", created_at=NOW))
    await session.commit()
    assert await busy_item_ids(session, a) == set()  # «встык» не пересекается


# ---------- подтверждение ----------

async def test_confirm_creates_orders_with_price_and_blocks_the_same_kind_twice(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180, price_per_use=30000)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    svc = InventoryService(session)
    result = await svc.confirm(t.id, 1, [bike.id, helmet.id], NOW)
    assert result.conflicts == [] and {o.item_id for o in result.orders} == {bike.id, helmet.id}
    assert next(o for o in result.orders if o.item_id == bike.id).price == 30000
    other_bike = await add_item(session, "bike", "Велосипед №2", frame="M", height_min=160, height_max=180)
    with pytest.raises(InvalidAction, match="уже есть"):
        await svc.confirm(t.id, 1, [other_bike.id], NOW)


async def test_last_item_race_second_rider_gets_a_conflict_not_an_order(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    await rider(session, 2, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    svc = InventoryService(session)
    await svc.confirm(t.id, 1, [bike.id], NOW)
    result = await svc.confirm(t.id, 2, [bike.id, helmet.id], NOW)
    assert result.conflicts == ["bike"]
    assert [o.item_id for o in result.orders] == [helmet.id]  # свободное сохранилось


async def test_inactive_item_is_a_conflict(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    bike.active = False
    await session.commit()
    result = await InventoryService(session).confirm(t.id, 1, [bike.id], NOW)
    assert result.conflicts == ["bike"] and result.orders == []


async def test_confirm_rejects_incompatible_pedals_and_shoes_and_duplicates(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    pedals = await add_item(session, "pedals", "Педали №1", pedal_type="look")
    shoes = await add_item(session, "shoes", "Туфли №1", shoe_eu=42, cleat_type="spd")
    other = await add_item(session, "pedals", "Педали №2", pedal_type="spd")
    svc = InventoryService(session)
    with pytest.raises(InvalidAction, match="не подходят"):
        await svc.confirm(t.id, 1, [pedals.id, shoes.id], NOW)
    with pytest.raises(InvalidAction):
        await svc.confirm(t.id, 1, [pedals.id, other.id], NOW)  # два вида «педали»
    with pytest.raises(InvalidAction):
        await svc.confirm(t.id, 1, [], NOW)
    with pytest.raises(NotFound):
        await svc.confirm(t.id, 1, [9999], NOW)
    assert list(await session.scalars(select(InventoryOrder))) == []


async def test_own_shoes_problem_uses_compat_data(session):
    await ensure_cleat_compat(session)
    svc = InventoryService(session)
    assert await svc.own_shoes_problem("spd", "spd") is None
    assert "не подходят" in await svc.own_shoes_problem("spd_sl", "spd")


# ---------- отмена ----------

async def test_cancel_order_rules_and_instant_release(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    await rider(session, 2, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    svc = InventoryService(session)
    (order,) = (await svc.confirm(t.id, 1, [bike.id], NOW)).orders
    with pytest.raises(PermissionDenied):
        await svc.cancel_order(order.id, 2, NOW)
    with pytest.raises(InvalidAction):
        await svc.cancel_order(order.id, 1, STARTS)  # уже началась
    await svc.cancel_order(order.id, 1, NOW)
    assert order.status == OrderStatus.CANCELLED
    result = await svc.confirm(t.id, 2, [bike.id], NOW)  # единица свободна сразу
    assert result.conflicts == []


async def test_cancel_for_participant_and_for_training(session):
    trainer, level, t = await world(session)
    await rider(session, 1, t)
    await rider(session, 2, t)
    b1 = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    b2 = await add_item(session, "bike", "Велосипед №2", frame="M", height_min=160, height_max=180)
    h = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    svc = InventoryService(session)
    await svc.confirm(t.id, 1, [b1.id, h.id], NOW)
    await svc.confirm(t.id, 2, [b2.id], NOW)
    cancelled = await svc.cancel_for_participant(t.id, 1)
    assert {o.item_id for o in cancelled} == {b1.id, h.id}
    assert await svc.cancel_for_participant(t.id, 1) == []  # повторно ничего
    rest = await svc.cancel_for_training(t.id)
    assert [o.item_id for o in rest] == [b2.id]
    assert list(await session.scalars(select(InventoryOrder).where(InventoryOrder.status == "active"))) == []


async def test_my_orders_lists_active_future_only(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    svc = InventoryService(session)
    orders = (await svc.confirm(t.id, 1, [bike.id, helmet.id], NOW)).orders
    await svc.cancel_order(next(o.id for o in orders if o.item_id == helmet.id), 1, NOW)
    views = await svc.my_orders(1, NOW)
    assert [v.item.id for v in views] == [bike.id] and views[0].training.id == t.id
    assert await svc.my_orders(1, STARTS + timedelta(hours=3)) == []


# ---------- сводка ----------

async def test_training_summary_is_for_those_who_manage_the_training(session):
    trainer, _, t = await world(session)
    admin = await add_user(session, 99, "admin")
    stranger = await add_user(session, 11, "trainer")
    await rider(session, 1, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    svc = InventoryService(session)
    await svc.confirm(t.id, 1, [bike.id], NOW)
    lines = await svc.training_summary(trainer, t.id)
    assert [(l.kind, l.frame, l.user_name) for l in lines] == [("bike", "M", "User 1")]
    assert len(await svc.training_summary(admin, t.id)) == 1
    with pytest.raises(PermissionDenied):
        await svc.training_summary(stranger, t.id)
```

В `tests/inventory/test_service.py` используется `OrderLine` из Задачи 5 только через возвращаемые объекты; сам модуль `summary.py` создаётся в этой задаче минимально (см. шаг 3).

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/inventory/test_service.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

Сначала минимальный `bot/inventory/summary.py` (расширяется в Задаче 5):
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class OrderLine:
    user_name: str
    kind: str
    label: str
    frame: str | None = None
    shoe_eu: int | None = None
    pedal_type: str | None = None
    helmet_size: str | None = None
```

`bot/inventory/service.py`:
```python
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.permissions import require_admin
from bot.core.settings import get_setting
from bot.inventory.matching import (
    Candidate, Profile, Suggestion, Wanted, missing_profile_fields, own_shoes_problem, set_problems, suggest,
)
from bot.inventory.matching import KIND_LABELS
from bot.inventory.models import CleatCompat, InventoryItem, InventoryOrder, ItemKind, OrderStatus
from bot.inventory.summary import OrderLine
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.service import ScheduleService
from bot.users.models import User
from bot.users.profile import CLEAT_TYPES, HELMET_SIZES
from bot.inventory.matching import PEDAL_TYPES

_PARAM_FIELDS = {"frame", "height_min", "height_max", "shoe_eu", "cleat_type", "pedal_type", "helmet_size", "price_per_use"}


class NotGoing(InvalidAction):
    """Участник не записан на тренировку: UI предлагает сначала записаться."""


@dataclass
class ConfirmResult:
    orders: list[InventoryOrder]
    conflicts: list[str]  # виды единиц, которые успели забрать или вывели из парка


@dataclass
class OrderView:
    order: InventoryOrder
    item: InventoryItem
    training: Training


def to_candidate(item: InventoryItem) -> Candidate:
    return Candidate(
        id=item.id, kind=item.kind, label=item.label, frame=item.frame, height_min=item.height_min,
        height_max=item.height_max, shoe_eu=item.shoe_eu, cleat_type=item.cleat_type,
        pedal_type=item.pedal_type, helmet_size=item.helmet_size,
    )


async def busy_item_ids(session: AsyncSession, training: Training) -> set[int]:
    """Единицы с активным заказом на тренировке, пересекающейся по времени с данной.
    Пересечение: a.starts_at < b.ends_at AND b.starts_at < a.ends_at (встык не пересекается)."""
    lo = training.starts_at - timedelta(days=1)
    hi = training.ends_at + timedelta(days=1)
    rows = await session.execute(
        select(InventoryOrder.item_id, Training.starts_at, Training.duration_min)
        .join(Training, Training.id == InventoryOrder.training_id)
        .where(InventoryOrder.status == OrderStatus.ACTIVE, Training.starts_at > lo, Training.starts_at < hi)
    )
    busy: set[int] = set()
    for item_id, starts_at, duration in rows:
        if starts_at < training.ends_at and training.starts_at < starts_at + timedelta(minutes=duration):
            busy.add(item_id)
    return busy


def _validate_item(kind: str, label: str, params: dict) -> None:
    if kind not in ItemKind.ALL:
        raise InvalidAction("Неизвестный вид инвентаря")
    if not label.strip():
        raise InvalidAction("Укажите метку единицы")
    unknown = set(params) - _PARAM_FIELDS
    if unknown:
        raise InvalidAction("Неизвестные параметры: " + ", ".join(sorted(unknown)))
    if kind == ItemKind.BIKE:
        if not params.get("frame") or not (params.get("height_min") and params.get("height_max")) \
                or params["height_min"] > params["height_max"]:
            raise InvalidAction("Для велосипеда нужны рама и диапазон роста")
    elif kind == ItemKind.PEDALS:
        if params.get("pedal_type") not in PEDAL_TYPES:
            raise InvalidAction("Неверный тип педалей")
    elif kind == ItemKind.SHOES:
        if not params.get("shoe_eu") or params.get("cleat_type") not in CLEAT_TYPES:
            raise InvalidAction("Для туфель нужны размер и тип шипов")
    elif params.get("helmet_size") not in HELMET_SIZES:
        raise InvalidAction("Размер шлема: S, M или L")


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- парк ----

    async def add_item(self, actor: User, kind: str, label: str, **params) -> InventoryItem:
        require_admin(actor)
        _validate_item(kind, label, params)
        item = InventoryItem(kind=kind, label=label.strip(), **params)
        self.session.add(item)
        await self.session.commit()
        return item

    async def set_active(self, actor: User, item_id: int, active: bool) -> InventoryItem:
        require_admin(actor)
        item = await self.session.get(InventoryItem, item_id)
        if item is None:
            raise NotFound("Единица не найдена")
        item.active = active
        await self.session.commit()
        return item

    async def list_items(self, include_inactive: bool = True) -> list[InventoryItem]:
        query = select(InventoryItem).order_by(InventoryItem.kind, InventoryItem.id)
        if not include_inactive:
            query = query.where(InventoryItem.active.is_(True))
        return list(await self.session.scalars(query))

    # ---- проверки ----

    async def check_can_order(self, training_id: int, user_id: int, now: datetime) -> Training:
        training = await self.session.get(Training, training_id)
        if training is None:
            raise NotFound("Тренировка не найдена")
        if training.status != TrainingStatus.SCHEDULED or training.starts_at <= now:
            raise InvalidAction("На эту тренировку инвентарь заказать уже нельзя")
        deadline = await get_setting(self.session, "inventory_deadline_h")
        if deadline and now > training.starts_at - timedelta(hours=int(deadline)):
            raise InvalidAction(f"Приём заказов закрыт за {deadline} ч до тренировки")
        row = await self.session.get(Participation, (training_id, user_id))
        if row is None or row.status != ParticipationStatus.GOING:
            raise NotGoing("Сначала запишитесь на тренировку")
        return training

    async def _reject_kinds_already_ordered(self, training_id: int, user_id: int, kinds: set[str]) -> None:
        ordered = await self.session.scalars(
            select(InventoryItem.kind)
            .join(InventoryOrder, InventoryOrder.item_id == InventoryItem.id)
            .where(
                InventoryOrder.training_id == training_id, InventoryOrder.user_id == user_id,
                InventoryOrder.status == OrderStatus.ACTIVE,
            )
        )
        clash = kinds & set(ordered)
        if clash:
            names = ", ".join(KIND_LABELS[k].lower() for k in sorted(clash))
            raise InvalidAction(f"У вас уже есть заказ ({names}). Отмените его в /my, чтобы заказать заново")

    async def _compat(self) -> set[tuple[str, str]]:
        return {(r.cleat_type, r.pedal_type) for r in await self.session.scalars(select(CleatCompat))}

    async def own_shoes_problem(self, own_cleat: str | None, pedal_type: str | None) -> str | None:
        return own_shoes_problem(own_cleat, pedal_type, await self._compat())

    # ---- подбор ----

    async def suggest(self, training_id: int, user: User, wanted: Wanted, now: datetime) -> list[Suggestion]:
        training = await self.check_can_order(training_id, user.telegram_id, now)
        await self._reject_kinds_already_ordered(training_id, user.telegram_id, set(wanted.kinds))
        profile = Profile(user.height_cm, user.foot_eu, user.helmet_size)
        if missing_profile_fields(profile, wanted.kinds):
            raise InvalidAction("Не хватает данных профиля — заполните /profile")
        if ItemKind.PEDALS in wanted.kinds and wanted.pedal_type is None:
            raise InvalidAction("Выберите тип педалей")
        compat = await self._compat()
        problem = own_shoes_problem(wanted.own_shoes_cleat, wanted.pedal_type, compat)
        if problem:
            raise InvalidAction(problem)
        items = await self.session.scalars(
            select(InventoryItem).where(InventoryItem.active.is_(True), InventoryItem.kind.in_(list(wanted.kinds)))
        )
        busy = await busy_item_ids(self.session, training)
        candidates = [to_candidate(i) for i in items if i.id not in busy]
        return suggest(profile, wanted, candidates, compat)

    # ---- подтверждение ----

    async def confirm(self, training_id: int, user_id: int, item_ids: list[int], now: datetime) -> ConfirmResult:
        training = await self.check_can_order(training_id, user_id, now)
        if not item_ids or len(set(item_ids)) != len(item_ids):
            raise InvalidAction("Нечего заказывать")
        # FOR UPDATE: на PostgreSQL сериализует конкурентные заказы одной единицы (SQLite игнорирует)
        items = list(
            await self.session.scalars(select(InventoryItem).where(InventoryItem.id.in_(item_ids)).with_for_update())
        )
        if len(items) != len(item_ids):
            raise NotFound("Единица инвентаря не найдена")
        kinds = [i.kind for i in items]
        if len(set(kinds)) != len(kinds):
            raise InvalidAction("Можно заказать одну единицу каждого вида")
        await self._reject_kinds_already_ordered(training_id, user_id, set(kinds))
        busy = await busy_item_ids(self.session, training)
        free = [i for i in items if i.active and i.id not in busy]
        conflicts = [i.kind for i in items if i not in free]
        problems = set_problems([to_candidate(i) for i in free], await self._compat())
        if problems:
            raise InvalidAction(problems[0])
        orders = [
            InventoryOrder(
                training_id=training_id, user_id=user_id, item_id=i.id, status=OrderStatus.ACTIVE,
                price=i.price_per_use, created_at=now,
            )
            for i in free
        ]
        self.session.add_all(orders)
        await self.session.commit()
        return ConfirmResult(orders, conflicts)

    # ---- мои заказы и отмена ----

    async def my_orders(self, user_id: int, now: datetime) -> list[OrderView]:
        rows = await self.session.execute(
            select(InventoryOrder, InventoryItem, Training)
            .join(InventoryItem, InventoryItem.id == InventoryOrder.item_id)
            .join(Training, Training.id == InventoryOrder.training_id)
            .where(
                InventoryOrder.user_id == user_id, InventoryOrder.status == OrderStatus.ACTIVE,
                Training.starts_at > now, Training.status == TrainingStatus.SCHEDULED,
            )
            .order_by(Training.starts_at, InventoryItem.kind)
        )
        return [OrderView(o, i, t) for o, i, t in rows]

    async def cancel_order(self, order_id: int, user_id: int, now: datetime) -> InventoryOrder:
        order = await self.session.get(InventoryOrder, order_id)
        if order is None:
            raise NotFound("Заказ не найден")
        if order.user_id != user_id:
            raise PermissionDenied("Это не ваш заказ")
        training = await self.session.get(Training, order.training_id)
        if training.starts_at <= now:
            raise InvalidAction("Тренировка уже началась — заказ отменить нельзя")
        order.status = OrderStatus.CANCELLED
        await self.session.commit()
        return order

    async def _cancel_where(self, *conditions) -> list[InventoryOrder]:
        orders = list(
            await self.session.scalars(
                select(InventoryOrder).where(InventoryOrder.status == OrderStatus.ACTIVE, *conditions)
            )
        )
        for order in orders:
            order.status = OrderStatus.CANCELLED
        await self.session.commit()
        return orders

    async def cancel_for_participant(self, training_id: int, user_id: int) -> list[InventoryOrder]:
        return await self._cancel_where(InventoryOrder.training_id == training_id, InventoryOrder.user_id == user_id)

    async def cancel_for_training(self, training_id: int) -> list[InventoryOrder]:
        return await self._cancel_where(InventoryOrder.training_id == training_id)

    # ---- сводка тренера ----

    async def training_summary(self, actor: User, training_id: int) -> list[OrderLine]:
        training = await self.session.get(Training, training_id)
        if training is None:
            raise NotFound("Тренировка не найдена")
        if not await ScheduleService(self.session).can_manage(actor, training):
            raise PermissionDenied("Нет прав на эту тренировку")
        rows = await self.session.execute(
            select(User.name, InventoryItem)
            .join(InventoryOrder, InventoryOrder.user_id == User.telegram_id)
            .join(InventoryItem, InventoryItem.id == InventoryOrder.item_id)
            .where(InventoryOrder.training_id == training_id, InventoryOrder.status == OrderStatus.ACTIVE)
            .order_by(User.name, InventoryItem.kind)
        )
        return [
            OrderLine(
                user_name=name, kind=item.kind, label=item.label, frame=item.frame,
                shoe_eu=item.shoe_eu, pedal_type=item.pedal_type, helmet_size=item.helmet_size,
            )
            for name, item in rows
        ]
```

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/inventory -v` → PASS.
```bash
git add -A
git commit -m "feat(inventory): InventoryService — suggest, confirm with recheck, cancel, summary"
```

---

### Task 5: Сводка тренера, «Мои записи», вспомогательный flow (чистая логика)

**Files:**
- Modify: `bot/inventory/summary.py`
- Create: `bot/inventory/flow.py`
- Test: `tests/inventory/test_summary.py`, `tests/inventory/test_flow.py`

**Interfaces:**
- Produces:
  - `summarize(lines: list[OrderLine]) -> str` — одна строка вида `велосипеды M×2, L×1; педали spd×2; туфли 42×2; шлем M×1` (виды в порядке: велосипеды, педали, туфли, шлем; внутри — по размеру; пусто → `Заказов нет`);
  - `breakdown(lines) -> str` — по участникам: `Анна: велосипед M (Велосипед №7), шлем M (Шлем №2)`;
  - `render_my(entries: list[MyEntry]) -> str`, `MyEntry(title: str, status: str, orders: list[str])` (в `flow.py`);
  - `format_review(suggestions, confirmed_labels, replacement: bool) -> str`; `kinds_markup_data(selected) -> list[tuple[str, str]]` (подписи/данные кнопок видов); `can_confirm(suggestions) -> bool`.

- [ ] **Step 1: Написать падающие тесты**

`tests/inventory/test_summary.py`:
```python
from bot.inventory.summary import OrderLine, breakdown, summarize


def line(user, kind, label, **kw):
    return OrderLine(user_name=user, kind=kind, label=label, **kw)


LINES = [
    line("Анна", "bike", "Велосипед №7", frame="M"),
    line("Анна", "helmet", "Шлем №2", helmet_size="M"),
    line("Борис", "bike", "Велосипед №8", frame="M"),
    line("Вера", "bike", "Велосипед №9", frame="L"),
    line("Борис", "shoes", "Туфли №5", shoe_eu=42),
    line("Вера", "shoes", "Туфли №6", shoe_eu=42),
    line("Вера", "pedals", "Педали №1", pedal_type="spd"),
    line("Борис", "pedals", "Педали №2", pedal_type="spd"),
]


def test_summary_groups_by_kind_and_size():
    assert summarize(LINES) == "велосипеды M×2, L×1; педали spd×2; туфли 42×2; шлем M×1"


def test_summary_empty():
    assert summarize([]) == "Заказов нет"


def test_summary_sorts_sizes_sensibly():
    mixed = [line("a", "bike", "в", frame="L"), line("b", "bike", "в", frame="S"), line("c", "shoes", "т", shoe_eu=44),
             line("d", "shoes", "т", shoe_eu=40)]
    assert summarize(mixed) == "велосипеды S×1, L×1; туфли 40×1, 44×1"


def test_breakdown_per_participant():
    text = breakdown(LINES)
    assert text.splitlines()[0] == "Анна: велосипед M (Велосипед №7), шлем M (Шлем №2)"
    assert "Борис: велосипед M (Велосипед №8), педали spd (Педали №2), туфли 42 (Туфли №5)" in text
    assert breakdown([]) == ""
```

`tests/inventory/test_flow.py`:
```python
from bot.inventory.flow import MyEntry, can_confirm, format_review, kinds_markup_data, render_my
from bot.inventory.matching import MatchStatus, Suggestion


def sug(kind, status, item_id, text):
    return Suggestion(kind, status, item_id, text)


def test_kinds_markup_marks_selected():
    rows = kinds_markup_data({"bike", "helmet"})
    assert ("✅ Велосипед", "iv:k:bike") in rows and ("▫️ Педали", "iv:k:pedals") in rows
    assert rows[-1] == ("Готово", "iv:kdone")


def test_review_text_and_confirmability():
    items = [sug("bike", MatchStatus.EXACT, 1, "🚲 Велосипед №1"),
             sug("helmet", MatchStatus.NEAREST, 2, "⛑ Шлем №2: размер L"),
             sug("shoes", MatchStatus.NONE, None, "👟 Туфли: нет в наличии")]
    text = format_review(items, [], replacement=False)
    assert "Велосипед №1" in text and "размер L" in text
    assert "ближайшие" in text
    assert "Подтвердить" in text
    assert can_confirm(items) is True
    assert can_confirm([items[2]]) is False


def test_review_for_replacement_mentions_it_and_keeps_confirmed():
    items = [sug("bike", MatchStatus.EXACT, 3, "🚲 Велосипед №3")]
    text = format_review(items, ["Шлем №2"], replacement=True)
    assert "забрали" in text and "Шлем №2" in text


def test_render_my():
    assert render_my([]) == "Записей пока нет. Расписание: /schedule"
    text = render_my([
        MyEntry("вторник, 20.10 19:00–20:30 · Интервалы", "идёте", ["Велосипед №7", "Шлем №2"]),
        MyEntry("четверг, 22.10 19:00–20:30 · Техника", "не идёте", []),
    ])
    assert "Интервалы" in text and "идёте" in text and "Велосипед №7" in text and "не идёте" in text
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/inventory/test_summary.py tests/inventory/test_flow.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

Дополнить `bot/inventory/summary.py` (оставить `OrderLine`):
```python
from collections import Counter

FRAME_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]
_PLURAL = {"bike": "велосипеды", "pedals": "педали", "shoes": "туфли", "helmet": "шлем"}
_SINGULAR = {"bike": "велосипед", "pedals": "педали", "shoes": "туфли", "helmet": "шлем"}
_ORDER = ["bike", "pedals", "shoes", "helmet"]


def _size(line: OrderLine):
    if line.kind == "bike":
        return line.frame
    if line.kind == "pedals":
        return line.pedal_type
    if line.kind == "shoes":
        return line.shoe_eu
    return line.helmet_size


def _sort_key(kind: str, size):
    if kind == "bike":
        return (FRAME_ORDER.index(size) if size in FRAME_ORDER else len(FRAME_ORDER), str(size))
    if kind == "shoes":
        return (size, "")
    return (0, str(size))


def summarize(lines: list[OrderLine]) -> str:
    parts: list[str] = []
    for kind in _ORDER:
        counts = Counter(_size(l) for l in lines if l.kind == kind)
        if not counts:
            continue
        sizes = sorted(counts, key=lambda s: _sort_key(kind, s))
        parts.append(f"{_PLURAL[kind]} " + ", ".join(f"{s}×{counts[s]}" for s in sizes))
    return "; ".join(parts) if parts else "Заказов нет"


def breakdown(lines: list[OrderLine]) -> str:
    by_user: dict[str, list[OrderLine]] = {}
    for line in lines:
        by_user.setdefault(line.user_name, []).append(line)
    rows = []
    for name, items in by_user.items():
        items.sort(key=lambda l: _ORDER.index(l.kind))
        rows.append(f"{name}: " + ", ".join(f"{_SINGULAR[l.kind]} {_size(l)} ({l.label})" for l in items))
    return "\n".join(rows)
```

`bot/inventory/flow.py`:
```python
from dataclasses import dataclass

from bot.inventory.matching import KIND_LABELS, MatchStatus, Suggestion

_KIND_ORDER = ["bike", "pedals", "shoes", "helmet"]


@dataclass(frozen=True)
class MyEntry:
    title: str
    status: str  # «идёте» / «не идёте»
    orders: list[str]


def kinds_markup_data(selected: set[str]) -> list[tuple[str, str]]:
    rows = [(("✅ " if kind in selected else "▫️ ") + KIND_LABELS[kind], f"iv:k:{kind}") for kind in _KIND_ORDER]
    rows.append(("Готово", "iv:kdone"))
    return rows


def can_confirm(suggestions: list[Suggestion]) -> bool:
    return any(s.item_id is not None for s in suggestions)


def format_review(suggestions: list[Suggestion], confirmed_labels: list[str], replacement: bool) -> str:
    lines: list[str] = []
    if replacement:
        lines.append("Эту единицу только что забрали — предлагаю замену:")
    else:
        lines.append("Подобрал:")
    lines += [s.text for s in suggestions]
    if any(s.status == MatchStatus.NEAREST for s in suggestions):
        lines.append("\nЧасть позиций — ближайшие по параметрам, проверьте.")
    if confirmed_labels:
        lines.append("\nУже заказано: " + ", ".join(confirmed_labels))
    if can_confirm(suggestions):
        lines.append("\nПодтвердить заказ?")
    return "\n".join(lines)


def render_my(entries: list[MyEntry]) -> str:
    if not entries:
        return "Записей пока нет. Расписание: /schedule"
    blocks = []
    for entry in entries:
        block = f"• {entry.title} — {entry.status}"
        if entry.orders:
            block += "\n   🚲 " + ", ".join(entry.orders)
        blocks.append(block)
    return "Мои тренировки:\n" + "\n".join(blocks)
```
В тесте `test_review_text_and_confirmability` условие `"..." in text and "ближайш" in text.lower() or "размер L" in text` намеренно допускает любой из вариантов; текст содержит «Часть позиций — ближайшие…» (проверка пройдёт).

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/inventory -v` → PASS.
```bash
git add -A
git commit -m "feat(inventory): trainer summary, review and my-bookings rendering (pure)"
```

---

### Task 6: Хуки инвентаря в сценариях и кнопка на карточке

**Files:**
- Modify: `bot/usecases.py`, `bot/publishing/render.py`, `tests/publishing/test_render.py`
- Test: `tests/test_usecases_inventory.py`

**Interfaces:**
- Consumes: `InventoryService.cancel_for_participant/cancel_for_training`.
- Produces:
  - `decline_training` и `manual_remove_participant` отменяют активные заказы участника на тренировку и уведомляют его: `«Заказы инвентаря на тренировку отменены: <метки>»`; `cancel_trainings` отменяет заказы всех отменённых тренировок; `update_series` отменяет заказы заменённых тренировок.
  - Карточка опубликованной `scheduled` тренировки получает кнопку `Button("🚲 Инвентарь", "inv:<id>")` третьей строкой.
  - `PublishService.build_card` не меняется.

- [ ] **Step 1: Написать падающие тесты**

Обновить в `tests/publishing/test_render.py` ожидаемую клавиатуру в `test_published_card_text_and_buttons`:
```python
    assert kb == [
        [Button("✅ Пойду", "join:7"), Button("❌ Не пойду", "decline:7")],
        [Button("👥 Участники", "who:7")],
        [Button("🚲 Инвентарь", "inv:7")],
    ]
```

`tests/test_usecases_inventory.py`:
```python
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from bot.inventory.models import InventoryOrder, OrderStatus, ensure_cleat_compat
from bot.inventory.service import InventoryService
from bot.publishing.service import PublishService
from bot.schedule.models import Training
from bot.schedule.service import ScheduleService, SeriesInput, series_to_input
from bot.usecases import (
    cancel_trainings, decline_training, join_training, manual_remove_participant, update_series,
)
from tests.factories import NOW, add_chat, add_item, add_level, add_training, add_user

STARTS = NOW + timedelta(hours=20)


async def ordered(session, messenger, capacity=5):
    admin = await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS, capacity=capacity)
    await PublishService(session, messenger, send_delay=0).publish(t.id, NOW)
    await ensure_cleat_compat(session)
    await add_user(session, 1)
    await join_training(session, messenger, t.id, 1, NOW)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    await InventoryService(session).confirm(t.id, 1, [bike.id, helmet.id], NOW)
    return admin, trainer, t, bike, helmet


async def active_orders(session):
    return list(await session.scalars(select(InventoryOrder).where(InventoryOrder.status == OrderStatus.ACTIVE)))


async def test_decline_cancels_orders_and_notifies(session, messenger):
    _, _, t, bike, helmet = await ordered(session, messenger)
    await decline_training(session, messenger, t.id, 1, NOW)
    assert await active_orders(session) == []
    (text,) = [txt for uid, txt in messenger.notifications if uid == 1 and "инвентар" in txt.lower()]
    assert "Велосипед №1" in text and "Шлем №1" in text


async def test_decline_without_orders_sends_no_inventory_notice(session, messenger):
    admin = await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    await PublishService(session, messenger, send_delay=0).publish(t.id, NOW)
    await add_user(session, 1)
    await join_training(session, messenger, t.id, 1, NOW)
    await decline_training(session, messenger, t.id, 1, NOW)
    assert not any("инвентар" in txt.lower() for _, txt in messenger.notifications)


async def test_manual_remove_also_cancels_orders(session, messenger):
    admin, _, t, *_ = await ordered(session, messenger)
    await manual_remove_participant(session, messenger, admin, t.id, 1, NOW)
    assert await active_orders(session) == []


async def test_trainer_cancel_frees_every_order_and_the_items(session, messenger):
    admin, _, t, bike, helmet = await ordered(session, messenger)
    await cancel_trainings(session, messenger, admin, [t.id], NOW)
    assert await active_orders(session) == []
    other = await add_training(
        session, trainer=admin, levels=list((await session.get(Training, t.id)).levels), starts_at=STARTS,
        published_at=NOW,
    )
    await add_user(session, 2)
    from bot.participation.service import ParticipationService

    await ParticipationService(session).join(other.id, 2, NOW)
    result = await InventoryService(session).confirm(other.id, 2, [bike.id], NOW)
    assert result.conflicts == []  # единица снова свободна


async def test_series_edit_cancels_orders_of_replaced_trainings(session, messenger):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
    T1 = T0 + timedelta(days=2)
    svc = ScheduleService(session)
    series = await svc.create_series(
        admin,
        SeriesInput(kind="intervals", place="Парк", level_ids=[level.id], weekdays={2},
                    start_date=date(2026, 10, 6), start_time=time(19, 0)),
        T0,
    )
    t13 = await session.scalar(select(Training).where(Training.starts_at == datetime(2026, 10, 13, 16, tzinfo=timezone.utc)))
    await PublishService(session, messenger, send_delay=0).publish(t13.id, T1)
    await ensure_cleat_compat(session)
    await add_user(session, 1)
    await join_training(session, messenger, t13.id, 1, T1)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    await InventoryService(session).confirm(t13.id, 1, [helmet.id], T1)
    await update_series(session, messenger, admin, series.id, replace(series_to_input(series), place="Стадион"), T1)
    assert await active_orders(session) == []
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/test_usecases_inventory.py tests/publishing/test_render.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

В `bot/publishing/render.py` в ветке опубликованной карточки заменить блок клавиатуры:
```python
            keyboard = [
                [Button("✅ Пойду", f"join:{tid}"), Button("❌ Не пойду", f"decline:{tid}")],
                [Button("👥 Участники", f"who:{tid}")],
                [Button("🚲 Инвентарь", f"inv:{tid}")],
            ]
```

В `bot/usecases.py` добавить импорт `from bot.inventory.service import InventoryService`, вспомогательную функцию и изменить сценарии:
```python
async def _cancel_participant_orders(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int
) -> None:
    inventory = InventoryService(session)
    orders = await inventory.cancel_for_participant(training_id, user_id)
    if not orders:
        return
    from bot.inventory.models import InventoryItem

    labels = [(await session.get(InventoryItem, o.item_id)).label for o in orders]
    await messenger.notify(user_id, "Заказы инвентаря на тренировку отменены: " + ", ".join(labels))
```
- в `decline_training` внутри `if previous == ParticipationStatus.GOING:` перед `refresh_posts` добавить `await _cancel_participant_orders(session, messenger, training_id, user_id)`;
- в `manual_remove_participant` внутри `if removed:` добавить тот же вызов (до `refresh_posts`);
- в `cancel_trainings` внутри цикла по `cancelled` перед `announce_cancelled` добавить `await InventoryService(session).cancel_for_training(training.id)`;
- в `update_series` внутри цикла `for item in replaced:` первой строкой добавить `await InventoryService(session).cancel_for_training(item.old_id)`.

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest -v` → всё PASS.
```bash
git add -A
git commit -m "feat: inventory orders follow declines, cancellations and series edits; card button"
```

---

### Task 7: Telegram — `/profile` и `/my`

**Files:**
- Create: `bot/telegram/handlers/profile.py`, `bot/telegram/handlers/my.py`
- Modify: `bot/telegram/states.py`, `bot/participation/service.py` (метод `my_upcoming`)
- Test: `tests/participation/test_my.py`

**Interfaces:**
- Consumes: `UserService.update_profile`, `render_profile`, `InventoryService.my_orders/cancel_order`, `flow.render_my`.
- Produces:
  - `ParticipationService.my_upcoming(user_id, now, limit=15) -> list[tuple[Training, str]]` (будущие `scheduled` тренировки, где у пользователя есть запись: `going` или `declined`; по времени);
  - состояния `ProfileInput.value` (данные: `field`); `InventoryFlow` (см. Задачу 8);
  - команды `/profile` (кнопки `pf:h`, `pf:f`, `pf:m:<S|M|L>`, `pf:c:<тип>`), `/my` (кнопки `my:co:<order_id>`).

- [ ] **Step 1: Написать падающий тест**

`tests/participation/test_my.py`:
```python
from datetime import timedelta

from bot.participation.service import ParticipationService
from bot.schedule.models import TrainingStatus
from tests.factories import NOW, add_level, add_training, add_user


async def test_my_upcoming_lists_future_scheduled_with_my_status(session):
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_user(session, 1)
    svc = ParticipationService(session)
    mk = lambda hours, **kw: add_training(  # noqa: E731
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=hours),
        published_at=NOW - timedelta(hours=1), **kw)
    going = await mk(30)
    declined = await mk(50)
    past = await mk(-2)
    cancelled = await mk(70, status=TrainingStatus.CANCELLED)
    untouched = await mk(90)
    await svc.join(going.id, 1, NOW)
    await svc.decline(declined.id, 1, NOW)
    result = await svc.my_upcoming(1, NOW)
    assert [(t.id, s) for t, s in result] == [(going.id, "going"), (declined.id, "declined")]
```
(записи на прошлую/отменённую тренировку создавать нельзя — сервис откажет, поэтому они не попадают в выборку, а `untouched` не записана.)

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/participation/test_my.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

В `bot/participation/service.py` добавить метод:
```python
    async def my_upcoming(self, user_id: int, now: datetime, limit: int = 15) -> list[tuple[Training, str]]:
        rows = await self.session.execute(
            select(Training, Participation.status)
            .join(Participation, Participation.training_id == Training.id)
            .where(
                Participation.user_id == user_id,
                Training.status == TrainingStatus.SCHEDULED,
                Training.starts_at > now,
            )
            .order_by(Training.starts_at)
            .limit(limit)
        )
        return [(training, status) for training, status in rows]
```

Добавить в `bot/telegram/states.py`:
```python
class ProfileInput(StatesGroup):
    value = State()  # data: field


class InventoryFlow(StatesGroup):
    kinds = State()
    pedal = State()
    shoes = State()
    cleat = State()
    profile = State()   # текстовый ввод роста/размера ноги; data: ask
    helmet = State()
    review = State()


class ParkInput(StatesGroup):
    line = State()  # data: kind
```

`bot/telegram/handlers/profile.py`:
```python
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.users.models import User
from bot.users.profile import CLEAT_LABELS, CLEAT_TYPES, PROFILE_PROMPTS, render_profile
from bot.users.service import UserService
from bot.telegram.states import ProfileInput
from bot.telegram.ui import markup

router = Router()

SHORT_CLEAT = {"none": "Плоские", "spd": "SPD", "look": "Look", "spd_sl": "SPD-SL"}


def profile_markup():
    return markup(
        [("📏 Рост", "pf:h"), ("🦶 Размер ноги", "pf:f")],
        [("⛑ S", "pf:m:S"), ("⛑ M", "pf:m:M"), ("⛑ L", "pf:m:L")],
        [(SHORT_CLEAT[c], f"pf:c:{c}") for c in CLEAT_TYPES],
    )


@router.message(Command("profile"), F.chat.type == "private")
async def cmd_profile(message: Message, user: User, state: FSMContext) -> None:
    await state.clear()
    await message.answer(render_profile(user) + "\n\nШлем и шипы — кнопками, рост и размер ноги — числом.", reply_markup=profile_markup())


@router.callback_query(F.data.in_({"pf:h", "pf:f"}))
async def on_ask(cb: CallbackQuery, state: FSMContext) -> None:
    field = "height_cm" if cb.data == "pf:h" else "foot_eu"
    await state.clear()
    await state.set_state(ProfileInput.value)
    await state.update_data(field=field)
    await cb.message.answer(PROFILE_PROMPTS[field])
    await cb.answer()


@router.message(ProfileInput.value, F.text)
async def on_value(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    field = (await state.get_data())["field"]
    await UserService(session).update_profile(user, field, message.text)  # ошибка: состояние остаётся
    await state.clear()
    await message.answer(render_profile(user), reply_markup=profile_markup())


@router.callback_query(F.data.startswith("pf:m:") | F.data.startswith("pf:c:"))
async def on_choice(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    _, kind, value = cb.data.split(":")
    await UserService(session).update_profile(user, "helmet_size" if kind == "m" else "cleat_type", value)
    await cb.message.edit_text(render_profile(user), reply_markup=profile_markup())
    await cb.answer("Сохранено")
```
`bot/telegram/handlers/my.py`:
```python
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.inventory.flow import MyEntry, render_my
from bot.inventory.service import InventoryService
from bot.participation.service import ParticipationService
from bot.telegram.ui import club_tz, markup, short_title
from bot.users.models import User

router = Router()


async def _render(session: AsyncSession, user: User):
    now = utcnow()
    tz = await club_tz(session)
    views = await InventoryService(session).my_orders(user.telegram_id, now)
    orders_by_training: dict[int, list[str]] = {}
    for view in views:
        orders_by_training.setdefault(view.training.id, []).append(view.item.label)
    entries = [
        MyEntry(short_title(t, tz), "идёте" if status == "going" else "не идёте", orders_by_training.get(t.id, []))
        for t, status in await ParticipationService(session).my_upcoming(user.telegram_id, now)
    ]
    rows = [[(f"🗑 Отменить: {v.item.label}", f"my:co:{v.order.id}")] for v in views]
    return render_my(entries), markup(*rows) if rows else None


@router.message(Command("my"), F.chat.type == "private")
async def cmd_my(message: Message, session: AsyncSession, user: User) -> None:
    text, keyboard = await _render(session, user)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("my:co:"))
async def on_cancel_order(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await InventoryService(session).cancel_order(int(cb.data.split(":")[2]), user.telegram_id, utcnow())
    text, keyboard = await _render(session, user)
    await cb.message.edit_text(text, reply_markup=keyboard)
    await cb.answer("Заказ отменён")
```

- [ ] **Step 4: Прогнать, закоммитить**

Run: `python -m pytest tests/participation -v` → PASS; `python -c "import bot.telegram.handlers.profile, bot.telegram.handlers.my"` → без ошибок.
```bash
git add -A
git commit -m "feat(telegram): /profile and /my"
```

---

### Task 8: Telegram — заказ инвентаря (диалог)

**Files:**
- Create: `bot/telegram/handlers/inventory.py`
- Modify: `bot/telegram/handlers/common.py` (диплинк `inv_`), `bot/telegram/handlers/trainer.py` (кнопка сводки)

**Interfaces:**
- Consumes: `InventoryService`, `Wanted`, `Profile`, `missing_profile_fields`, `flow.*`, `PROFILE_FIELDS`, `UserService.update_profile`.
- Produces: роутер `inventory.router`; функция `begin_inventory(message, state, session, user, training_id)`; callback-данные: `inv:<id>` (кнопка на карточке), `iv:k:<kind>`, `iv:kdone`, `iv:p:<тип педалей>`, `iv:s:own|park`, `iv:c:<тип шипов>`, `iv:h:<S|M|L>`, `iv:ok`, `iv:no`, `tr:inv:<id>` (сводка тренера). Данные FSM: `training_id`, `kinds` (виды из парка), `pedal_type`, `shoes_own` (bool), `own_cleat`, `confirmed` (метки), `suggestions` (список словарей `kind,status,item_id,text`), `ask` (поле профиля), `replacement` (bool).

**Порядок вопросов (`_advance`):** педали → источник туфель → (шипы своих туфель) → недостающие поля профиля (рост, размер ноги, шлем) → подбор и обзор.

- [ ] **Step 1: Реализовать**

`bot/telegram/handlers/inventory.py`:
```python
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.messenger import Messenger
from bot.inventory.flow import can_confirm, format_review, kinds_markup_data
from bot.inventory.matching import PEDAL_LABELS, PEDAL_TYPES, Profile, Suggestion, Wanted, missing_profile_fields
from bot.inventory.service import InventoryService, NotGoing
from bot.inventory.summary import breakdown, summarize
from bot.schedule.models import Training
from bot.telegram.states import InventoryFlow
from bot.telegram.ui import club_tz, markup, short_title
from bot.users.models import User
from bot.users.profile import CLEAT_LABELS, CLEAT_TYPES, PROFILE_FIELDS, PROFILE_PROMPTS
from bot.users.service import UserService

router = Router()


def _kinds_markup(selected: set[str]):
    return markup(*[[row] for row in kinds_markup_data(selected)], [("✖️ Отмена", "iv:no")])


# ---------- вход ----------

async def begin_inventory(message: Message, state: FSMContext, session: AsyncSession, user: User, training_id: int) -> None:
    try:
        await InventoryService(session).check_can_order(training_id, user.telegram_id, utcnow())
    except NotGoing as exc:
        await message.answer(exc.message, reply_markup=markup([("✅ Записаться", f"join:{training_id}")]))
        return
    await state.clear()
    await state.set_state(InventoryFlow.kinds)
    await state.update_data(training_id=training_id, kinds=[], confirmed=[], replacement=False)
    await message.answer("Что нужно на тренировку?", reply_markup=_kinds_markup(set()))


@router.callback_query(F.data.startswith("inv:"))
async def on_inventory_button(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    training_id = int(cb.data.split(":")[1])
    if cb.message.chat.type == "private":
        await begin_inventory(cb.message, state, session, user, training_id)
        await cb.answer()
        return
    username = (await cb.bot.me()).username
    await cb.answer(url=f"https://t.me/{username}?start=inv_{training_id}")  # «Продолжим в личке»


# ---------- виды ----------

@router.callback_query(InventoryFlow.kinds, F.data.startswith("iv:k:"))
async def on_kind_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    kind = cb.data.split(":")[2]
    selected = set((await state.get_data())["kinds"])
    selected.symmetric_difference_update({kind})
    await state.update_data(kinds=sorted(selected))
    await cb.message.edit_reply_markup(reply_markup=_kinds_markup(selected))
    await cb.answer()


@router.callback_query(InventoryFlow.kinds, F.data == "iv:kdone")
async def on_kinds_done(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    if not data["kinds"]:
        await cb.answer("Выберите хотя бы одну позицию", show_alert=True)
        return
    await state.update_data(pedal_type=None, shoes_own=None, own_cleat=None)
    await _advance(cb.message, state, session, user)
    await cb.answer()


# ---------- уточнения ----------

async def _advance(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    kinds = set(data["kinds"])
    if "pedals" in kinds and not data.get("pedal_type"):
        await state.set_state(InventoryFlow.pedal)
        rows = [[(PEDAL_LABELS[p].capitalize(), f"iv:p:{p}")] for p in PEDAL_TYPES]
        await message.answer("Какие педали?", reply_markup=markup(*rows))
        return
    if "shoes" in kinds and data.get("shoes_own") is None:
        await state.set_state(InventoryFlow.shoes)
        await message.answer(
            "Туфли свои или из парка?",
            reply_markup=markup([("Свои", "iv:s:own"), ("Из парка", "iv:s:park")]),
        )
        return
    if data.get("shoes_own") and not data.get("own_cleat"):
        await state.set_state(InventoryFlow.cleat)
        rows = [[(CLEAT_LABELS[c], f"iv:c:{c}")] for c in CLEAT_TYPES]
        await message.answer("Какие шипы на ваших туфлях?", reply_markup=markup(*rows))
        return
    park_kinds = kinds - {"shoes"} if data.get("shoes_own") else kinds
    profile = Profile(user.height_cm, user.foot_eu, user.helmet_size)
    missing = missing_profile_fields(profile, park_kinds)
    if missing:
        field = missing[0]
        if field == "helmet_size":
            await state.set_state(InventoryFlow.helmet)
            await message.answer(
                "Размер шлема?", reply_markup=markup([("S", "iv:h:S"), ("M", "iv:h:M"), ("L", "iv:h:L")])
            )
        else:
            await state.set_state(InventoryFlow.profile)
            await state.update_data(ask=field)
            await message.answer(PROFILE_PROMPTS[field])
        return
    await _review(message, state, session, user, park_kinds)


@router.callback_query(InventoryFlow.pedal, F.data.startswith("iv:p:"))
async def on_pedal(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    pedal_type = cb.data.split(":")[2]
    await state.update_data(pedal_type=pedal_type)
    data = await state.get_data()
    if data.get("own_cleat"):  # свои туфли уже известны — проверяем сразу
        problem = await InventoryService(session).own_shoes_problem(data["own_cleat"], pedal_type)
        if problem:
            await state.update_data(pedal_type=None)
            await cb.message.answer(problem + " Выберите педали заново.")
    await _advance(cb.message, state, session, user)
    await cb.answer()


@router.callback_query(InventoryFlow.shoes, F.data.startswith("iv:s:"))
async def on_shoes_source(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    own = cb.data.endswith(":own")
    await state.update_data(shoes_own=own)
    await _advance(cb.message, state, session, user)
    await cb.answer()


@router.callback_query(InventoryFlow.cleat, F.data.startswith("iv:c:"))
async def on_cleat(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    cleat = cb.data.split(":")[2]
    await UserService(session).update_profile(user, "cleat_type", cleat)  # сохраняется в профиль
    await state.update_data(own_cleat=cleat)
    data = await state.get_data()
    if data.get("pedal_type"):
        problem = await InventoryService(session).own_shoes_problem(cleat, data["pedal_type"])
        if problem:
            await state.update_data(pedal_type=None)
            await cb.message.answer(problem + " Выберите педали заново.")
    await _advance(cb.message, state, session, user)
    await cb.answer()


@router.callback_query(InventoryFlow.helmet, F.data.startswith("iv:h:"))
async def on_helmet(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await UserService(session).update_profile(user, "helmet_size", cb.data.split(":")[2])
    await _advance(cb.message, state, session, user)
    await cb.answer()


@router.message(InventoryFlow.profile, F.text)
async def on_profile_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    field = (await state.get_data())["ask"]
    await UserService(session).update_profile(user, field, message.text)  # ошибка ввода: вопрос повторится
    await _advance(message, state, session, user)


# ---------- обзор и подтверждение ----------

async def _review(message: Message, state: FSMContext, session: AsyncSession, user: User, park_kinds: set[str]) -> None:
    data = await state.get_data()
    wanted = Wanted(frozenset(park_kinds), pedal_type=data.get("pedal_type"), own_shoes_cleat=data.get("own_cleat"))
    suggestions = await InventoryService(session).suggest(data["training_id"], user, wanted, utcnow())
    await state.update_data(suggestions=[s.__dict__ for s in suggestions])
    await state.set_state(InventoryFlow.review)
    buttons = [[("✅ Подтвердить", "iv:ok")]] if can_confirm(suggestions) else []
    buttons.append([("✖️ Отмена", "iv:no")])
    await message.answer(
        format_review(suggestions, data.get("confirmed", []), data.get("replacement", False)),
        reply_markup=markup(*buttons),
    )


@router.callback_query(InventoryFlow.review, F.data == "iv:ok")
async def on_confirm(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    suggestions = [Suggestion(**d) for d in data["suggestions"]]
    item_ids = [s.item_id for s in suggestions if s.item_id is not None]
    result = await InventoryService(session).confirm(data["training_id"], user.telegram_id, item_ids, utcnow())
    from bot.inventory.models import InventoryItem

    confirmed = list(data.get("confirmed", []))
    for order in result.orders:
        confirmed.append((await session.get(InventoryItem, order.item_id)).label)
    if result.conflicts:
        # забрали единицу: подбираем замену только для неё, подтверждённое сохраняется
        await state.update_data(confirmed=confirmed, kinds=result.conflicts, replacement=True)
        data = await state.get_data()
        await _review(cb.message, state, session, user, set(result.conflicts))
        await cb.answer()
        return
    await state.clear()
    not_found = [s.text for s in suggestions if s.item_id is None]
    text = "✅ Заказано: " + ", ".join(confirmed) + "\nОтменить заказ можно в /my до начала тренировки."
    if not_found:
        text += "\n\nНе нашлось:\n" + "\n".join(not_found)
    await cb.message.edit_text(text)
    await cb.answer()


@router.callback_query(F.data == "iv:no")
async def on_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Отменено")
    await cb.answer()


# ---------- сводка тренера ----------

@router.callback_query(F.data.startswith("tr:inv:"))
async def on_trainer_summary(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    training_id = int(cb.data.split(":")[2])
    lines = await InventoryService(session).training_summary(user, training_id)
    training = await session.get(Training, training_id)
    tz = await club_tz(session)
    text = f"{short_title(training, tz)}\n{summarize(lines)}"
    if lines:
        text += "\n\n" + breakdown(lines)
    await cb.message.edit_text(text, reply_markup=markup([("⬅️ Назад", f"tr:t:{training_id}")]))
    await cb.answer()
```
Примечание по состояниям `iv:no` без активного состояния: обработчик не привязан к состоянию, потому что кнопка «Отмена» есть и на экране видов (состояние `kinds`), и на обзоре.

- [ ] **Step 2: Подключить диплинк `inv_` и кнопку сводки**

В `bot/telegram/handlers/common.py` в `cmd_start_link` расширить сигнатуру `state: FSMContext` и добавить ветку до `await cmd_start(...)`:
```python
    if arg.startswith("inv_") and arg[4:].isdigit():
        from bot.telegram.handlers.inventory import begin_inventory

        await begin_inventory(message, state, session, user, int(arg[4:]))
        return
```
(`from aiogram.fsm.context import FSMContext` добавить в импорты.)

В `bot/telegram/handlers/trainer.py` в клавиатуре `on_detail` после строки с участниками добавить строку:
```python
            [("🚲 Инвентарь", f"tr:inv:{tid}")],
```

- [ ] **Step 3: Проверить импорты и закоммитить**

Run: `python -c "import bot.telegram.handlers.inventory"` → без ошибок.
```bash
git add -A
git commit -m "feat(telegram): inventory ordering flow, deep link, trainer summary"
```

---

### Task 9: Telegram — парк инвентаря (админ), настройки, запуск

**Files:**
- Create: `bot/telegram/handlers/park.py`
- Modify: `bot/telegram/handlers/admin.py` (меню и подпись настройки), `bot/core/settings.py`, `bot/main.py`
- Test: `tests/core/test_settings.py` (дополнить)

**Interfaces:**
- Consumes: `InventoryService.add_item/set_active/list_items`, `parse_item_line`, `ITEM_FORMATS`.
- Produces: callback-данные `ad:park`, `ad:pk:<id>` (переключить активность), `ad:pka` (добавить), `ad:pkk:<kind>`; в настройках — `inventory_deadline_h`.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/core/test_settings.py`:
```python
def test_inventory_deadline_accepts_dash_as_no_limit():
    assert validate_setting("inventory_deadline_h", "-") == ""
    assert validate_setting("inventory_deadline_h", "нет") == ""
    assert validate_setting("inventory_deadline_h", " 3 ") == "3"
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/core/test_settings.py -v` → FAIL.

- [ ] **Step 3: Реализовать**

В `bot/core/settings.py` в ветке `inventory_deadline_h` функции `validate_setting` заменить тело:
```python
    elif key == "inventory_deadline_h":
        if value in ("-", "нет"):
            value = ""
        elif not value.isdigit():
            raise InvalidAction("Нужно целое число часов или «-» (без ограничения)")
```

В `bot/telegram/handlers/admin.py`:
- в `ADMIN_MENU` добавить строку `[("🚲 Парк инвентаря", "ad:park")]` перед строкой с приглашением;
- в `SETTING_LABELS` добавить `"inventory_deadline_h": "Дедлайн заказа инвентаря, ч до старта («-» — нет)"`;
- в `on_settings` значение пустой настройки показывать как «нет»: заменить выражение значения на `(await get_setting(session, key)) or "нет"`.

`bot/telegram/handlers/park.py`:
```python
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.permissions import require_admin
from bot.inventory.items import ITEM_FORMATS, parse_item_line
from bot.inventory.matching import KIND_LABELS
from bot.inventory.service import InventoryService
from bot.telegram.states import ParkInput
from bot.telegram.ui import markup
from bot.users.models import User

router = Router()


def _describe(item) -> str:
    detail = {
        "bike": f"рама {item.frame}, рост {item.height_min}–{item.height_max}",
        "pedals": f"{item.pedal_type}",
        "shoes": f"{item.shoe_eu}, шипы {item.cleat_type}",
        "helmet": f"{item.helmet_size}",
    }[item.kind]
    return f"{item.label} · {detail}"


async def _show_park(cb: CallbackQuery, session: AsyncSession) -> None:
    items = await InventoryService(session).list_items()
    rows = [[(("✅ " if i.active else "🚫 ") + _describe(i), f"ad:pk:{i.id}")] for i in items]
    rows.append([("➕ Добавить единицу", "ad:pka")])
    rows.append([("⬅️ Меню", "ad:menu")])
    await cb.message.edit_text(
        "Парк инвентаря (нажмите, чтобы вывести из парка / вернуть):" if items else "Парк пуст",
        reply_markup=markup(*rows),
    )


@router.callback_query(F.data == "ad:park")
async def on_park(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await _show_park(cb, session)
    await cb.answer()


@router.callback_query(F.data.startswith("ad:pk:"))
async def on_toggle(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    item_id = int(cb.data.split(":")[2])
    items = {i.id: i for i in await InventoryService(session).list_items()}
    await InventoryService(session).set_active(user, item_id, not items[item_id].active)
    await _show_park(cb, session)
    await cb.answer()


@router.callback_query(F.data == "ad:pka")
async def on_add(cb: CallbackQuery, user: User) -> None:
    require_admin(user)
    rows = [[(KIND_LABELS[k], f"ad:pkk:{k}")] for k in ITEM_FORMATS]
    await cb.message.edit_text("Что добавить?", reply_markup=markup(*rows, [("⬅️ Назад", "ad:park")]))
    await cb.answer()


@router.callback_query(F.data.startswith("ad:pkk:"))
async def on_kind(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    kind = cb.data.split(":")[2]
    await state.clear()
    await state.set_state(ParkInput.line)
    await state.update_data(kind=kind)
    await cb.message.answer(f"Одной строкой через «;»:\n{ITEM_FORMATS[kind]}")
    await cb.answer()


@router.message(ParkInput.line, F.text)
async def on_line(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    kind = (await state.get_data())["kind"]
    label, params = parse_item_line(kind, message.text)  # ошибка формата: состояние остаётся
    item = await InventoryService(session).add_item(user, kind, label, **params)
    await state.clear()
    await message.answer(f"Добавлено: {item.label} ✅", reply_markup=markup([("⬅️ К парку", "ad:park")]))
```

В `bot/main.py` добавить `inventory, my, park, profile` в импорт `from bot.telegram.handlers import ...` и в `dp.include_routers(...)`: `profile.router, my.router, inventory.router, park.router` (до `schedule.router` и `participation.router`; `inventory.router` должен стоять раньше `trainer.router`? обработчики не пересекаются: префиксы `inv:`, `iv:`, `tr:inv:` — `tr:inv:` объявлен в `inventory.router`, а у `trainer.router` нет шаблона `tr:inv:`).

- [ ] **Step 4: Прогнать всё и закоммитить**

Run: `python -c "import bot.main"`, затем `python -m pytest -v` → всё PASS.
```bash
git add -A
git commit -m "feat(telegram): park management, inventory deadline setting, wiring"
```

---

### Task 10: Ручная проверка

- [ ] **Step 1: Подготовка.** `alembic upgrade head`, `python -m bot.main`. Нужны: админ, тренер с чатом и тренировкой (план 2), два участника.
- [ ] **Step 2: Парк.** `/admin` → «🚲 Парк инвентаря» → «Добавить»: велосипед `Велосипед №1; M; 165; 178`, `Велосипед №2; L; 178; 192`, педали `Педали №1; spd`, туфли `Туфли №1; 42; spd`, шлем `Шлем №1; M`. Строка `Велосипед; M; 178; 165` → понятная подсказка формата, состояние остаётся.
- [ ] **Step 3: Заказ.** Участник без записи нажимает «🚲 Инвентарь» в группе → открывается личка (ссылка), бот предлагает сначала записаться. После записи: выбрать «Велосипед», «Педали», «Туфли», «Шлем» → бот спрашивает тип педалей, источник туфель, недостающие рост/размер ноги/шлем (в профиль). Результат: перечень с пояснениями; «Подтвердить» → «Заказано».
- [ ] **Step 4: Конкуренция.** Второй участник заказывает те же позиции: уже занятые единицы не предлагаются; если один успел подтвердить раньше (два телефона), второй видит «Эту единицу только что забрали — предлагаю замену» только для неё.
- [ ] **Step 5: Свои туфли.** Выбрать педали Look и «свои туфли» со шипами SPD → сразу «Ваши туфли (SPD) не подходят к педалям Look. Выберите педали заново».
- [ ] **Step 6: Нет в наличии.** Заказать размер ноги 47 → «Туфли: нет в наличии на это время — напишите тренеру», остальные позиции заказываются.
- [ ] **Step 7: `/my`.** Видны тренировки и заказы; «🗑 Отменить: Велосипед №1» освобождает единицу (проверить вторым участником).
- [ ] **Step 8: Хуки.** Участник нажимает «❌ Не пойду» → приходит «Заказы инвентаря на тренировку отменены: …». Тренер отменяет тренировку → в `/my` заказов нет.
- [ ] **Step 9: Сводка.** `/trainer` → тренировка → «🚲 Инвентарь»: `велосипеды M×2, L×1; педали spd×2; туфли 42×2; шлем M×1` и разбивка по участникам.
- [ ] **Step 10: Дедлайн.** `/admin` → «⚙️ Настройки» → дедлайн `3`: за 2 часа до старта заказ отклоняется («Приём заказов закрыт за 3 ч»); `-` снимает ограничение.
- [ ] **Step 11: Профиль.** `/profile`: рост `1700` → подсказка, профиль не изменился; шлем и шипы меняются кнопками.

---

## Self-Review

**Покрытие спецификации (область плана 3):**
- Модель `InventoryItem`, `InventoryOrder` (с `price`, `paid_status`, `price_per_use`), диапазоны роста и совместимость шипов данными → Задача 1.
- Подбор: велосипед по росту/ближайшая рама, туфли по размеру/шипам/±1, шлем по размеру/ближайший, «нет в наличии» с советом написать тренеру, проверка совместимости набора → Задача 3.
- Пересечения по времени (`a.starts_at < b.ends_at AND b.starts_at < a.ends_at`), занятость активными заказами → Задача 4.
- Проверки: тренировка `scheduled`, не началась, дедлайн, участник `going` + предложение записаться → Задача 4 (сервис), 8 (UI).
- Повторная проверка свободы в транзакции, замена только для забранной, сохранение подтверждённого → Задачи 4, 8.
- Отмена заказа до старта с мгновенным освобождением → Задача 4; отказ/отмена тренировки/правка серии отменяют заказы, отказ — с уведомлением → Задача 6.
- Входы: кнопка «🚲 Инвентарь», «Продолжим в личке» со ссылкой `?start=inv_<id>` → Задача 8; `/profile`, `/my` → Задача 7; сводка тренера и разбивка по участникам → Задачи 5, 8; парк в `/admin` → Задача 9.
- Не входит: оплата, отметка выдачи, очередь → соблюдено (только поля).

**Сканирование на заглушки:** TBD/TODO нет. Утверждения тестов сверены с реализацией.

**Согласованность типов:** `Wanted(kinds, pedal_type, own_shoes_cleat)`, `Profile`, `Suggestion(kind, status, item_id, text)`, `Candidate`, `ConfirmResult(orders, conflicts)`, `OrderLine`, `NotGoing` одинаковы в Задачах 3–8. `InventoryService.cancel_for_participant/cancel_for_training` используются в Задаче 6 как определено в Задаче 4. Callback-префиксы `inv:`, `iv:`, `pf:`, `my:`, `ad:pk*`, `tr:inv:` не пересекаются с планами 1–2.

**Review Focus:** п.1 — Задача 4 (`test_last_item_race...`); п.2 — Задача 4 (`test_overlapping_training...`); п.3 — Задача 6; п.4 — Задачи 3, 4, 8; п.5 — Задача 2.
