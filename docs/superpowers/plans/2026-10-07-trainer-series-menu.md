# Серии в меню тренера Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать тренеру и админу в меню тренера пункт «🔁 Мои серии» со списком активных серий и кнопкой «🏁 Завершить серию» (отмена всех будущих тренировок серии и остановка генерации).

**Architecture:** `ScheduleService` получает `active_series`, `future_training_count` и `end_series` (общий с `cancel` условный `UPDATE` выносится в `_cancel_rows`). Usecase `end_series` в `bot/usecases.py` добавляет отмену заказов инвентаря и объявления. Telegram-слой: чистые построители подписи и сводки серии (`bot/schedule/forms.py`), кнопка в `TRAINER_MENU`, четыре обработчика в `bot/telegram/handlers/trainer.py` (до catch-all `tr:`).

**Tech Stack:** Python 3.11+, aiogram 3, SQLAlchemy 2 async + aiosqlite, pytest (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-10-07-trainer-series-menu-design.md`

## Preconditions

В рабочем дереве есть чужие незакоммиченные правки в `ARCHITECTURE.md`, `bot/telegram/menu.py`, `bot/telegram/handlers/{my,profile,schedule}.py`, `tests/telegram/test_menu_handlers.py`. Задачи 3 и 5 меняют `menu.py` и `ARCHITECTURE.md`. До старта попросите владельца закоммитить или спрятать (`git stash`) эти правки. Если они остаются, коммитьте эти два файла через `git add -p` и берите в коммит только свои hunks. Остальные файлы коммитьте по явным путям, без `git add -A` и `git add .`.

## Global Constraints

- aiogram импортируется только в `bot/telegram/`; домен не знает про Telegram.
- Права проверяются в сервисе (`ScheduleService.end_series`), а не берутся из `callback_data`.
- Обработчики Telegram вызывают usecase (`bot/usecases.py`), а не сервисы, для действий, которые обновляют посты или уведомляют людей.
- Каждый роутер заканчивается catch-all `tr:`: новые обработчики стоят выше `on_stale` в `bot/telegram/handlers/trainer.py`.
- Время хранится в UTC; «сегодня» считается в `club_timezone`.
- Отмена тренировки гасит её pending-задачи (`cancel_for_training`).
- Логирование: `log = logging.getLogger(__name__)`, без `basicConfig`.
- `callback_data` не длиннее 64 байт.
- Тест конкурентности из `tests/participation/` должен оставаться зелёным.
- Комментарии и тексты для пользователя на русском, в стиле окружающего кода.
- Коммиты заканчиваются строкой `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Команды: `python -m pytest -q` (на Windows при активном venv — `.venv\Scripts\python -m pytest -q`).

## Review Focus

Случаи, которые спека подразумевает, а базовые тесты задач могли бы пропустить (каждому есть тест в указанной задаче):

1. Серия, которая ещё не началась: `end_date` (вчера) раньше `start_date`, ничего не падает, `generate()` не создаёт тренировок (Задача 1, `test_end_series_before_first_training`).
2. Граница суток: в UTC ещё вчера, а в `club_timezone` уже завтра; `end_date` считается по местной дате (Задача 1, `test_end_series_uses_club_timezone_for_today`).
3. Опубликованные тренировки с записавшимся участником и заказом инвентаря: уведомление приходит один раз, заказы отменены, посты помечены «Отменена» (Задача 2).
4. Устаревшая кнопка: экран уже завершённой серии не показывает «Завершить», а повторное подтверждение даёт `InvalidAction`, а не вторую отмену (Задачи 1, 4).
5. Длинное название места и «чужая» серия: подпись кнопки не длиннее 64 символов; чужой тренер получает `PermissionDenied` и ничего не меняется (Задачи 1, 3, 4).

---

### Task 1: ScheduleService — `active_series`, `future_training_count`, `end_series`

**Files:**
- Modify: `bot/schedule/service.py` (импорт `func`; `is_active_series`; `cancel` → `_cancel_rows`; три новых метода)
- Create: `tests/schedule/test_end_series.py`

**Interfaces:**
- Consumes: существующие `ScheduleService._tz()`, `cancel_for_training`, `Training`, `TrainingSeries`, `CancelReason`.
- Produces:
  - `is_active_series(series: TrainingSeries, today: date) -> bool` (модульная функция в `bot/schedule/service.py`);
  - `ScheduleService.active_series(actor: User, now: datetime) -> list[TrainingSeries]`;
  - `ScheduleService.future_training_count(series_id: int, now: datetime) -> int`;
  - `ScheduleService.end_series(actor: User, series_id: int, now: datetime) -> list[Training]`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/schedule/test_end_series.py`:

```python
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.jobs.models import ScheduledJob
from bot.schedule.models import CancelReason, Training, TrainingSeries, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput
from tests.factories import add_level, add_user

T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)  # вторник, 13:00 в Москве


async def make_series(session, *, weekdays=(2, 4), start=date(2026, 10, 6), trainer_id=None, now=T0):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    series = await ScheduleService(session).create_series(
        admin,
        SeriesInput(
            kind="intervals", place="Парк", level_ids=[level.id], start_date=start,
            start_time=time(19, 0), weekdays=set(weekdays), trainer_id=trainer_id,
        ),
        now,
    )
    return admin, series


