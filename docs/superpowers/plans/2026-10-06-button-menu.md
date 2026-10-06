# Coach's Shadow — План: кнопочное меню вместо слеш-команд

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/start` показывает главное меню на inline-кнопках (расписание, мои записи, профиль, тренер, админ по ролям); админ-меню и меню тренера достижимы кнопками и возвращают назад; уровень создаётся диалогом с кнопкой «Отмена»; чат привязывается к уровню выбором из списка; в меню команд Telegram остаются только `/start` и `/whoami`.

**Architecture:** Чистые построители клавиатур и константы `callback_data` — в новом `bot/telegram/menu.py` (без БД и Telegram API, тестируются напрямую). Хендлеры меню — в `handlers/common.py`; тела команд `/schedule`, `/my`, `/profile` выносятся в общие функции `send_schedule` / `send_my` / `send_profile`, которые зовут и команда, и кнопка. Привязка чата — новый `handlers/binding.py` (групповые колбэки, без фильтра «только личка»). Сервис получает `list_levels()` / `get_level()` и валидацию названия уровня.

**Tech Stack:** Python 3.11+, aiogram 3, SQLAlchemy 2 async + aiosqlite, pytest (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-10-06-button-menu-design.md`
**Выполненные планы, на которых стоит этот:** `2026-10-06-engine-schedule-signup.md`, `2026-10-06-roles-trainer-admin-ui.md`, `2026-10-06-inventory-profile.md`.

## Отступления от спецификации и состояние кода

Спека писалась до плана инвентаря; код ушёл вперёд. Учтено так:

- **Админ-меню и диалог уровня уже есть** (`ADMIN_MENU` в `handlers/admin.py`, `ad:addlvl` → `AdminInput`, purpose `add_level`). Новых состояний и префикса `admin:*` нет: дорабатывается существующее (`ad:cancel`, возврат в админ-меню, «⬅️ Главное меню»). Кнопка «➕ Добавить уровень» на экране уровней переименовывается в «➕ Новый уровень» (как в спеке).
- **`ADMIN_MENU` теперь содержит строку «🚲 Парк инвентаря» (`ad:park`)**; при переносе в `menu.py` она обязана сохраниться (тест в Задаче 2).
- **Кнопка «🎓 Тренер»** в главном меню: без неё `/trainer` недоступна после сужения меню команд. Доступ проверяет `ensure_trainer`.
- **Кнопки «📋 Мои записи» (`menu:my`) и «👤 Профиль» (`menu:profile`)** для всех ролей; присылают новое сообщение и сбрасывают FSM.
- **Спека утверждает «хендлеры напрямую не тестируются», но в репозитории они тестируются** (`tests/telegram/test_inventory_handlers.py`, `test_park_handlers.py`: фейковые `Message`/`CallbackQuery` + настоящий `FSMContext`). План следует фактической практике. Существующие `FakeMessage`/`FakeCallback` не хранят `chat.title` и разметку правок, поэтому для новых тестов создаётся `tests/telegram/fakes.py`.
- **Привязка чата:** `/bind_level` и `bind:<id>` переезжают из `admin.py` в `binding.py`: роутер `admin` целиком фильтрует колбэки по «только личка».
- **Название уровня:** максимум 50 символов (колонка 100, но название должно помещаться в кнопку), дубликат — понятная `InvalidAction` (иначе сырой `IntegrityError` из-за `unique`).
- **Не меняются:** deep link `/start inv_<id>`; тексты со ссылками на `/my`, `/profile`, `/schedule` в сервисах инвентаря, `park.py`, `trainer.py`, `trainer_forms.py`, `publishing/service.py` (команды продолжают работать, часть тестов проверяет эти тексты).

## Global Constraints

- Язык интерфейса — русский. Иконки: «📅 Расписание», «📋 Мои записи», «👤 Профиль», «🎓 Тренер», «⚙️ Админ», «➕ Новый уровень», «⬅️ Главное меню», «✖️ Отмена», «🔗 *Название*».
- aiogram импортируется только в `bot/telegram/`. `menu.py` — построители клавиатур; `keyboards.py` (рендерер постов) не меняется.
- Роль проверяется на сервере (`require_admin` / `ensure_trainer`), а не скрытием кнопки; устаревшая кнопка → алерт через `on_domain_error`.
- `callback_data` не длиннее 64 байт. Новые префиксы `menu:` и `bind:` не пересекаются с `join:/decline:/who:/noop`, `tr:`, `nf:`, `pk:`, `ch:`, `ad:`, `my:`, `pf:`, `inv:`.
- Меню в личке правят текущее сообщение (`safe_edit`); «Расписание», «Мои записи», «Профиль» присылают новое. В группе лишних сообщений нет: список уровней правится в результат привязки.
- Нажатие кнопок меню сбрасывает FSM (как слеш-команда через `clear_state_on_command`).
- Обработчики Telegram следуют правилам проекта: `take_form` до сервиса, `restore_form` при `DomainError`, правки экранов через `safe_edit`, каждый роутер заканчивается catch-all `<prefix>:`.
- Миграции и новые модели не нужны. Логирование — только stdlib `logging`. Тест конкурентных записей в `tests/participation/` не трогать и держать зелёным.

## Review Focus

1. **Устаревшая кнопка после смены роли:** «⚙️ Админ» у разжалованного админа → «Только для администратора», «🎓 Тренер» у снятого тренера → «Раздел для тренеров»; меню не открывается (Задача 3).
2. **Кривое название уровня:** пустое, из пробелов, длиннее 50, точный дубликат — понятная ошибка, диалог остаётся открытым, можно повторить или нажать «Отмена» (Задачи 1, 4).
3. **Привязка в группе:** нет уровней (подсказка), не админ (отказ, ничего не привязано), уровень удалён («Уровень не найден»), мусор в `bind:<id>`, повторное нажатие (Задача 5).
4. **Кнопка меню посреди ввода:** «⬅️ Главное меню», «✖️ Отмена», «📋 Мои записи», «👤 Профиль» во время ввода названия уровня сбрасывают состояние, и следующий текст не становится названием (Задачи 3, 4).
5. **Регресс админ-меню:** при переносе в `menu.py` не потерялись «🚲 Парк инвентаря» и прочие кнопки; `/admin` и `/trainer` как слеш-команды работают (Задачи 2, 3).

---

## Структура файлов

```
bot/
  schedule/service.py        (+list_levels, +get_level, +MAX_LEVEL_NAME, валидация в add_level)
  telegram/
    menu.py                  (новый: константы, main_menu, ADMIN_MENU, TRAINER_MENU, levels_keyboard, cancel_keyboard, BOT_COMMANDS)
    handlers/
      common.py              (/start с меню, колбэки menu:*)
      schedule.py            (send_schedule вынесен из cmd_schedule)
      my.py                  (send_my вынесен из cmd_my)
      profile.py             (send_profile вынесен из cmd_profile)
      admin.py               (меню из menu.py, «Отмена», «➕ Новый уровень», возврат в меню; /bind_level убран)
      trainer.py             (TRAINER_MENU из menu.py)
      binding.py             (новый: /bind_level, bind:<id>)
  main.py                    (+binding.router, +set_my_commands)
tests/
  schedule/test_service.py       (+list_levels, get_level, валидация)
  telegram/fakes.py              (новый: FakeMessage, FakeCallback, fsm, callback_data)
  telegram/test_menu.py          (новый)
  telegram/test_menu_handlers.py (новый)
  telegram/test_admin_levels.py  (новый)
  telegram/test_binding.py       (новый)
README.md, CLAUDE.md, ARCHITECTURE.md (команды и меню)
```

