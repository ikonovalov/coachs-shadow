# Архитектура Coach's Shadow

Telegram-бот велоклуба: расписание тренировок, публикация постов в чаты уровней за 24 часа до старта,
запись «Пойду / Не пойду» с лимитом мест, напоминания за 2 часа, отмена и автозакрытие.

Это обзор текущей реализации. Постановка задачи и мотивация — в
[`docs/superpowers/specs/2026-10-06-coachs-shadow-design.md`](docs/superpowers/specs/2026-10-06-coachs-shadow-design.md),
планы (в том числе ещё не реализованные) — в `docs/superpowers/plans/`. Команды запуска и тестов — в
[`README.md`](README.md) и [`CLAUDE.md`](CLAUDE.md).

## 1. Стек

| Область | Выбор |
|---|---|
| Язык | Python 3.11+, asyncio |
| Telegram | aiogram 3 (long polling, FSM в `MemoryStorage`) |
| БД | SQLAlchemy 2 (async) + aiosqlite (SQLite); модели совместимы с PostgreSQL |
| Миграции | Alembic |
| Конфигурация | pydantic-settings (`.env`) + таблица `settings` в БД |
| Тесты | pytest (`asyncio_mode = "auto"`), сеть и токен не нужны |
| Логи | stdlib `logging` в stdout |

Один процесс: `python -m bot.main` запускает и приём апдейтов, и планировщик фоновых задач.

## 2. Общая схема

```mermaid
flowchart TB
    TG[(Telegram)]
    subgraph TL["bot/telegram — единственный слой с aiogram"]
        MW["middlewares<br/>DbSession · clear_state_on_command · Access"]
        H["handlers<br/>common · picker · admin · chats · profile · my · inventory<br/>schedule · trainer · trainer_forms · participation"]
        AM["AiogramMessenger / AiogramMembership"]
    end
    UC["bot/usecases.py<br/>сквозные операции"]
    subgraph FEAT["Feature-пакеты (models + service)"]
        U[users]
        S[schedule]
        P[participation]
        PB[publishing]
    end
    J["bot/jobs<br/>очередь + scheduler_loop"]
    CORE["bot/core<br/>config · db · errors · settings · permissions · Messenger"]
    DB[(SQLite)]

    TG <--> MW --> H --> UC
    H --> FEAT
    UC --> FEAT
    J --> FEAT
    FEAT --> CORE
    FEAT --> DB
    PB -- "Messenger (протокол)" --> AM --> TG
    J -- "scheduler_loop(messenger)" --> PB
```

Зависимости направлены вниз. Домен не знает про aiogram: всё общение с Telegram идёт через протоколы
`Messenger` и `MembershipChecker` (`bot/core/messenger.py`).

## 3. Структура каталогов

```
bot/
  main.py            точка входа: сборка Dispatcher, роутеров, запуск scheduler_loop
  bootstrap.py       старт: синхронизация ADMIN_IDS, часовой пояс, первая generate-задача
  registry.py        импорт всех моделей (нужен Alembic autogenerate)
  usecases.py        операции, затрагивающие несколько фич (состояние + посты + уведомления)
  core/              config, db, errors, settings, permissions, messenger, logging_setup
  users/             User, ChatTrainer, Invite; роли, приглашения, доступ к боту
  schedule/          Level, Chat, TrainingSeries, Training; повторения, генерация, правка, отмена
  participation/     Participation; запись с лимитом мест
  publishing/        TrainingPost; рендер карточки, публикация, обновление, напоминания
  jobs/              ScheduledJob; очередь, обработчики, раннер
  telegram/          handlers/, middleware, keyboards, states, ui, messenger, membership
alembic/             миграции
scripts/seed_demo.py демо-тренировка на завтра 19:00
tests/               зеркалит bot/; fakes.py (FakeMessenger, FakeMembership), factories.py
```

## 4. Слои и правила зависимостей

### 4.1. `bot/core`
Общая база без бизнес-логики: `Config` (env), `Base` и `UTCDateTime` (`db.py`), иерархия `DomainError`
(`PermissionDenied`, `NotFound`, `InvalidAction`; текст показывается пользователю как есть), `Setting`
(ключ-значение в БД), чистые функции прав (`permissions.py`), протоколы `Messenger` / `MembershipChecker`.