async def all_trainings(session):
    return list(await session.scalars(select(Training).order_by(Training.starts_at)))


async def test_end_series_cancels_future_trainings_and_stops_generation(session):
    admin, series = await make_series(session)
    svc = ScheduleService(session)
    cancelled = await svc.end_series(admin, series.id, T0)
    assert len(cancelled) == 8  # вт и чт на 4 недели вперёд
    session.expire_all()
    rows = await all_trainings(session)
    assert {t.status for t in rows} == {TrainingStatus.CANCELLED}
    assert {t.cancel_reason for t in rows} == {CancelReason.USER}
    assert (await session.get(TrainingSeries, series.id)).end_date == date(2026, 10, 5)
    assert await svc.generate(T0 + timedelta(days=1)) == []
    assert len(await all_trainings(session)) == 8


async def test_end_series_removes_pending_jobs(session):
    admin, series = await make_series(session)
    await ScheduleService(session).end_series(admin, series.id, T0)
    pending = list(await session.scalars(select(ScheduledJob).where(ScheduledJob.done_at.is_(None))))
    assert pending == []


async def test_end_series_keeps_past_trainings(session):
    admin, series = await make_series(session)
    now = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)  # среда: 6, 8 и 13 октября уже прошли
    cancelled = await ScheduleService(session).end_series(admin, series.id, now)
    assert len(cancelled) == 5  # 15, 20, 22, 27, 29 октября
    session.expire_all()
    rows = await all_trainings(session)
    past = [t for t in rows if t.starts_at <= now]
    assert len(past) == 3 and {t.status for t in past} == {TrainingStatus.SCHEDULED}
    assert (await session.get(TrainingSeries, series.id)).end_date == date(2026, 10, 13)


async def test_end_series_by_owner_but_not_by_other_trainer(session):
    await add_user(session, 10, "trainer")
    other = await add_user(session, 11, "trainer")
    admin, series = await make_series(session, trainer_id=10)
    svc = ScheduleService(session)
    with pytest.raises(PermissionDenied):
        await svc.end_series(other, series.id, T0)
    session.expire_all()
    assert (await session.get(TrainingSeries, series.id)).end_date is None
    assert {t.status for t in await all_trainings(session)} == {TrainingStatus.SCHEDULED}
    owner = await session.get(type(other), 10)
    assert len(await svc.end_series(owner, series.id, T0)) == 8


async def test_end_series_twice_and_unknown_and_one_off(session):
    admin, series = await make_series(session)
    svc = ScheduleService(session)
    await svc.end_series(admin, series.id, T0)
    with pytest.raises(InvalidAction, match="уже завершена"):
        await svc.end_series(admin, series.id, T0)
    with pytest.raises(NotFound):
        await svc.end_series(admin, 12345, T0)
    level = await add_level(session, "Другой", 1)
    one_off = await svc.create_series(
        admin,
        SeriesInput(kind="intervals", place="Парк", level_ids=[level.id],
                    start_date=date(2026, 10, 20), start_time=time(19, 0)),
        T0,
    )
    with pytest.raises(InvalidAction, match="разовая"):
        await svc.end_series(admin, one_off.id, T0)


async def test_end_series_without_future_trainings_still_ends_it(session):
    admin, series = await make_series(session)
    later = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)  # генератор до сих пор не заходил в ноябрь
    cancelled = await ScheduleService(session).end_series(admin, series.id, later)
    assert cancelled == []
    session.expire_all()
    assert (await session.get(TrainingSeries, series.id)).end_date == date(2026, 11, 19)


async def test_end_series_before_first_training(session):
    admin, series = await make_series(session, weekdays=(2,), start=date(2026, 10, 27))
    svc = ScheduleService(session)
    cancelled = await svc.end_series(admin, series.id, T0)
    assert len(cancelled) == 1  # единственная сгенерированная: 27 октября
    session.expire_all()
    got = await session.get(TrainingSeries, series.id)
    assert got.end_date == date(2026, 10, 5) and got.end_date < got.start_date
    assert await svc.generate(T0 + timedelta(days=2)) == []


async def test_end_series_uses_club_timezone_for_today(session):
    admin, series = await make_series(session)
    late = datetime(2026, 10, 6, 22, 30, tzinfo=timezone.utc)  # в Москве уже 7 октября, 01:30
    await ScheduleService(session).end_series(admin, series.id, late)
    session.expire_all()
    assert (await session.get(TrainingSeries, series.id)).end_date == date(2026, 10, 6)