---

### Task 1: Сервис — `list_levels`, `get_level`, валидация названия

**Files:**
- Modify: `bot/schedule/service.py` (`add_level`, ~стр. 71)
- Test: `tests/schedule/test_service.py`

**Interfaces:**
- Consumes: `Level` (`bot/schedule/models.py`), `require_admin`, `InvalidAction`, `NotFound` (`bot/core/errors.py`), фабрики `add_user`, `add_level` (`tests/factories.py`).
- Produces: `ScheduleService.list_levels() -> list[Level]` (порядок `position`, затем `name`; без проверки прав); `ScheduleService.get_level(level_id: int) -> Level` (бросает `NotFound("Уровень не найден")`); константа `MAX_LEVEL_NAME = 50`; `add_level` бросает `InvalidAction` для названия длиннее лимита и для дубликата. Сигнатура `bind_chat` не меняется.

- [ ] **Step 1: Write the failing tests**

Добавить в конец раздела про уровни и чаты в `tests/schedule/test_service.py` (проверить, что `NotFound`, `InvalidAction`, `add_level`, `add_user` там уже импортированы; недостающее добавить в импорты):

```python
async def test_list_levels_orders_by_position_then_name(session):
    svc = ScheduleService(session)
    assert await svc.list_levels() == []
    await add_level(session, "Б", position=1)
    await add_level(session, "Я", position=0)
    await add_level(session, "А", position=1)
    assert [lvl.name for lvl in await svc.list_levels()] == ["Я", "А", "Б"]


async def test_get_level_found_and_missing(session):
    level = await add_level(session, "Новички")
    svc = ScheduleService(session)
    assert (await svc.get_level(level.id)).name == "Новички"
    with pytest.raises(NotFound, match="Уровень не найден"):
        await svc.get_level(level.id + 100)


async def test_add_level_rejects_too_long_duplicate_and_blank_names(session):
    admin = await add_user(session, 99, "admin")
    svc = ScheduleService(session)
    with pytest.raises(InvalidAction, match="длиннее"):
        await svc.add_level(admin, "х" * 51)
    await svc.add_level(admin, "Новички")
    with pytest.raises(InvalidAction, match="уже есть"):
        await svc.add_level(admin, "  Новички ")
    with pytest.raises(InvalidAction):
        await svc.add_level(admin, "   ")
    assert [lvl.name for lvl in await svc.list_levels()] == ["Новички"]
    await svc.add_level(admin, "х" * 50)  # граница допустима


async def test_bind_chat_to_level_found_by_id(session):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    svc = ScheduleService(session)
    found = await svc.get_level(level.id)
    chat = await svc.bind_chat(admin, -1001, "Новички клуба", found.name)
    assert chat.level_id == level.id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/schedule/test_service.py -k "list_levels or get_level or too_long or found_by_id" -v`
Expected: FAIL (`AttributeError: ... no attribute 'list_levels'`; в третьем тесте нет `InvalidAction` для длинного названия).

- [ ] **Step 3: Implement**

В `bot/schedule/service.py` после импортов добавить `MAX_LEVEL_NAME = 50`; заменить `add_level` и добавить два метода сразу после (если `InvalidAction`, `NotFound`, `select` уже импортированы — повторно не добавлять):

```python
MAX_LEVEL_NAME = 50
```

```python
    async def add_level(self, actor: User, name: str, position: int = 0) -> Level:
        require_admin(actor)
        name = name.strip()
        if not name:
            raise InvalidAction("Название уровня не может быть пустым")
        if len(name) > MAX_LEVEL_NAME:
            raise InvalidAction(f"Название уровня длиннее {MAX_LEVEL_NAME} символов")
        if await self.session.scalar(select(Level.id).where(Level.name == name)) is not None:
            raise InvalidAction(f"Уровень «{name}» уже есть")
        level = Level(name=name, position=position)
        self.session.add(level)
        await self.session.commit()
        return level

    async def list_levels(self) -> list[Level]:
        return list(await self.session.scalars(select(Level).order_by(Level.position, Level.name)))

    async def get_level(self, level_id: int) -> Level:
        level = await self.session.get(Level, level_id)
        if level is None:
            raise NotFound("Уровень не найден")
        return level
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/schedule -q`
Expected: PASS (существующие тесты `add_level` / `bind_chat` зелёные).

- [ ] **Step 5: Commit**

```bash
git add bot/schedule/service.py tests/schedule/test_service.py
git commit -m "feat(schedule): list_levels, get_level, level name validation"
```

---

### Task 2: Построители клавиатур — `bot/telegram/menu.py`

**Files:**
- Create: `bot/telegram/menu.py`
- Modify: `bot/telegram/handlers/admin.py` (удалить локальный `ADMIN_MENU`, импортировать из `menu`), `bot/telegram/handlers/trainer.py` (то же для `TRAINER_MENU`)
- Test: `tests/telegram/test_menu.py`

**Interfaces:**
- Consumes: `markup(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup` (`bot/telegram/ui.py`); `Role` с константами-строками `ADMIN`/`TRAINER`/`MEMBER` (`bot/core/permissions.py`); `Level` (`bot/schedule/models.py`).
- Produces (всё из `bot.telegram.menu`):
  - `callback_data`: `MENU_SCHEDULE="menu:schedule"`, `MENU_MY="menu:my"`, `MENU_PROFILE="menu:profile"`, `MENU_TRAINER="menu:trainer"`, `MENU_ADMIN="menu:admin"`, `MENU_BACK="menu:back"`, `BIND_PREFIX="bind:"`, `ADD_LEVEL_CANCEL="ad:cancel"`;
  - тексты: `TEXT_SCHEDULE="📅 Расписание"`, `TEXT_MY="📋 Мои записи"`, `TEXT_PROFILE="👤 Профиль"`, `TEXT_TRAINER="🎓 Тренер"`, `TEXT_ADMIN="⚙️ Админ"`, `TEXT_BACK="⬅️ Главное меню"`, `TEXT_CANCEL="✖️ Отмена"`;
  - `main_menu(role: str) -> InlineKeyboardMarkup`: расписание, «Мои записи», «Профиль» всем; «🎓 Тренер» ролям `trainer` и `admin`; «⚙️ Админ» только `admin`; по одной кнопке в строке;
  - `ADMIN_MENU`, `TRAINER_MENU`: прежние кнопки без изменений плюс последняя строка `[(TEXT_BACK, MENU_BACK)]`;
  - `levels_keyboard(levels: list[Level]) -> InlineKeyboardMarkup`: «🔗 *Название*» с `bind:<id>` в отдельной строке;
  - `cancel_keyboard() -> InlineKeyboardMarkup`: «✖️ Отмена» (`ad:cancel`).

- [ ] **Step 1: Write the failing tests**

`tests/telegram/test_menu.py`:

```python
from bot.core.permissions import Role
from bot.schedule.models import Level
from bot.telegram import menu


def buttons(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


COMMON = [("📅 Расписание", "menu:schedule"), ("📋 Мои записи", "menu:my"), ("👤 Профиль", "menu:profile")]


def test_main_menu_by_role():
    assert buttons(menu.main_menu(Role.MEMBER)) == COMMON
    assert buttons(menu.main_menu(Role.TRAINER)) == COMMON + [("🎓 Тренер", "menu:trainer")]
    assert buttons(menu.main_menu(Role.ADMIN)) == COMMON + [("🎓 Тренер", "menu:trainer"), ("⚙️ Админ", "menu:admin")]


def test_admin_button_only_for_admin():
    for role in (Role.MEMBER, Role.TRAINER):
        assert "menu:admin" not in [data for _, data in buttons(menu.main_menu(role))]


def test_submenus_end_with_back_and_keep_existing_buttons():
    for submenu in (menu.ADMIN_MENU, menu.TRAINER_MENU):
        assert buttons(submenu)[-1] == ("⬅️ Главное меню", "menu:back")
    admin = buttons(menu.ADMIN_MENU)
    for button in [("🎚 Уровни", "ad:levels"), ("💬 Чаты", "ch:list"), ("🚲 Парк инвентаря", "ad:park"),
                   ("👥 Тренеры", "ad:trainers"), ("🛡 Админы", "ad:admins"),
                   ("🔗 Приглашение тренера", "ad:invite"), ("⚙️ Настройки", "ad:settings")]:
        assert button in admin
    trainer = buttons(menu.TRAINER_MENU)
    for button in [("📋 Мои тренировки", "tr:list"), ("➕ Новая серия", "tr:new:series"),
                   ("➕ Разовая", "tr:new:one"), ("💬 Мои чаты", "ch:list")]:
        assert button in trainer


def test_levels_keyboard_one_button_per_level():
    levels = [Level(id=1, name="Новички"), Level(id=22, name="Про")]
    assert buttons(menu.levels_keyboard(levels)) == [("🔗 Новички", "bind:1"), ("🔗 Про", "bind:22")]
    assert buttons(menu.levels_keyboard([])) == []


def test_cancel_keyboard():
    assert buttons(menu.cancel_keyboard()) == [("✖️ Отмена", "ad:cancel")]


def test_callback_data_fits_telegram_limit():
    levels = [Level(id=10**9, name="х" * 50)]
    every = [menu.main_menu(r) for r in (Role.MEMBER, Role.TRAINER, Role.ADMIN)]
    every += [menu.ADMIN_MENU, menu.TRAINER_MENU, menu.levels_keyboard(levels), menu.cancel_keyboard()]
    for kb in every:
        for _, data in buttons(kb):
            assert len(data.encode()) <= 64


def test_menu_prefixes_do_not_collide_with_existing_ones():
    taken = ("join:", "decline:", "who:", "noop", "tr:", "nf:", "pk:", "ch:", "ad:", "my:", "pf:", "inv:")
    for data in (menu.MENU_SCHEDULE, menu.MENU_MY, menu.MENU_PROFILE, menu.MENU_TRAINER, menu.MENU_ADMIN,
                 menu.MENU_BACK, menu.BIND_PREFIX):
        assert not data.startswith(taken)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/telegram/test_menu.py -v`
Expected: FAIL (`ImportError: cannot import name 'menu'`).

- [ ] **Step 3: Implement**

Перед переносом открыть текущие `ADMIN_MENU` (`handlers/admin.py`) и `TRAINER_MENU` (`handlers/trainer.py`) и скопировать строки как есть, добавив последнюю строку возврата. Ниже их состояние на момент написания плана; при расхождении с кодом приоритет у кода.

`bot/telegram/menu.py`:

```python
"""Клавиатуры меню и константы callback_data. Без обращений к БД и к Telegram API.

(Рендерер постов тренировок — отдельно, в keyboards.py.)"""
from aiogram.types import InlineKeyboardMarkup

from bot.core.permissions import Role
from bot.schedule.models import Level
from bot.telegram.ui import markup

MENU_SCHEDULE = "menu:schedule"
MENU_MY = "menu:my"
MENU_PROFILE = "menu:profile"
MENU_TRAINER = "menu:trainer"
MENU_ADMIN = "menu:admin"
MENU_BACK = "menu:back"
BIND_PREFIX = "bind:"
ADD_LEVEL_CANCEL = "ad:cancel"

TEXT_SCHEDULE = "📅 Расписание"
TEXT_MY = "📋 Мои записи"
TEXT_PROFILE = "👤 Профиль"
TEXT_TRAINER = "🎓 Тренер"
TEXT_ADMIN = "⚙️ Админ"
TEXT_BACK = "⬅️ Главное меню"
TEXT_CANCEL = "✖️ Отмена"

ADMIN_MENU = markup(
    [("👥 Тренеры", "ad:trainers"), ("🛡 Админы", "ad:admins")],
    [("🎚 Уровни", "ad:levels"), ("💬 Чаты", "ch:list")],
    [("🚲 Парк инвентаря", "ad:park")],
    [("🔗 Приглашение тренера", "ad:invite"), ("⚙️ Настройки", "ad:settings")],
    [(TEXT_BACK, MENU_BACK)],
)
TRAINER_MENU = markup(
    [("📋 Мои тренировки", "tr:list")],
    [("➕ Новая серия", "tr:new:series"), ("➕ Разовая", "tr:new:one")],
    [("💬 Мои чаты", "ch:list")],
    [(TEXT_BACK, MENU_BACK)],
)


def main_menu(role: str) -> InlineKeyboardMarkup:
    rows = [[(TEXT_SCHEDULE, MENU_SCHEDULE)], [(TEXT_MY, MENU_MY)], [(TEXT_PROFILE, MENU_PROFILE)]]
    if role in (Role.TRAINER, Role.ADMIN):
        rows.append([(TEXT_TRAINER, MENU_TRAINER)])
    if role == Role.ADMIN:
        rows.append([(TEXT_ADMIN, MENU_ADMIN)])
    return markup(*rows)


def levels_keyboard(levels: list[Level]) -> InlineKeyboardMarkup:
    return markup(*[[(f"🔗 {level.name}", f"{BIND_PREFIX}{level.id}")] for level in levels])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return markup([(TEXT_CANCEL, ADD_LEVEL_CANCEL)])
```

Затем в `handlers/admin.py` удалить определение `ADMIN_MENU` и добавить `from bot.telegram.menu import ADMIN_MENU`; в `handlers/trainer.py` удалить `TRAINER_MENU` и добавить `from bot.telegram.menu import TRAINER_MENU`. Остальной код не трогать (импорт `markup` в обоих файлах остаётся нужным).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/telegram -q && python -c "import bot.main"`
Expected: PASS, импорт без ошибок.

- [ ] **Step 5: Commit**

```bash
git add bot/telegram/menu.py bot/telegram/handlers/admin.py bot/telegram/handlers/trainer.py tests/telegram/test_menu.py
git commit -m "feat(telegram): menu keyboards module, back-to-main-menu buttons"
```

---

### Task 3: Главное меню — `/start`, колбэки `menu:*`, общие функции показа

**Files:**
- Modify: `bot/telegram/handlers/schedule.py`, `bot/telegram/handlers/my.py`, `bot/telegram/handlers/profile.py`, `bot/telegram/handlers/common.py`
- Create: `tests/telegram/fakes.py`, `tests/telegram/test_menu_handlers.py`

**Interfaces:**
- Consumes: из Задачи 2 `main_menu`, `ADMIN_MENU`, `TRAINER_MENU`, `MENU_*`; `ensure_trainer(session, user)` (`handlers/trainer.py`); `require_admin(actor)`; `safe_edit` (`ui.py`); `begin_inventory` (уже импортирован в `common.py`); `RoleService.create_invite(actor, now) -> Invite` (поле `token`) и `redeem_invite(token, user, now)`; фабрики `add_user`, `add_level`, `add_chat`, `add_chat_trainer`, константа `NOW` (`tests/factories.py`); фикстуры `session`, `messenger` (`tests/conftest.py`).
- Produces: `send_schedule(message, session, messenger)`, `send_my(message, session, user)`, `send_profile(message, user)`; в `common.py` — `cmd_start`, `on_menu_back`, `on_menu_schedule`, `on_menu_my`, `on_menu_profile`, `on_menu_trainer`, `on_menu_admin`, `on_menu_stale`; хелперы тестов `FakeMessage`, `FakeCallback`, `fsm()`, `callback_data()` в `tests/telegram/fakes.py`.

- [ ] **Step 1: Write the test helpers and the failing tests**

`tests/telegram/fakes.py`:

```python
"""Фейки для тестов меню: в отличие от test_inventory_handlers хранят chat.title и разметку правок."""
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