### 4.2. Feature-пакеты
Каждый пакет — `models.py` + `service.py` (класс сервиса поверх `AsyncSession`).

| Пакет | Ответственность |
|---|---|
| `users` | пользователи и роли (`admin` / `trainer` / `member`), тренеры чатов и главный тренер, одноразовые приглашения тренеров, `AccessService` (допуск в личку) |
| `schedule` | уровни, привязка чатов к уровням, серии-шаблоны и тренировки, генерация по правилу повторения, правка и отмена |
| `participation` | запись «Пойду / Не пойду», ручное добавление/удаление тренером, контроль лимита мест |
| `publishing` | карточка тренировки (`render.py`), публикация в чаты, обновление постов, напоминания, закрытие, объявления об отмене/замене |
| `jobs` | очередь отложенных задач и её исполнитель |

Правила:

- **Фичи не импортируют сервисы друг друга.** Всё, что меняет состояние и одновременно должно обновить
  посты или уведомить людей, живёт в `bot/usecases.py`: `join_training`, `decline_training`,
  `cancel_trainings`, `update_series`, `update_training`, `manual_add_participant`,
  `manual_remove_participant`. Use case = вызов сервиса + `PublishService.refresh_posts` / объявление.
  Отказ, ручное удаление, отмена тренировки и замена при правке серии также отменяют активные заказы
  инвентаря (`InventoryService.cancel_for_participant` / `cancel_for_training`); участнику, который
  отказался или был удалён, приходит уведомление со списком отменённых единиц.
- Фичи могут делиться моделями и read-хелперами из `bot/schedule/queries.py` (`can_manage`,
  `going_count`, `target_chat_ids`, …), но не сервисами.
- Права проверяются **в сервисах**, а не в обработчиках: данные в callback и FSM подделываются.

### 4.3. `bot/telegram`
Единственное место, где импортируется aiogram.

- **Middleware** (порядок важен):
  1. `DbSessionMiddleware` (outer, на `dp.update`) — сессия БД на апдейт + upsert пользователя
     (`started_bot` ставится, когда пользователь пишет боту в личке; роль админа берётся из `ADMIN_IDS`);
  2. `clear_state_on_command` (outer, на `dp.message`) — любая `/команда` сбрасывает открытую форму/выбор/ввод
     и проваливается в свой обработчик;
  3. `AccessMiddleware` (на message и callback_query) — см. §6.
- **Роутеры** подключаются в порядке: `trainer_forms`, `common`, `picker`, `admin`, `chats`, `profile`, `my`,
  `inventory`, `schedule`, `trainer`, `participation`. `trainer_forms` — первым, потому что владеет `tr:new:*`, а у
  `trainer` есть catch-all `tr:`; по той же причине `inventory` (владеет `tr:inv:*`) стоит раньше `trainer`. Каждый роутер заканчивается stateless-обработчиком своего префикса `<prefix>:`,
  отвечающим на устаревшие кнопки.
- **Профиль и «Мои записи»** (только личка): `/profile` (`handlers/profile.py`, префикс `pf:`) — рост и размер
  ноги вводятся текстом в состоянии `ProfileInput.value` (неверный ввод → подсказка из `DomainError`, состояние
  сохраняется), шлем и тип шипов — кнопками; разбор и проверка значений — в `UserService.update_profile`.
  `/my` (`handlers/my.py`, префикс `my:`) — будущие тренировки с записью (`ParticipationService.my_upcoming`)
  и активные заказы инвентаря с кнопками отмены (`InventoryService.cancel_order` сам проверяет владельца,
  время старта и повторное нажатие).