async def test_active_series_scope_and_filters(session):
    await add_user(session, 10, "trainer")
    mine = await add_user(session, 11, "trainer")
    admin, series_a = await make_series(session, trainer_id=10)
    svc = ScheduleService(session)
    level = await add_level(session, "Другой", 1)
    series_b = await svc.create_series(
        admin,
        SeriesInput(kind="intervals", place="Стадион", level_ids=[level.id], start_date=date(2026, 10, 6),
                    start_time=time(19, 0), weekdays={3}, trainer_id=11),
        T0,
    )
    await svc.create_series(  # разовая в список не попадает
        admin,
        SeriesInput(kind="intervals", place="Разовая", level_ids=[level.id],
                    start_date=date(2026, 10, 20), start_time=time(19, 0), trainer_id=11),
        T0,
    )
    assert [s.id for s in await svc.active_series(admin, T0)] == [series_a.id, series_b.id]
    assert [s.id for s in await svc.active_series(mine, T0)] == [series_b.id]
    member = await add_user(session, 1, "member")
    assert await svc.active_series(member, T0) == []
    await svc.end_series(admin, series_a.id, T0)
    assert [s.id for s in await svc.active_series(admin, T0)] == [series_b.id]


async def test_future_training_count(session):
    admin, series = await make_series(session)
    svc = ScheduleService(session)
    assert await svc.future_training_count(series.id, T0) == 8
    assert await svc.future_training_count(series.id, datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)) == 5
    await svc.end_series(admin, series.id, T0)
    assert await svc.future_training_count(series.id, T0) == 0
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/schedule/test_end_series.py -q`
Expected: FAIL — `AttributeError: 'ScheduleService' object has no attribute 'end_series'` (и `active_series`, `future_training_count`).

- [ ] **Step 3: Реализовать**

В `bot/schedule/service.py`:

1. Импорт: `from sqlalchemy import func, or_, select, update`.

2. После `series_to_input` (перед `class ScheduleService`) добавить:

```python
def is_active_series(series: TrainingSeries, today: date) -> bool:
    """Активная серия — повторяющаяся и не закончившаяся к местной сегодняшней дате."""
    return bool(series.weekdays) and (series.end_date is None or series.end_date >= today)
```

3. В `cancel` заменить цикл мутаций вызовом `_cancel_rows`. Метод целиком станет таким:

```python
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
        await self._cancel_rows(trainings, now)
        await self.session.commit()
        return trainings
```

4. После `cancel` добавить:

```python
    async def _cancel_rows(self, trainings: list[Training], now: datetime) -> None:
        """Переводит тренировки в CANCELLED (причина USER) и гасит их задачи. Без commit: его делает вызывающий."""
        for training in trainings:
            # conditional UPDATE: of two concurrent cancels (double tap) only one flips the status
            flipped = await self.session.execute(
                update(Training)
                .where(Training.id == training.id, Training.status == TrainingStatus.SCHEDULED)
                .values(status=TrainingStatus.CANCELLED, cancel_reason=CancelReason.USER)
            )
            if flipped.rowcount != 1:
                await self.session.rollback()
                raise InvalidAction("Тренировка уже отменена или прошла")
            training.status, training.cancel_reason = TrainingStatus.CANCELLED, CancelReason.USER  # in-memory copy
            await cancel_for_training(self.session, training.id, now)
```

5. После `update_series` (перед `# ---- internals ----`) добавить:

```python
    async def active_series(self, actor: User, now: datetime) -> list[TrainingSeries]:
        """Активные повторяющиеся серии: свои у тренера, все у админа."""
        if actor.role not in (Role.ADMIN, Role.TRAINER):
            return []
        today = now.astimezone(await self._tz()).date()
        query = (
            select(TrainingSeries)
            .where(
                TrainingSeries.weekdays != "",
                or_(TrainingSeries.end_date.is_(None), TrainingSeries.end_date >= today),
            )
            .order_by(TrainingSeries.id)
        )
        if actor.role != Role.ADMIN:
            query = query.where(TrainingSeries.trainer_id == actor.telegram_id)
        return list(await self.session.scalars(query))

    async def future_training_count(self, series_id: int, now: datetime) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(Training)
            .where(
                Training.series_id == series_id,
                Training.status == TrainingStatus.SCHEDULED,
                Training.starts_at > now,
            )
        )
        return count or 0

    async def end_series(self, actor: User, series_id: int, now: datetime) -> list[Training]:
        """Завершает серию: отменяет её будущие тренировки и закрывает генерацию. Проверки идут до мутаций."""
        series = await self.session.get(TrainingSeries, series_id)
        if series is None:
            raise NotFound("Серия не найдена")
        if actor.role != Role.ADMIN and series.trainer_id != actor.telegram_id:
            raise PermissionDenied("Серию может завершить только её тренер или администратор")
        if not series.weekdays:
            raise InvalidAction("Это разовая тренировка: отмените её в разделе «Мои тренировки»")
        today = now.astimezone(await self._tz()).date()
        if not is_active_series(series, today):
            raise InvalidAction("Серия уже завершена")
        # вчера: generate() берёт серии с end_date >= today, поэтому серия сразу выпадает и из генерации, и из списка
        series.end_date = today - timedelta(days=1)
        future = list(
            await self.session.scalars(
                select(Training)
                .where(
                    Training.series_id == series.id,
                    Training.status == TrainingStatus.SCHEDULED,
                    Training.starts_at > now,
                )
                .order_by(Training.starts_at)
            )
        )
        await self._cancel_rows(future, now)
        await self.session.commit()
        return future
```