class FakeMessage:
    def __init__(self, chat_type="private", chat_id=5, title="Чат", text=None):
        self.chat = SimpleNamespace(type=chat_type, id=chat_id, title=title)
        self.text = text
        self.answers = []  # (text, reply_markup)
        self.edits = []  # (text, reply_markup)

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or FakeMessage()
        self.answers = []  # (text, show_alert)

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def fsm(user_id=5) -> FSMContext:
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))


def callback_data(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]
```

`tests/telegram/test_menu_handlers.py`:

```python
from types import SimpleNamespace

import pytest

from bot.core.errors import PermissionDenied
from bot.telegram.handlers import common
from bot.telegram.states import AdminInput
from bot.users.roles import RoleService
from tests.factories import NOW, add_chat, add_chat_trainer, add_level, add_user
from tests.telegram.fakes import FakeCallback, FakeMessage, callback_data, fsm

MEMBER_MENU = ["menu:schedule", "menu:my", "menu:profile"]
ADMIN_MENU_BUTTONS = MEMBER_MENU + ["menu:trainer", "menu:admin"]


async def test_start_shows_menu_without_slash_commands(session):
    member = await add_user(session, 1, "member", name="Аня")
    msg = FakeMessage()
    await common.cmd_start(msg, member)
    text, markup = msg.answers[0]
    assert "Аня" in text and "/schedule" not in text
    assert callback_data(markup) == MEMBER_MENU


async def test_start_menu_shows_admin_button_only_to_admin(session):
    admin = await add_user(session, 99, "admin")
    msg = FakeMessage()
    await common.cmd_start(msg, admin)
    assert callback_data(msg.answers[0][1]) == ADMIN_MENU_BUTTONS


async def test_back_clears_state_and_edits_message(session):
    admin = await add_user(session, 99, "admin")
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:back")
    await common.on_menu_back(cb, admin, state)
    assert await state.get_state() is None
    assert callback_data(cb.message.edits[0][1]) == ADMIN_MENU_BUTTONS
    assert cb.answers == [(None, False)]


async def test_admin_button_opens_admin_menu_and_clears_state(session):
    admin = await add_user(session, 99, "admin")
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:admin")
    await common.on_menu_admin(cb, admin, state)
    text, markup = cb.message.edits[0]
    assert text == "Меню администратора"
    assert "menu:back" in callback_data(markup) and "ad:park" in callback_data(markup)
    assert await state.get_state() is None


async def test_stale_admin_button_is_refused_for_demoted_admin(session):
    ex_admin = await add_user(session, 99, "member")
    cb = FakeCallback("menu:admin")
    with pytest.raises(PermissionDenied, match="Только для администратора"):
        await common.on_menu_admin(cb, ex_admin, fsm())
    assert cb.message.edits == []


async def test_trainer_button_opens_trainer_menu(session):
    level = await add_level(session)
    chat = await add_chat(session, level)
    trainer = await add_user(session, 10, "trainer")
    await add_chat_trainer(session, chat, 10, is_lead=True)
    cb = FakeCallback("menu:trainer")
    await common.on_menu_trainer(cb, session, trainer, fsm())
    assert cb.message.edits[0][0] == "Меню тренера"
    assert "menu:back" in callback_data(cb.message.edits[0][1])


async def test_trainer_without_chat_gets_alert_text_not_menu(session):
    trainer = await add_user(session, 10, "trainer")
    cb = FakeCallback("menu:trainer")
    with pytest.raises(PermissionDenied, match="не добавлены ни в один чат"):
        await common.on_menu_trainer(cb, session, trainer, fsm())
    assert cb.message.edits == []


async def test_stale_trainer_button_is_refused_for_ex_trainer(session):
    member = await add_user(session, 10, "member")
    cb = FakeCallback("menu:trainer")
    with pytest.raises(PermissionDenied, match="Раздел для тренеров"):
        await common.on_menu_trainer(cb, session, member, fsm())
    assert cb.message.edits == []


async def test_schedule_button_with_no_trainings_says_so(session, messenger):
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:schedule")
    await common.on_menu_schedule(cb, session, messenger, state)
    assert cb.message.answers == [("Ближайших тренировок нет", None)]
    assert cb.answers == [(None, False)]
    assert await state.get_state() is None


async def test_my_button_sends_my_records_and_clears_state(session):
    member = await add_user(session, 1, "member")
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:my")
    await common.on_menu_my(cb, session, member, state)
    assert len(cb.message.answers) == 1
    assert cb.answers == [(None, False)]
    assert await state.get_state() is None


async def test_profile_button_sends_profile_with_edit_buttons_and_clears_state(session):
    member = await add_user(session, 1, "member")
    state = fsm()
    await state.set_state(AdminInput.value)
    cb = FakeCallback("menu:profile")
    await common.on_menu_profile(cb, member, state)
    _, markup = cb.message.answers[0]
    assert "pf:h" in callback_data(markup)
    assert cb.answers == [(None, False)]
    assert await state.get_state() is None


async def test_trainer_invite_deep_link_shows_menu_not_slash_command(session, monkeypatch):
    monkeypatch.setattr(common, "utcnow", lambda: NOW)
    admin = await add_user(session, 99, "admin")
    newbie = await add_user(session, 10, "member")
    invite = await RoleService(session).create_invite(admin, NOW)
    msg = FakeMessage()
    await common.cmd_start_link(msg, SimpleNamespace(args=f"trainer_{invite.token}"), session, newbie, fsm())
    text, markup = msg.answers[0]
    assert "/trainer" not in text
    assert "menu:trainer" in callback_data(markup)


async def test_unknown_menu_button_is_just_answered():
    cb = FakeCallback("menu:old")
    await common.on_menu_stale(cb)
    assert cb.answers == [(None, False)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/telegram/test_menu_handlers.py -v`
Expected: FAIL (`AttributeError: module 'bot.telegram.handlers.common' has no attribute 'on_menu_back'`).

- [ ] **Step 3: Extract the shared senders**

`handlers/schedule.py` — заменить `cmd_schedule`:

```python
async def send_schedule(message: Message, session: AsyncSession, messenger: Messenger) -> None:
    """Общий показ расписания: его зовут и /schedule, и кнопка «📅 Расписание»."""
    trainings = await ScheduleService(session).upcoming(utcnow())
    if not trainings:
        await message.answer("Ближайших тренировок нет")
        return
    publisher = PublishService(session, messenger)
    for training in trainings:
        text, keyboard = await publisher.render(training)
        await message.answer(text, reply_markup=to_markup(keyboard))


@router.message(Command("schedule"), F.chat.type == "private")
async def cmd_schedule(message: Message, session: AsyncSession, messenger: Messenger) -> None:
    await send_schedule(message, session, messenger)
```

`handlers/my.py` — заменить `cmd_my`:

```python
async def send_my(message: Message, session: AsyncSession, user: User) -> None:
    text, keyboard = await _render(session, user)
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("my"))
async def cmd_my(message: Message, session: AsyncSession, user: User) -> None:
    await send_my(message, session, user)