- **Заказ инвентаря** (`handlers/inventory.py`, префиксы `inv:` / `iv:`, состояния `InventoryFlow`). Кнопка
  «🚲 Инвентарь» на карточке (`inv:<id>`) в группе отвечает ссылкой `t.me/<bot>?start=inv_<id>` («Продолжим в личке»),
  диплинк `inv_<id>` в `common.py` и та же кнопка в личке вызывают `begin_inventory`; незаписанному предлагается
  сначала записаться (`NotGoing` → кнопка `join:<id>`). Шаги (`_advance`): виды → тип педалей → «туфли свои или из
  парка» (только если педали из парка, а туфли нет) → шипы своих туфель (сохраняются в профиль; несовместимы с
  педалями — бот сразу говорит об этом и снова спрашивает педали) → недостающие рост/размер ноги (текстом, как в
  `/profile`) и шлем (кнопками) → `InventoryService.suggest` и обзор. «Подтвердить» забирает форму (`take_form`) и
  вызывает `confirm`; если часть единиц успели забрать (`ConfirmResult.conflicts`), подтверждённое остаётся, а
  замена подбирается только для этих видов — с тем же `pedal_type` из FSM (типом уже взятых педалей), потому что
  `confirm` сверяет совместимость педалей и туфель только внутри одного вызова. Сводка заказов для тренера —
  кнопка `tr:inv:<id>` на экране тренировки (права проверяет `training_summary`).
- **Формы и повторные нажатия.** Апдейты обрабатываются параллельно, поэтому подтверждающие
  обработчики сначала «забирают» форму (`take_form`), затем вызывают сервис, и при `DomainError`
  возвращают её (`restore_form`) — двойной тап не выполнит действие дважды. Экраны правятся через `safe_edit`.
- **Выбор пользователя** (`handlers/picker.py`): `start_picker(purpose=…, ctx=…)` + `@on_pick(purpose)`.
  Выбранные id приходят из подделываемых данных, поэтому каждый pick-обработчик повторно проверяет права
  в сервисе.
- **Ошибки домена**: `dp.errors.register(on_domain_error, ExceptionTypeFilter(DomainError))` превращает
  `DomainError` в сообщение пользователю.
- `AiogramMessenger` реализует `Messenger` (`send`, `edit_post`, `notify`), `AiogramMembership` —
  `MembershipChecker`; оба переводят ошибки Telegram в `ChatUnavailable` / `False`.

## 5. Модель данных

```mermaid
erDiagram
    users ||--o{ chat_trainers : "тренер чата"
    chats ||--o{ chat_trainers : ""
    levels ||--o{ chats : "несколько чатов на уровень"
    users ||--o{ invites : "создал / использовал"
    users ||--o{ training_series : trainer
    training_series }o--o{ levels : series_levels
    training_series ||--o{ trainings : "генерирует"
    trainings }o--o{ levels : training_levels
    trainings ||--o{ participations : ""
    users ||--o{ participations : ""
    trainings ||--o{ training_posts : "по посту на чат"
    chats ||--o{ training_posts : ""
    trainings ||--o{ scheduled_jobs : ""
    settings {
        string key PK
        string value
    }
```

Ключевые решения:

- **Серия — шаблон, тренировка — снимок.** `Training` копирует поля серии, поэтому правка серии не
  переписывает уже созданные тренировки. Разовая тренировка — серия с пустым `weekdays`.
- **Уникальность**: `uq_training_series_start` — одна тренировка на `(series_id, starts_at)`, кроме
  отменённых правкой серии (`cancel_reason = 'series_edit'`) — их место занимает замена.
  `uq_chat_one_lead` — не больше одного главного тренера на чат.
- **Статусы тренировки**: `scheduled` → `done` (закрыта по времени) или `cancelled`
  (`cancel_reason`: `user` / `series_edit`). Запись открыта, когда `published_at` не `NULL`.
- **Участие**: составной PK `(training_id, user_id)`, статус `going` / `declined`, `added_by` — кто
  добавил вручную.
- **Время хранится в UTC.** `UTCDateTime` принимает только aware-значения и всегда возвращает aware UTC.
  Правила повторения (`schedule/recurrence.py`) вычисляются в `club_timezone` (настройка в БД, при старте
  инициализируется из конфигурации, если её ещё нет).
- **Настройки в БД** (`settings`): `publish_window_h` (24), `reminder_h` (2), `duration_min` (90),
  `inventory_deadline_h`, `club_timezone`; значения по умолчанию — в `DEFAULTS`, валидация — `validate_setting`.