- [ ] **Step 4: Запустить тесты серии, затем все тесты расписания**

Run: `python -m pytest tests/schedule -q`
Expected: PASS (новые 9 тестов; существующие тесты `cancel` не сломаны рефакторингом).

- [ ] **Step 5: Commit**

```bash
git add bot/schedule/service.py tests/schedule/test_end_series.py
git commit -m "feat(schedule): end_series, active_series and future_training_count" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Usecase `end_series`

**Files:**
- Modify: `bot/usecases.py` (после `cancel_trainings`)
- Create: `tests/test_usecases_series.py`

**Interfaces:**
- Consumes: `ScheduleService.end_series` (Задача 1), `InventoryService.cancel_for_training`, `PublishService.announce_cancelled`.
- Produces: `end_series(session: AsyncSession, messenger: Messenger, actor: User, series_id: int, now: datetime) -> list[Training]` в `bot/usecases.py`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_usecases_series.py`:

```python
from datetime import date, time

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, PermissionDenied
from bot.inventory.models import InventoryOrder, OrderStatus, ensure_cleat_compat
from bot.inventory.service import InventoryService
from bot.publishing.service import PublishService
from bot.schedule.models import Training, TrainingSeries, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput
from bot.usecases import end_series, join_training
from tests.factories import NOW, add_chat, add_item, add_level, add_user


async def published_series(session, messenger):
    """Серия по вторникам: 4 тренировки (20.10, 27.10, 3.11, 10.11); первая опубликована, в ней идёт участник 1."""
    admin = await add_user(session, 99, "admin")
    await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    series = await ScheduleService(session).create_series(
        admin,
        SeriesInput(
            kind="intervals", place="Парк", level_ids=[level.id], start_date=date(2026, 10, 20),
            start_time=time(19, 0), weekdays={2}, trainer_id=10,
        ),
        NOW,
    )
    first = (await session.scalars(select(Training).order_by(Training.starts_at))).first()
    await PublishService(session, messenger, send_delay=0).publish(first.id, NOW)
    await ensure_cleat_compat(session)
    await add_user(session, 1)
    await join_training(session, messenger, first.id, 1, NOW)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    await InventoryService(session).confirm(first.id, 1, [bike.id], NOW)
    return admin, series, first


async def test_end_series_announces_cancels_orders_and_closes_the_series(session, messenger):
    admin, series, first = await published_series(session, messenger)
    cancelled = await end_series(session, messenger, admin, series.id, NOW)
    assert len(cancelled) == 4
    session.expire_all()
    assert {t.status for t in await session.scalars(select(Training))} == {TrainingStatus.CANCELLED}
    assert (await session.get(TrainingSeries, series.id)).end_date == date(2026, 10, 18)
    active = list(await session.scalars(select(InventoryOrder).where(InventoryOrder.status == OrderStatus.ACTIVE)))
    assert active == []
    assert "Отменена" in messenger.edits[-1].text
    assert [uid for uid, text in messenger.notifications if "отменена" in text] == [1]  # участнику одно уведомление


async def test_end_series_by_stranger_changes_nothing(session, messenger):
    _, series, first = await published_series(session, messenger)
    stranger = await add_user(session, 2, "member")
    edits = len(messenger.edits)
    with pytest.raises(PermissionDenied):
        await end_series(session, messenger, stranger, series.id, NOW)
    session.expire_all()
    assert (await session.get(Training, first.id)).status == TrainingStatus.SCHEDULED
    assert (await session.get(TrainingSeries, series.id)).end_date is None
    assert len(messenger.edits) == edits


async def test_end_series_twice_announces_once(session, messenger):
    admin, series, _ = await published_series(session, messenger)
    await end_series(session, messenger, admin, series.id, NOW)
    notes = len(messenger.notifications)
    with pytest.raises(InvalidAction):
        await end_series(session, messenger, admin, series.id, NOW)
    assert len(messenger.notifications) == notes
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_usecases_series.py -q`
Expected: FAIL — `ImportError: cannot import name 'end_series' from 'bot.usecases'`.

- [ ] **Step 3: Реализовать**

В `bot/usecases.py` после `cancel_trainings` (перед `update_series`) добавить:

```python
async def end_series(
    session: AsyncSession, messenger: Messenger, actor: User, series_id: int, now: datetime
) -> list[Training]:
    cancelled = await ScheduleService(session).end_series(actor, series_id, now)
    log.info("series %s ended by user=%s, cancelled trainings %s", series_id, actor.telegram_id, [t.id for t in cancelled])
    publisher = PublishService(session, messenger)
    inventory = InventoryService(session)
    for training in cancelled:
        await inventory.cancel_for_training(training.id)
        await publisher.announce_cancelled(training.id)
    return cancelled
```

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_usecases_series.py tests/test_usecases.py tests/test_usecases_inventory.py -q`
Expected: PASS. Если `InventoryService.confirm` в Шаге 1 отклоняет заказ из-за дедлайна заказов, сдвиньте тест на более раннюю дату начала первой тренировки, а не меняйте сервис.

- [ ] **Step 5: Commit**

```bash
git add bot/usecases.py tests/test_usecases_series.py
git commit -m "feat(usecases): end_series cancels future trainings, orders and announces" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Чистые построители — подпись, сводка и кнопка меню