```

`handlers/profile.py` — заменить `cmd_profile`:

```python
async def send_profile(message: Message, user: User) -> None:
    await message.answer(render_profile(user) + HINT, reply_markup=profile_markup())


@router.message(Command("profile"))
async def cmd_profile(message: Message, user: User, state: FSMContext) -> None:
    await state.clear()
    await send_profile(message, user)
```

- [ ] **Step 4: Implement the menu handlers in `handlers/common.py`**

Импорты файла привести к виду (существующий `begin_inventory` остаётся):

```python
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.messenger import Messenger
from bot.core.permissions import require_admin
from bot.telegram.handlers.inventory import begin_inventory
from bot.telegram.handlers.my import send_my
from bot.telegram.handlers.profile import send_profile
from bot.telegram.handlers.schedule import send_schedule
from bot.telegram.handlers.trainer import ensure_trainer
from bot.telegram.menu import (
    ADMIN_MENU, MENU_ADMIN, MENU_BACK, MENU_MY, MENU_PROFILE, MENU_SCHEDULE, MENU_TRAINER, TRAINER_MENU, main_menu,
)
from bot.telegram.ui import safe_edit
from bot.users.models import User
from bot.users.roles import RoleService

router = Router()
MENU_TITLE = "Выберите действие:"
```

В `cmd_start_link` ветка `trainer_` (`redeem_invite` меняет `user.role` на том же объекте, поэтому меню строится по обновлённой роли; ветка `inv_` не меняется):

```python
    if arg.startswith("trainer_"):
        await RoleService(session).redeem_invite(arg[len("trainer_"):], user, utcnow())
        await message.answer(
            "Вы назначены тренером. Кнопка «🎓 Тренер» откроется, когда администратор добавит вас в чат клуба.",
            reply_markup=main_menu(user.role),
        )
        return
```

`cmd_start` и колбэки (`cmd_whoami` без изменений):

```python
@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, user: User) -> None:
    await message.answer(f"Привет, {user.name}! Я бот велоклуба.\n{MENU_TITLE}", reply_markup=main_menu(user.role))