- Поля профиля инвентаря в `User` (рост, размер ноги/шлема, тип педалей) заведены, но функциональность —
  в плане `inventory-profile`, пока не реализована.
- Новые модели обязательно импортировать в `bot/registry.py`, иначе Alembic их не увидит.

## 6. Роли и доступ

**Роли**: `admin`, `trainer`, `member` (`core/permissions.py`). Администраторы из `ADMIN_IDS` синхронизируются
при старте и защищены от разжалования.

- Тренер может создавать серии только для **своих уровней** (`can_create_for_levels`) и управлять своими
  тренировками. **Главный тренер** чата управляет и чужими тренировками, если он ведёт *каждый* чат,
  на который нацелена тренировка (`can_manage_training`).
- Тренером становятся по **одноразовой ссылке-приглашению** `?start=trainer_<токен>` (создаёт админ,
  у токена есть срок годности).

**Доступ в личку** (`AccessMiddleware` + `AccessService`):

- В группах бот пропускает всё; нажатие кнопки в **привязанном** чате — доказательство членства
  (кладётся в кэш).
- В личке пускают только участников хотя бы одного привязанного чата (`MembershipChecker.is_member`);
  `ADMIN_IDS` — всегда. Результат кэшируется в памяти на 5 минут. Ошибка проверки по одному чату
  не блокирует остальные.
- Исключения: `/whoami` и `/start trainer_…` (приглашение должно работать до вступления).

## 7. Жизненный цикл тренировки

```mermaid
sequenceDiagram
    participant Q as Очередь jobs
    participant S as ScheduleService
    participant PB as PublishService
    participant C as Чаты уровней
    participant M as Участники

    Q->>S: generate (раз в 24 ч)
    S->>S: создать тренировки на 4 недели вперёд
    S->>Q: enqueue publish / remind_2h / close
    Q->>PB: publish (старт − 24 ч)
    PB->>C: пост с кнопками «Пойду / Не пойду»
    M->>C: нажимает «Пойду»
    C-->>PB: join_training → refresh_posts
    Q->>PB: remind_2h (старт − 2 ч)
    PB->>M: личное напоминание (notify)
    Q->>PB: close (старт)
    PB->>C: status=done, пост без кнопок
```

- **Генерация** (`ScheduleService.generate` / `_generate_for_series`): окно `GENERATION_WEEKS = 4`; одна
  тренировка серии на локальную дату (существующая, даже отменённая пользователем, занимает свой день).
  Для каждой новой тренировки ставятся три задачи: `publish` (не раньше «сейчас»), `remind_2h`
  (если ещё впереди) и `close`.
- **Публикация** (`PublishService.publish`): идемпотентна. Шлёт пост в каждый чат, на который нацелены
  уровни тренировки; после каждого чата делает коммит `TrainingPost`, поэтому сбой посередине
  продолжается без дублей. Недоступные чаты (`ChatUnavailable`) не валят задачу — админам уходит
  предупреждение. Между отправками небольшая пауза (`send_delay`).
- **Обновление постов** (`refresh_posts`): правит сообщения при записи, отказе, ручных изменениях и
  правке тренировки; если старое сообщение удалено — `edit_post` возвращает новый `message_id`.
- **Отмена** (`cancel_trainings`): помечает тренировки отменёнными, гасит их pending-задачи
  (`cancel_for_training`), отменяет заказы инвентаря (единицы сразу свободны), объявляет в чатах.
  Отмена пачкой — одним действием.
- **Правка серии** (`update_series`): неопубликованные будущие тренировки удаляются вместе с задачами;
  опубликованные отменяются с причиной `series_edit` (их заказы инвентаря отменяются), на их место
  генерируются новые, а в чатах
  появляется объявление о замене со ссылкой на новый пост. Все проверки — до первой мутации.

## 8. Запись и лимит мест

`ParticipationService` — самое чувствительное к конкурентности место. SQLite игнорирует `FOR UPDATE`, а
pysqlite выполняет SELECT вне транзакции, поэтому «проверил, потом записал» гоняется. Поэтому:

- проверка лимита и запись — **один SQL-оператор** (`_take_seat`: `INSERT … SELECT … WHERE count < capacity`
  либо условный `UPDATE`), выполняемый под блокировкой записи SQLite;