**Files:**
- Modify: `bot/schedule/forms.py` (после `summarize_series`)
- Modify: `bot/telegram/menu.py:38-43` (`TRAINER_MENU`)
- Test: `tests/schedule/test_forms.py` (добавить в конец), `tests/telegram/test_menu.py` (расширить два теста)

**Interfaces:**
- Consumes: `SeriesInput`, `WEEKDAY_SHORT`, `summarize_series` из `bot/schedule/forms.py`.
- Produces:
  - `series_title(inp: SeriesInput) -> str` — подпись кнопки, например `Вт/Чт 19:00 · Парк`, не длиннее 64 символов;
  - `series_details(inp: SeriesInput, level_names: list[str]) -> str` — `summarize_series` плюс строка `Начало: дд.мм.гггг`;
  - кнопка `("🔁 Мои серии", "tr:slist")` в `TRAINER_MENU` после «📋 Мои тренировки».

- [ ] **Step 1: Написать падающие тесты**

В начале `tests/schedule/test_forms.py` добавить `from dataclasses import replace` первой строкой и расширить импорт из `bot.schedule.forms` именами `series_details, series_title`. В конец файла:

```python
def test_series_title_for_weekly_and_long_place():
    inp = SeriesInput(
        kind="intervals", place="Парк", level_ids=[1], start_date=date(2026, 10, 6), start_time=time(19, 0),
        weekdays={4, 2},
    )
    assert series_title(inp) == "Вт/Чт 19:00 · Парк"
    long = replace(inp, place="х" * 100)
    assert len(series_title(long)) == 64


def test_series_details_adds_start_date():
    inp = SeriesInput(
        kind="intervals", place="Парк", level_ids=[1], start_date=date(2026, 10, 6), start_time=time(19, 0),
        weekdays={2}, end_date=date(2026, 12, 31),
    )
    text = series_details(inp, ["Новички"])
    assert "Уровни: Новички" in text and "до 31.12.2026" in text
    assert text.endswith("Начало: 06.10.2026")
```

В `tests/telegram/test_menu.py` в `test_submenus_end_with_back_and_keep_existing_buttons` добавить в список кнопок тренера `("🔁 Мои серии", "tr:slist")`, а после списка — проверку порядка:

```python
    data = [d for _, d in trainer]
    assert data.index("tr:list") + 1 == data.index("tr:slist")
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/schedule/test_forms.py tests/telegram/test_menu.py -q`
Expected: FAIL — `AttributeError: module 'bot.schedule.forms' has no attribute 'series_title'` и отсутствие кнопки в `TRAINER_MENU`.

- [ ] **Step 3: Реализовать**

В `bot/schedule/forms.py` после `summarize_series` добавить:

```python
def series_title(inp: SeriesInput) -> str:
    """Подпись кнопки серии: «Вт/Чт 19:00 · Парк» (до 64 символов)."""
    when = "/".join(WEEKDAY_SHORT[d - 1].capitalize() for d in sorted(inp.weekdays)) or f"{inp.start_date:%d.%m}"
    return f"{when} {inp.start_time:%H:%M} · {inp.place}"[:64]


def series_details(inp: SeriesInput, level_names: list[str]) -> str:
    return summarize_series(inp, level_names) + f"\nНачало: {inp.start_date:%d.%m.%Y}"
```

В `bot/telegram/menu.py` в `TRAINER_MENU` после строки `[("📋 Мои тренировки", "tr:list")],` добавить `[("🔁 Мои серии", "tr:slist")],`.

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/schedule/test_forms.py tests/telegram/test_menu.py tests/telegram/test_menu_handlers.py -q`
Expected: PASS (включая `test_callback_data_fits_telegram_limit` и `test_menu_prefixes_do_not_collide_with_existing_ones`: префикс `tr:` уже занят существующим).

- [ ] **Step 5: Commit**

Обратите внимание на раздел Preconditions: `menu.py` может содержать чужие правки, тогда коммитьте его через `git add -p`.

```bash
git add bot/schedule/forms.py tests/schedule/test_forms.py tests/telegram/test_menu.py bot/telegram/menu.py
git commit -m "feat(telegram): «Мои серии» in trainer menu, series title and details builders" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Обработчики — список, экран серии, подтверждение и завершение

**Files:**
- Modify: `bot/telegram/handlers/trainer.py` (импорты; `on_series_menu`; новые `on_series_list`, `on_series_end_ask`, `on_series_end_do`; всё выше `on_stale`)
- Create: `tests/telegram/test_series_handlers.py`

**Interfaces:**
- Consumes: `ScheduleService.active_series`, `ScheduleService.future_training_count`, `is_active_series` (Задача 1); `end_series` из `bot.usecases` (Задача 2); `series_title`, `series_details` (Задача 3).
- Produces: обработчики `on_series_list` (`tr:slist`), `on_series_menu` (`tr:series:<id>`, изменён), `on_series_end_ask` (`tr:send:<id>`), `on_series_end_do` (`tr:sendok:<id>`).

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/telegram/test_series_handlers.py`:

```python
from datetime import date, time