@router.callback_query(F.data == MENU_BACK, F.message.chat.type == "private")
async def on_menu_back(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(cb.message, MENU_TITLE, reply_markup=main_menu(user.role))
    await cb.answer()


@router.callback_query(F.data == MENU_SCHEDULE, F.message.chat.type == "private")
async def on_menu_schedule(cb: CallbackQuery, session: AsyncSession, messenger: Messenger, state: FSMContext) -> None:
    await state.clear()
    await send_schedule(cb.message, session, messenger)
    await cb.answer()


@router.callback_query(F.data == MENU_MY, F.message.chat.type == "private")
async def on_menu_my(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await state.clear()
    await send_my(cb.message, session, user)
    await cb.answer()


@router.callback_query(F.data == MENU_PROFILE, F.message.chat.type == "private")
async def on_menu_profile(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await send_profile(cb.message, user)
    await cb.answer()


@router.callback_query(F.data == MENU_TRAINER, F.message.chat.type == "private")
async def on_menu_trainer(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await ensure_trainer(session, user)  # роль проверяется на сервере: кнопка могла устареть
    await state.clear()
    await safe_edit(cb.message, "Меню тренера", reply_markup=TRAINER_MENU)
    await cb.answer()


@router.callback_query(F.data == MENU_ADMIN, F.message.chat.type == "private")
async def on_menu_admin(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    require_admin(user)
    await state.clear()
    await safe_edit(cb.message, "Меню администратора", reply_markup=ADMIN_MENU)
    await cb.answer()


@router.callback_query(F.data.startswith("menu:"))
async def on_menu_stale(cb: CallbackQuery) -> None:
    await cb.answer()
```

Проверка на всякий случай: циклических импортов нет (`common` уже импортирует `inventory`; `my`, `profile`, `schedule`, `trainer` не импортируют `common`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/telegram tests/inventory -q && python -c "import bot.main"`
Expected: PASS (существующие тесты `/my`, `/profile`, `/schedule` зелёные).

- [ ] **Step 6: Commit**

```bash
git add bot/telegram/handlers/common.py bot/telegram/handlers/schedule.py bot/telegram/handlers/my.py bot/telegram/handlers/profile.py tests/telegram/fakes.py tests/telegram/test_menu_handlers.py
git commit -m "feat(telegram): main menu on /start with schedule, my records, profile, trainer, admin buttons"
```

---

### Task 4: Диалог «Новый уровень» — «Отмена», возврат в меню, `list_levels`

**Files:**
- Modify: `bot/telegram/handlers/admin.py` (`on_levels`, `on_add_level`, `on_admin_input`; новый `on_add_level_cancel` до `on_stale`)
- Test: `tests/telegram/test_admin_levels.py`

**Interfaces:**
- Consumes: `ScheduleService.list_levels/add_level` (Задача 1); `cancel_keyboard`, `ADD_LEVEL_CANCEL`, `ADMIN_MENU` (Задача 2); `FakeMessage/FakeCallback/fsm/callback_data` (Задача 3); существующие `take_form` / `restore_form`, `AdminInput`.
- Produces: `on_add_level_cancel(cb, state, user)` (колбэк `ad:cancel`: `require_admin`, `state.clear()`, правка в «Меню администратора»); `on_add_level` присылает «Название нового уровня:» с `cancel_keyboard()`; создание отвечает «Уровень «X» добавлен» с `ADMIN_MENU` (подсказка про `/bind_level` убирается); `on_levels` берёт уровни из `list_levels()`, кнопка «➕ Новый уровень». Ветка настроек (`setting:<key>`) в `on_admin_input` не меняется.

- [ ] **Step 1: Write the failing tests**

`tests/telegram/test_admin_levels.py`:

```python
import pytest

from bot.core.errors import InvalidAction, PermissionDenied
from bot.schedule.service import ScheduleService
from bot.telegram.handlers import admin
from bot.telegram.states import AdminInput
from tests.factories import add_level, add_user
from tests.telegram.fakes import FakeCallback, FakeMessage, callback_data, fsm


async def test_add_level_button_asks_for_name_with_cancel_button(session):
    adm = await add_user(session, 99, "admin")
    state = fsm()
    cb = FakeCallback("ad:addlvl")
    await admin.on_add_level(cb, state, adm)
    assert await state.get_state() == AdminInput.value.state
    text, markup = cb.message.answers[0]
    assert text == "Название нового уровня:"
    assert callback_data(markup) == ["ad:cancel"]


async def test_name_creates_level_and_returns_admin_menu(session):
    adm = await add_user(session, 99, "admin")
    state = fsm()
    await admin.on_add_level(FakeCallback("ad:addlvl"), state, adm)
    msg = FakeMessage(text="Новички")
    await admin.on_admin_input(msg, state, session, adm)
    assert [lvl.name for lvl in await ScheduleService(session).list_levels()] == ["Новички"]
    text, markup = msg.answers[0]
    assert text == "Уровень «Новички» добавлен"
    assert "menu:back" in callback_data(markup)
    assert await state.get_state() is None


@pytest.mark.parametrize("bad", ["   ", "х" * 51])
async def test_bad_name_keeps_dialog_open_for_retry(session, bad):
    adm = await add_user(session, 99, "admin")
    state = fsm()
    await admin.on_add_level(FakeCallback("ad:addlvl"), state, adm)
    with pytest.raises(InvalidAction):
        await admin.on_admin_input(FakeMessage(text=bad), state, session, adm)
    assert await state.get_state() == AdminInput.value.state
    msg = FakeMessage(text="Новички")
    await admin.on_admin_input(msg, state, session, adm)
    assert msg.answers[0][0] == "Уровень «Новички» добавлен"


async def test_duplicate_name_keeps_dialog_open(session):
    adm = await add_user(session, 99, "admin")
    await add_level(session, "Новички")
    state = fsm()
    await admin.on_add_level(FakeCallback("ad:addlvl"), state, adm)
    with pytest.raises(InvalidAction, match="уже есть"):
        await admin.on_admin_input(FakeMessage(text="Новички"), state, session, adm)
    assert await state.get_state() == AdminInput.value.state


async def test_cancel_drops_state_and_returns_admin_menu(session):
    adm = await add_user(session, 99, "admin")
    state = fsm()
    await admin.on_add_level(FakeCallback("ad:addlvl"), state, adm)
    cb = FakeCallback("ad:cancel")
    await admin.on_add_level_cancel(cb, state, adm)
    assert await state.get_state() is None
    assert await state.get_data() == {}
    assert cb.message.edits[0][0] == "Меню администратора"
    assert cb.answers == [(None, False)]


async def test_cancel_by_non_admin_is_refused(session):
    member = await add_user(session, 1, "member")
    cb = FakeCallback("ad:cancel")
    with pytest.raises(PermissionDenied):
        await admin.on_add_level_cancel(cb, fsm(), member)
    assert cb.message.edits == []


async def test_levels_screen_lists_levels_in_service_order_with_new_level_button(session):
    adm = await add_user(session, 99, "admin")
    await add_level(session, "Б", position=1)
    await add_level(session, "А", position=0)
    cb = FakeCallback("ad:levels")
    await admin.on_levels(cb, session, adm)
    text, markup = cb.message.edits[0]
    assert text == "Уровни:\n• А\n• Б"
    assert callback_data(markup) == ["ad:addlvl", "ad:menu"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/telegram/test_admin_levels.py -v`
Expected: FAIL (нет `on_add_level_cancel`; приглашение без кнопки; подтверждение содержит «/bind_level»).

- [ ] **Step 3: Implement**

В `bot/telegram/handlers/admin.py`:

1. Импорт: `from bot.telegram.menu import ADD_LEVEL_CANCEL, ADMIN_MENU, cancel_keyboard`. Удалить ставшие неиспользуемыми `from sqlalchemy import select` и `from bot.schedule.models import Level` (проверить поиском, что больше нигде в файле не нужны).
2. `on_levels`:

```python
@router.callback_query(F.data == "ad:levels")
async def on_levels(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    names = [level.name for level in await ScheduleService(session).list_levels()]
    await safe_edit(cb.message,
        "Уровни:\n" + ("\n".join(f"• {n}" for n in names) or "пока нет"),
        reply_markup=markup([("➕ Новый уровень", "ad:addlvl")], [("⬅️ Меню", "ad:menu")]),
    )
    await cb.answer()
```

3. В `on_add_level` заменить приглашение: `await cb.message.answer("Название нового уровня:", reply_markup=cancel_keyboard())`.
4. Новый хендлер сразу после `on_add_level` (до `on_stale`):

```python
@router.callback_query(F.data == ADD_LEVEL_CANCEL)
async def on_add_level_cancel(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    await state.clear()
    await safe_edit(cb.message, "Меню администратора", reply_markup=ADMIN_MENU)
    await cb.answer()
```

5. В конце `on_admin_input` ветка `add_level`:

```python
    if purpose == "add_level":
        await message.answer(f"Уровень «{level.name}» добавлен", reply_markup=ADMIN_MENU)
    else:
        await message.answer("Сохранено ✅")
```

6. `cmd_add_level` остаётся (запасной путь). `cmd_bind_level` не трогать, он уходит в Задаче 5.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/telegram tests/schedule -q && python -c "import bot.main"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/telegram/handlers/admin.py tests/telegram/test_admin_levels.py
git commit -m "feat(telegram): add-level dialog with cancel button, return to admin menu"
```

---

### Task 5: Привязка чата к уровню кнопками

**Files:**
- Create: `bot/telegram/handlers/binding.py`, `tests/telegram/test_binding.py`
- Modify: `bot/telegram/handlers/admin.py` (удалить `cmd_bind_level`), `bot/main.py` (импорт и `include_routers`)

**Interfaces:**
- Consumes: `ScheduleService.list_levels/get_level/bind_chat(actor, chat_id, title, level_name)`; `levels_keyboard`, `BIND_PREFIX`; `safe_edit`; `require_admin`; `FakeMessage/FakeCallback/callback_data` (Задача 3).
- Produces: `cmd_bind_level(message, command, session, user)`: `/bind_level <название>` привязывает как раньше («Чат привязан к уровню «X»»); без аргумента: `require_admin`, затем список уровней кнопками или подсказка `NO_LEVELS_HINT`; `on_bind(cb, session, user)`: колбэк `bind:<id>` → `get_level` → `bind_chat` → правка в «Чат привязан к уровню «X»» без клавиатуры.

- [ ] **Step 1: Write the failing tests**

`tests/telegram/test_binding.py`:

```python
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from bot.core.errors import NotFound, PermissionDenied
from bot.schedule.models import Chat
from bot.telegram.handlers import binding
from tests.factories import add_level, add_user
from tests.telegram.fakes import FakeCallback, FakeMessage, callback_data

GROUP = dict(chat_type="supergroup", chat_id=-1001, title="Новички клуба")


def command(args=None):
    return SimpleNamespace(args=args)


async def bound_level_id(session, chat_id=-1001):
    chat = await session.scalar(select(Chat).where(Chat.chat_id == chat_id))
    return None if chat is None else chat.level_id


async def test_bind_level_without_args_lists_levels_as_buttons(session):
    adm = await add_user(session, 99, "admin")
    a = await add_level(session, "Новички", position=0)
    b = await add_level(session, "Про", position=1)
    msg = FakeMessage(**GROUP)
    await binding.cmd_bind_level(msg, command(), session, adm)
    _, markup = msg.answers[0]
    assert callback_data(markup) == [f"bind:{a.id}", f"bind:{b.id}"]
    assert await bound_level_id(session) is None


async def test_bind_level_without_levels_gives_a_hint(session):
    adm = await add_user(session, 99, "admin")
    msg = FakeMessage(**GROUP)
    await binding.cmd_bind_level(msg, command(), session, adm)
    assert msg.answers == [(binding.NO_LEVELS_HINT, None)]


async def test_bind_level_without_args_by_non_admin_is_refused(session):
    member = await add_user(session, 1, "member")
    await add_level(session)
    msg = FakeMessage(**GROUP)
    with pytest.raises(PermissionDenied):
        await binding.cmd_bind_level(msg, command(), session, member)
    assert msg.answers == []


async def test_bind_level_with_name_works_as_before(session):
    adm = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    msg = FakeMessage(**GROUP)
    await binding.cmd_bind_level(msg, command("  Новички "), session, adm)
    assert msg.answers[0][0] == "Чат привязан к уровню «Новички»"
    assert await bound_level_id(session) == level.id


async def test_bind_button_binds_chat_and_edits_the_list(session):
    adm = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    cb = FakeCallback(f"bind:{level.id}", FakeMessage(**GROUP))
    await binding.on_bind(cb, session, adm)
    assert await bound_level_id(session) == level.id
    assert cb.message.edits == [("Чат привязан к уровню «Новички»", None)]
    assert cb.answers == [(None, False)]


async def test_bind_button_pressed_again_just_rebinds(session):
    adm = await add_user(session, 99, "admin")
    level = await add_level(session, "Новички")
    for _ in range(2):
        await binding.on_bind(FakeCallback(f"bind:{level.id}", FakeMessage(**GROUP)), session, adm)
    assert await bound_level_id(session) == level.id


async def test_bind_button_by_non_admin_does_not_bind(session):
    member = await add_user(session, 1, "member")
    level = await add_level(session, "Новички")
    cb = FakeCallback(f"bind:{level.id}", FakeMessage(**GROUP))
    with pytest.raises(PermissionDenied):
        await binding.on_bind(cb, session, member)
    assert await bound_level_id(session) is None
    assert cb.message.edits == []


async def test_bind_button_for_deleted_level_is_not_found(session):
    adm = await add_user(session, 99, "admin")
    cb = FakeCallback("bind:777", FakeMessage(**GROUP))
    with pytest.raises(NotFound, match="Уровень не найден"):
        await binding.on_bind(cb, session, adm)
    assert await bound_level_id(session) is None


async def test_bind_button_with_garbage_id_is_not_found(session):
    adm = await add_user(session, 99, "admin")
    cb = FakeCallback("bind:abc", FakeMessage(**GROUP))
    with pytest.raises(NotFound):
        await binding.on_bind(cb, session, adm)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/telegram/test_binding.py -v`
Expected: FAIL (`ImportError: cannot import name 'binding'`).

- [ ] **Step 3: Implement `bot/telegram/handlers/binding.py`**

Права проверяются в сервисе: `get_level` читает уровень без прав, поэтому `on_bind` вызывает `require_admin` до обращения к БД, а `bind_chat` проверяет админа повторно.

```python
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import NotFound
from bot.core.permissions import require_admin
from bot.schedule.service import ScheduleService
from bot.telegram.menu import BIND_PREFIX, levels_keyboard
from bot.telegram.ui import safe_edit
from bot.users.models import User

router = Router()  # групповые чаты: фильтра «только личка» здесь нет
GROUPS = {"group", "supergroup"}
NO_LEVELS_HINT = "Уровней пока нет. Сначала создайте уровень в личном чате с ботом (⚙️ Админ → 🎚 Уровни)."


@router.message(Command("bind_level"), F.chat.type.in_(GROUPS))
async def cmd_bind_level(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    service = ScheduleService(session)
    if command.args:
        await service.bind_chat(user, message.chat.id, message.chat.title or "", command.args)  # права — в сервисе
        await message.answer(f"Чат привязан к уровню «{command.args.strip()}»")
        return
    require_admin(user)  # список уровней в группе показываем только админу
    levels = await service.list_levels()
    if not levels:
        await message.answer(NO_LEVELS_HINT)
        return
    await message.answer("К какому уровню привязать этот чат?", reply_markup=levels_keyboard(levels))


@router.callback_query(F.data.startswith(BIND_PREFIX), F.message.chat.type.in_(GROUPS))
async def on_bind(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    service = ScheduleService(session)
    try:
        level_id = int(cb.data[len(BIND_PREFIX):])
    except ValueError:
        raise NotFound("Уровень не найден") from None
    level = await service.get_level(level_id)
    await service.bind_chat(user, cb.message.chat.id, cb.message.chat.title or "", level.name)
    await safe_edit(cb.message, f"Чат привязан к уровню «{level.name}»")
    await cb.answer()
```

- [ ] **Step 4: Wire it up and remove the old handler**

В `bot/telegram/handlers/admin.py` удалить `cmd_bind_level` целиком (с декоратором). `CommandObject` и `InvalidAction` остаются: их использует `cmd_add_level`.

В `bot/main.py` добавить `binding` в импорт `from bot.telegram.handlers import (...)` и в `dp.include_routers(...)` после `common.router`:

```python
    dp.include_routers(
        trainer_forms.router, common.router, binding.router, picker.router, park.router, admin.router,
        chats.router, profile.router, my.router, inventory.router, schedule.router, trainer.router,
        participation.router,
    )
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q && python -c "import bot.main"`
Expected: PASS (включая тест конкурентных записей).

- [ ] **Step 6: Commit**

```bash
git add bot/telegram/handlers/binding.py bot/telegram/handlers/admin.py bot/main.py tests/telegram/test_binding.py
git commit -m "feat(telegram): bind chat to a level by choosing from buttons"
```

---

### Task 6: Меню команд Telegram и документация

**Files:**
- Modify: `bot/telegram/menu.py`, `bot/main.py`, `README.md`, `CLAUDE.md`, `ARCHITECTURE.md`
- Test: `tests/telegram/test_menu.py`

**Interfaces:**
- Consumes: `aiogram.types.BotCommand`; `Bot.set_my_commands`.
- Produces: `BOT_COMMANDS: list[tuple[str, str]] = [("start", "Главное меню"), ("whoami", "Мой Telegram ID")]` в `bot/telegram/menu.py`; вызов `set_my_commands` в `main()` до `start_polling`.

- [ ] **Step 1: Write the failing test**

Добавить в `tests/telegram/test_menu.py`:

```python
def test_bot_commands_are_only_start_and_whoami():
    assert [command for command, _ in menu.BOT_COMMANDS] == ["start", "whoami"]
    for _, description in menu.BOT_COMMANDS:
        assert 0 < len(description) <= 256
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/telegram/test_menu.py::test_bot_commands_are_only_start_and_whoami -v`
Expected: FAIL (`AttributeError: ... no attribute 'BOT_COMMANDS'`).

- [ ] **Step 3: Implement**

В `bot/telegram/menu.py` после констант текстов:

```python
# Команды в меню Telegram. /schedule, /my, /profile, /trainer, /admin и /add_level работают, но не рекламируются:
# всё, кроме /add_level, есть в кнопках главного меню.
BOT_COMMANDS = [("start", "Главное меню"), ("whoami", "Мой Telegram ID")]
```

В `bot/main.py`: `from aiogram.types import BotCommand`, `from bot.telegram.menu import BOT_COMMANDS`; после `bot = Bot(cfg.bot_token)`:

```python
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in BOT_COMMANDS])
```

- [ ] **Step 4: Update the docs**

- `README.md`, раздел команд (рядом со строками про `/profile` и `/my`): первой строкой `/start`: «в личке: главное меню на кнопках («📅 Расписание», «📋 Мои записи», «👤 Профиль»; «🎓 Тренер» тренерам и админам; «⚙️ Админ» админам)»; `/whoami` без изменений; `/schedule`, `/my`, `/profile`, `/trainer`, `/admin` — «работают, но в меню команд не показываются: то же самое есть в кнопках»; `/bind_level` — «администратор, в группе: список уровней кнопками или `/bind_level <название>`»; создание уровня: «⚙️ Админ → 🎚 Уровни → ➕ Новый уровень (диалог, «✖️ Отмена»)», `/add_level <название>` оставить одной строкой как запасной путь. В «Ручной проверке» шаг привязки заменить на `/bind_level` без аргумента с выбором кнопкой.
- `CLAUDE.md`: в раздел «Архитектура» одним предложением: клавиатуры меню и `callback_data` (`menu:*`, `bind:*`, `ad:cancel`) живут в `bot/telegram/menu.py`, список команд Telegram задаёт `BOT_COMMANDS` (только `/start` и `/whoami`).
- `ARCHITECTURE.md`: найти раздел про Telegram-слой (§4.3) и описание `/profile` и «Мои записи» (~стр. 133-152); добавить `menu.py`, колбэки `menu:*`, `binding.py` и общие функции `send_schedule` / `send_my` / `send_profile`; обновить упоминания команд там, где они теперь доступны кнопками.

- [ ] **Step 5: Run the full suite and the import check**

Run: `python -m pytest -q && python -c "import bot.main"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/main.py bot/telegram/menu.py tests/telegram/test_menu.py README.md CLAUDE.md ARCHITECTURE.md
git commit -m "feat(telegram): trim bot command list to /start and /whoami, document the menu"
```

---

### Task 7: Ручная проверка

(Нужны реальный токен и два аккаунта Telegram; выполняет человек.)

- [ ] **Step 1: Подготовка.** `alembic upgrade head`, `python -m scripts.seed_demo`, `python -m bot.main`; ваш ID в `ADMIN_IDS`; тестовая группа с ботом и второй аккаунт-участник.
- [ ] **Step 2: Меню команд.** В списке команд Telegram у бота ровно `/start` и `/whoami`.
- [ ] **Step 3: Главное меню.** `/start` у админа: «📅 Расписание», «📋 Мои записи», «👤 Профиль», «🎓 Тренер», «⚙️ Админ»; у участника: первые три. «📅 Расписание» присылает тренировки; «📋 Мои записи» и «👤 Профиль» открывают то же, что `/my` и `/profile`: кнопки «🗑» отмены и редактирования профиля работают.
- [ ] **Step 4: Админ-меню.** «⚙️ Админ» открывает меню со всеми прежними кнопками, включая «🚲 Парк инвентаря»; «⬅️ Главное меню» возвращает назад. У разжалованного админа устаревшая кнопка даёт алерт «Только для администратора». `/admin` и `/trainer` как команды работают.
- [ ] **Step 5: Новый уровень.** «🎚 Уровни» → «➕ Новый уровень» → пробелы: ошибка, диалог открыт; 51 символ: ошибка; «Новички»: «Уровень «Новички» добавлен» и админ-меню; повторное «Новички»: «уже есть». «➕ Новый уровень» → «✖️ Отмена»: админ-меню, следующий текст не становится уровнем. Посреди ввода нажать «📋 Мои записи»: ввод сброшен.
- [ ] **Step 6: Привязка.** В группе `/bind_level` от админа: кнопки «🔗 Новички»; нажатие превращает сообщение в «Чат привязан к уровню «Новички»». От не-админа: алерт, чат не привязан. `/bind_level Новички` работает как раньше. Без уровней: подсказка создать уровень в личке.
- [ ] **Step 7: Приглашение и инвентарь.** Ссылка-приглашение тренера: после принятия в ответе нет `/trainer`, есть меню с «🎓 Тренер». Кнопка «🚲 Инвентарь» из группы (deep link `inv_<id>`) по-прежнему открывает заказ.

---

## Self-Review

**Покрытие спецификации:**
- Главное меню по ролям: расписание, «Мои записи», «Профиль» всем, «Тренер» тренерам и админам, «Админ» админам → Задачи 2, 3.
- Вход в меню тренера и админа кнопками, возврат «⬅️ Главное меню» с `menu:back` и сбросом FSM → Задачи 2, 3.
- Диалог «➕ Новый уровень» с «✖️ Отмена» (`ad:cancel`), возврат в админ-меню → Задача 4.
- `/bind_level` без аргумента → список «🔗 *Название*», с аргументом как раньше, подсказка без уровней → Задача 5.
- Меню команд `/start`, `/whoami`; остальные команды работают и не рекламируются → Задача 6.
- Приветствие `/start` и ответ на приглашение тренера без ссылок на команды → Задача 3.
- Deep link `inv_<id>` не меняется → Задача 3 (ветка не тронута), Задача 7 (ручная проверка).
- `list_levels()` (порядок `position`, затем имя), привязка по id через `bind_chat(name)` без смены сигнатуры → Задачи 1, 5.
- Права на сервере, алерты устаревших кнопок, «Уровень не найден», валидация названия, `callback_data` ≤ 64 байт → Задачи 1-5.
- `MemoryStorage` и отсутствие миграций — уже так.
- Тесты из спеки: `list_levels` (порядок, пусто), привязка по id → Задача 1; `test_menu.py` (кнопки по ролям, админ-кнопка, тексты, «Главное меню» последней, длина) → Задача 2; устаревшие кнопки → `PermissionDenied` → Задача 3.
- Расхождение: спека говорит «хендлеры не тестируются»; план их тестирует, как принято в репозитории (см. «Отступления»).

**Сканирование на заглушки:** TBD/TODO нет; в шагах с кодом код приведён. Единственные места «при расхождении смотреть код» — перенос существующих меню (Задача 2) и правки документации (Задача 6).

**Согласованность типов:** `main_menu(role: str)`, `ADMIN_MENU`/`TRAINER_MENU`, `levels_keyboard`, `cancel_keyboard`, `MENU_*`, `BIND_PREFIX`, `ADD_LEVEL_CANCEL`, `BOT_COMMANDS` (Задачи 2, 6) используются под теми же именами в Задачах 3-6; `list_levels`/`get_level` (Задача 1) — в Задачах 4-5; `send_schedule`/`send_my`/`send_profile` (Задача 3) вызываются только из `common.py`; `FakeMessage`/`FakeCallback`/`fsm`/`callback_data` (Задача 3) — в Задачах 4-5; `FakeCallback.answer(text=None, show_alert=False)` совпадает с вызовами `cb.answer()` в хендлерах. `create_invite(actor, now)` вызывается с двумя аргументами.

**Review Focus:** п.1 — `test_stale_admin_button_is_refused_*`, `test_stale_trainer_button_is_refused_*`, `test_trainer_without_chat_gets_alert_text_not_menu` (Задача 3); п.2 — `test_add_level_rejects_too_long_duplicate_and_blank_names` (Задача 1), `test_bad_name_keeps_dialog_open_for_retry`, `test_duplicate_name_keeps_dialog_open` (Задача 4); п.3 — тесты `test_binding.py` (Задача 5); п.4 — `test_back_clears_state_*`, `test_my_button_*`, `test_profile_button_*`, `test_cancel_drops_state_*`; п.5 — `test_submenus_end_with_back_and_keep_existing_buttons` (Задача 2), `test_admin_button_opens_admin_menu_and_clears_state` (Задача 3). `/start` в группе не обрабатывается: фильтр `F.chat.type == "private"` на `cmd_start` уже есть и не менялся.