- переходы состояний (отмена записи, ручное удаление) — условный `UPDATE … WHERE status = …` с проверкой
  `rowcount`;
- параллельная вставка той же пары `(training, user)` ловится `IntegrityError` и повторяется;
- `FOR UPDATE` в `_open_training` оставлен для PostgreSQL.

Исходы `join`: `JOINED`, `ALREADY`, `FULL`. Конкурентность подкреплена тестом в `tests/participation/` —
его нужно сохранять зелёным при любых правках сервиса. Ручное добавление тренером может превысить лимит
(`allow_over_limit`).

## 9. Очередь задач (`bot/jobs`)

Очередь живёт в БД (`scheduled_jobs`), внешних брокеров нет, задачи переживают перезапуск.

- Виды: `generate`, `publish`, `remind_2h`, `close`. Поля: `run_at`, `training_id`, `done_at`.
- `scheduler_loop` тикает каждые 30 с и вызывает `run_due_jobs`: выбирает до 100 просроченных задач
  (`done_at IS NULL AND run_at <= now`) и выполняет **каждую в отдельной сессии/транзакции**.
- Успех → `done_at = now` и коммит; исключение → `rollback`, задача остаётся и повторится на следующем тике.
  Ошибка тика целиком логируется и не останавливает цикл.
- Обработчики (`jobs/handlers.py`) **идемпотентны**: перепроверяют состояние тренировки (статус, время
  старта, `published_at`), поэтому повтор или запоздалый запуск безопасны.
- `generate` после выполнения пересоздаёт себя через 24 ч (`ensure_generate_job`); `bootstrap` гарантирует,
  что такая задача есть при каждом старте.

## 10. Конфигурация и запуск

Порядок старта (`bot/main.py`): `Config()` → `setup_logging` → engine и session factory → `bootstrap`
(синхронизация админов, часовой пояс, `generate`-задача) → `Bot` + `Dispatcher` (middleware, роутеры) →
`scheduler_loop` в `asyncio.create_task` → `dp.start_polling`. При остановке планировщик отменяется, engine
закрывается.

Переменные окружения (`.env.example`): `BOT_TOKEN`, `ADMIN_IDS` (через запятую), `DATABASE_URL`
(по умолчанию SQLite-файл), `CLUB_TIMEZONE`, `LOG_LEVEL`.

Логирование: единственная точка настройки — `setup_logging()` в `bot/core/logging_setup.py`; модули берут
`log = logging.getLogger(__name__)` и не вызывают `basicConfig`. Шумные библиотеки (`sqlalchemy.engine`,
`aiosqlite`) приглушены до WARNING.

Схема БД создаётся и обновляется через `alembic upgrade head`; после изменения моделей —
`alembic revision --autogenerate`.

## 11. Тестирование

- `tests/` повторяет структуру `bot/`; сеть и токен не нужны.
- Домен тестируется с `FakeMessenger` и `FakeMembership` из `tests/fakes.py` — это прямая выгода от
  протоколов `Messenger` / `MembershipChecker`.
- `tests/factories.py` — фабрики данных, `tests/test_migrations.py` — проверка миграций,
  `tests/test_usecases*.py` — сквозные сценарии.
- Отдельный тест на конкурентную запись при лимите мест (`tests/participation/`).

## 12. Расширение: чек-лист

- **Новая фича** — пакет `bot/<feature>/` с `models.py` + `service.py`; модели → `bot/registry.py`;
  миграция через Alembic; без импорта сервисов соседних фич.
- **Действие, меняющее состояние и посты** — функция в `bot/usecases.py`, хендлер вызывает её.
- **Новая отложенная работа** — новый `JobKind`, ветка в `handle_job`, идемпотентный обработчик.
- **Новый экран/форма в Telegram** — хендлер в `bot/telegram/handlers/`, `take_form`/`restore_form`,
  catch-all для своего префикса в конце роутера, проверка прав в сервисе.
- **Работа с Telegram из домена** — только через `Messenger` / `MembershipChecker`; новый метод добавляется
  в протокол, в `AiogramMessenger` и в `FakeMessenger`.