import pytest

from bot.core.errors import InvalidAction, PermissionDenied
from bot.schedule.models import TrainingSeries
from bot.schedule.service import ScheduleService, SeriesInput
from bot.telegram.handlers import trainer
from tests.factories import NOW, add_chat, add_chat_trainer, add_level, add_user
from tests.telegram.fakes import FakeCallback, callback_data


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(trainer, "utcnow", lambda: NOW)


async def setup_series(session):
    """Серии по вторникам: a у тренера 10, b у тренера 11 и одна разовая у тренера 10."""
    admin = await add_user(session, 99, "admin")
    mine = await add_user(session, 10, "trainer")
    other = await add_user(session, 11, "trainer")
    level = await add_level(session)
    chat = await add_chat(session, level, -1001)
    await add_chat_trainer(session, chat, 10)
    await add_chat_trainer(session, chat, 11)
    svc = ScheduleService(session)

    def data(place, trainer_id, weekdays):
        return SeriesInput(
            kind="intervals", place=place, level_ids=[level.id], start_date=date(2026, 10, 20),
            start_time=time(19, 0), weekdays=weekdays, trainer_id=trainer_id,
        )

    a = await svc.create_series(admin, data("Парк", 10, {2}), NOW)
    b = await svc.create_series(admin, data("Стадион", 11, {2}), NOW)
    await svc.create_series(admin, data("Разовая", 10, set()), NOW)
    return admin, mine, other, a, b


async def test_list_shows_own_active_series_only(session):
    _, mine, _, a, _ = await setup_series(session)
    cb = FakeCallback("tr:slist")
    await trainer.on_series_list(cb, session, mine)
    text, markup = cb.message.edits[-1]
    assert text == "Мои серии"
    assert callback_data(markup) == [f"tr:series:{a.id}", "tr:menu"]
    assert cb.answers == [(None, False)]


async def test_admin_list_shows_all_series_without_one_offs(session):
    admin, _, _, a, b = await setup_series(session)
    cb = FakeCallback("tr:slist")
    await trainer.on_series_list(cb, session, admin)
    assert callback_data(cb.message.edits[-1][1]) == [f"tr:series:{a.id}", f"tr:series:{b.id}", "tr:menu"]


async def test_empty_list_after_series_is_ended(session):
    admin, mine, _, a, _ = await setup_series(session)
    await ScheduleService(session).end_series(admin, a.id, NOW)
    cb = FakeCallback("tr:slist")
    await trainer.on_series_list(cb, session, mine)
    text, markup = cb.message.edits[-1]
    assert text == "Активных серий нет"
    assert callback_data(markup) == ["tr:menu"]


async def test_series_screen_has_summary_end_button_and_back_to_list(session):
    _, mine, _, a, _ = await setup_series(session)
    cb = FakeCallback(f"tr:series:{a.id}")
    await trainer.on_series_menu(cb, session, mine)
    text, markup = cb.message.edits[-1]
    assert "Место: Парк" in text and "Начало: 20.10.2026" in text
    data = callback_data(markup)
    assert f"tr:send:{a.id}" in data and f"tr:sedit:{a.id}:place" in data
    assert data[-1] == "tr:slist"


async def test_ended_series_screen_has_no_end_button(session):
    admin, mine, _, a, _ = await setup_series(session)
    await ScheduleService(session).end_series(admin, a.id, NOW)
    cb = FakeCallback(f"tr:series:{a.id}")
    await trainer.on_series_menu(cb, session, mine)
    assert not any(d.startswith("tr:send:") for d in callback_data(cb.message.edits[-1][1]))


async def test_series_screen_of_foreign_trainer_is_denied(session):
    _, mine, _, _, b = await setup_series(session)
    with pytest.raises(PermissionDenied):
        await trainer.on_series_menu(FakeCallback(f"tr:series:{b.id}"), session, mine)


async def test_end_ask_shows_count_and_confirm_buttons(session):
    _, mine, _, a, _ = await setup_series(session)
    cb = FakeCallback(f"tr:send:{a.id}")
    await trainer.on_series_end_ask(cb, session, mine)
    text, markup = cb.message.edits[-1]
    assert "Будущие тренировки (4)" in text and "Парк" in text
    assert callback_data(markup) == [f"tr:sendok:{a.id}", f"tr:series:{a.id}"]


async def test_end_do_ends_series_and_reports_count(session, messenger):
    _, mine, _, a, _ = await setup_series(session)
    cb = FakeCallback(f"tr:sendok:{a.id}")
    await trainer.on_series_end_do(cb, session, mine, messenger)
    text, markup = cb.message.edits[-1]
    assert text == "Серия завершена. Отменено тренировок: 4"
    assert callback_data(markup) == ["tr:slist"]
    session.expire_all()
    assert (await session.get(TrainingSeries, a.id)).end_date == date(2026, 10, 18)


async def test_end_do_by_foreign_trainer_changes_nothing(session, messenger):
    _, mine, _, _, b = await setup_series(session)
    with pytest.raises(PermissionDenied):
        await trainer.on_series_end_do(FakeCallback(f"tr:sendok:{b.id}"), session, mine, messenger)
    assert (await session.get(TrainingSeries, b.id)).end_date is None


async def test_end_do_twice_is_rejected(session, messenger):
    _, mine, _, a, _ = await setup_series(session)
    await trainer.on_series_end_do(FakeCallback(f"tr:sendok:{a.id}"), session, mine, messenger)
    with pytest.raises(InvalidAction):
        await trainer.on_series_end_do(FakeCallback(f"tr:sendok:{a.id}"), session, mine, messenger)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/telegram/test_series_handlers.py -q`
Expected: FAIL — `AttributeError: module 'bot.telegram.handlers.trainer' has no attribute 'on_series_list'`.

- [ ] **Step 3: Реализовать**

В `bot/telegram/handlers/trainer.py`:

1. Импорты:
   - `from bot.schedule.forms import (SERIES_FIELD_PROMPTS, TRAINING_FIELD_PROMPTS, apply_series_field, apply_training_field, series_details, series_title)`;
   - `from bot.schedule.service import ScheduleService, is_active_series, series_to_input`;
   - в импорт из `bot.usecases` добавить `end_series` (по алфавиту: `cancel_trainings, end_series, manual_add_participant, manual_remove_participant, update_series, update_training`).

2. Раздел «правка серии»: перед `on_series_menu` добавить обработчик списка, затем заменить `on_series_menu`. После `on_edit_series`/`on_series_confirm` (то есть до раздела «участники») добавить два обработчика завершения. Итоговый код блока:

```python
# ---------- правка серии ----------

@router.callback_query(F.data == "tr:slist")
async def on_series_list(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await ensure_trainer(session, user)
    items = await ScheduleService(session).active_series(user, utcnow())
    rows = [[(series_title(series_to_input(s)), f"tr:series:{s.id}")] for s in items]
    rows.append([("⬅️ Меню", "tr:menu")])
    await safe_edit(cb.message, "Мои серии" if items else "Активных серий нет", reply_markup=markup(*rows))
    await cb.answer()


@router.callback_query(F.data.startswith("tr:series:"))
async def on_series_menu(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    sid = _id(cb)
    series = await _get_editable_series(session, user, sid)
    labels = {"kind": "Тип", "place": "Место", "description": "Описание", "capacity": "Лимит",
              "weekdays": "Дни", "time": "Время", "end_date": "Окончание"}
    rows = [[(f"✏️ {label}", f"tr:sedit:{sid}:{field}")] for field, label in labels.items()]
    today = utcnow().astimezone(await club_tz(session)).date()
    if is_active_series(series, today):
        rows.append([("🏁 Завершить серию", f"tr:send:{sid}")])
    rows.append([("⬅️ Назад", "tr:slist")])
    details = series_details(series_to_input(series), [level.name for level in series.levels])
    await safe_edit(cb.message,
        f"{details}\n\nИзменение серии применится ко всем будущим тренировкам. Уже опубликованные будут отменены "
        "и заменены новыми, записи придётся оформить заново.",
        reply_markup=markup(*rows),
    )
    await cb.answer()
```

и (после `on_series_confirm`, перед `# ---------- участники ----------`):

```python
@router.callback_query(F.data.startswith("tr:send:"))
async def on_series_end_ask(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    series = await _get_editable_series(session, user, _id(cb))
    count = await ScheduleService(session).future_training_count(series.id, utcnow())
    await safe_edit(cb.message,
        f"Завершить серию?\n{series_title(series_to_input(series))}\n"
        f"Будущие тренировки ({count}) будут отменены, записавшимся уйдёт уведомление, "
        "новые создаваться не будут.",
        reply_markup=markup([("🏁 Да, завершить", f"tr:sendok:{series.id}"), ("Нет", f"tr:series:{series.id}")]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("tr:sendok:"))
async def on_series_end_do(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    cancelled = await end_series(session, messenger, user, _id(cb), utcnow())
    await safe_edit(cb.message,
        f"Серия завершена. Отменено тренировок: {len(cancelled)}",
        reply_markup=markup([("⬅️ К сериям", "tr:slist")]),
    )
    await cb.answer()
```

Обработчик `on_series_end_do` не использует FSM, поэтому `take_form` / `restore_form` не нужны; повторное нажатие отсекает сервис (`InvalidAction("Серия уже завершена")`).

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/telegram -q`
Expected: PASS (новые 10 тестов и существующие). Отдельно проверьте, что порядок хендлеров цел: `grep -n "tr:send\|on_stale" bot/telegram/handlers/trainer.py` — все `tr:send*` выше `on_stale`.

- [ ] **Step 5: Commit**

```bash
git add bot/telegram/handlers/trainer.py tests/telegram/test_series_handlers.py
git commit -m "feat(telegram): series list and end-series flow in trainer menu" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Документация и полный прогон

**Files:**
- Modify: `ARCHITECTURE.md` (строки около 103-104, 308-314)
- Modify: `README.md` (дерево меню тренера, около строк 141-150)

**Interfaces:**
- Consumes: всё из Задач 1-4.
- Produces: актуальное описание поведения (по `CLAUDE.md` изменения поведения отражаются в `ARCHITECTURE.md`).

- [ ] **Step 1: Обновить `ARCHITECTURE.md`**

1. В перечне usecases (строки 103-104) добавить `end_series` после `cancel_trainings`: `` `cancel_trainings`, `end_series`, `update_series`, ``.
2. После пункта «**Правка серии** (`update_series`) …» (заканчивается «Все проверки — до первой мутации.») добавить пункт:

```markdown
- **Завершение серии** (`end_series`, меню тренера → «🔁 Мои серии» → серия → «🏁 Завершить серию»):
  ставит `end_date` = вчера по `club_timezone` (серия сразу выпадает из `generate()` и из списка активных),
  отменяет будущие `scheduled` тренировки серии с причиной `user` (общий с `cancel` условный `UPDATE`,
  `_cancel_rows`), гасит их pending-задачи, отменяет заказы инвентаря и объявляет в чатах. Права — как у правки
  серии: владелец или админ; разовая серия и уже завершённая отклоняются (`InvalidAction`), повторное нажатие
  безопасно. Список активных серий (`active_series`): повторяющиеся, `end_date` пуст или не раньше местной
  сегодняшней даты; тренер видит свои, админ все.
```

3. В абзаце про меню (около строки 130-136) после упоминания `TRAINER_MENU` добавить одной фразой, что в меню тренера есть «🔁 Мои серии» (`tr:slist`), если там перечисляются пункты; иначе дополнение не нужно. Не переписывайте чужие незакоммиченные правки в этом абзаце.

- [ ] **Step 2: Обновить `README.md`**

В дереве меню тренера после блока «📋 Мои тренировки» (до «➕ Новая серия / ➕ Разовая») добавить:

```
├── 🔁 Мои серии
│   └── <серия> → сводка · ✏️ тип, место, описание, лимит, дни, время, окончание · 🏁 Завершить серию → подтверждение
```

- [ ] **Step 3: Полный прогон тестов**

Run: `python -m pytest -q`
Expected: все тесты зелёные, включая `tests/participation/` (тест конкурентности `-k concurrent`).

- [ ] **Step 4: Проверка в запущенном боте (по желанию, нужен `.env`)**

Run: `alembic upgrade head`, затем `python -m scripts.seed_demo`, `python -m bot.main`. В личке: `/start` → «🎓 Тренер» → «🔁 Мои серии». Демо-тренировка создаётся как разовая, поэтому в списке серий её не будет; для ручной проверки создайте серию через «➕ Новая серия». Пропустите шаг, если токена нет, и скажите об этом в отчёте.

- [ ] **Step 5: Commit**

Если `ARCHITECTURE.md` содержит чужие незакоммиченные правки, используйте `git add -p` (см. Preconditions).

```bash
git add ARCHITECTURE.md README.md
git commit -m "docs: end-series flow and «Мои серии» in architecture and README" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review (выполнен при написании плана)

**Покрытие спеки:**
- Список серий (`tr:slist`), фильтры активности, видимость тренер/админ, подпись, пустой список — Задачи 1 (`active_series`), 3 (`series_title`, кнопка), 4 (`on_series_list`).
- Экран серии со сводкой, кнопка «Завершить», «Назад» → `tr:slist` — Задачи 3 (`series_details`), 4 (`on_series_menu`).
- Подтверждение с N и завершение — Задача 4 (`on_series_end_ask`, `on_series_end_do`), `future_training_count` — Задача 1.
- `ScheduleService.end_series` (проверки до мутаций, `end_date` = вчера, отмена будущих, общий `_cancel_rows`) — Задача 1.
- Usecase (заказы инвентаря, объявления, лог) — Задача 2.
- Идемпотентность, гонка с `generate()`, задачи — Задача 1 (порядок: `end_date`, затем выборка), тесты повторного вызова и `generate()` после завершения.
- Документация — Задача 5.

**Два отличия от спеки (оба сужают поведение, не расширяют):**
1. Для разовой серии `end_series` бросает `InvalidAction` с отдельным текстом про «Мои тренировки», а не «Серия уже завершена»: в спеке «активная» включает непустые `weekdays`, и общий текст для разовой вводил бы в заблуждение.
2. Кнопка «Завершить серию» на экране серии показывается только для активной серии (разовая и завершённая её не видят); в спеке она безусловна, но на недоступное действие кнопка не нужна.

**Типы и имена:** `is_active_series`, `active_series`, `future_training_count`, `end_series` (сервис и usecase), `_cancel_rows`, `series_title`, `series_details`, `on_series_list`, `on_series_menu`, `on_series_end_ask`, `on_series_end_do`, `tr:slist` / `tr:send:` / `tr:sendok:` — одинаковы во всех задачах.

**Плейсхолдеры:** нет.
