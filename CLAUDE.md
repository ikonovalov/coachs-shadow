# CLAUDE.md

Этот файл содержит рекомендации для Claude Code (claude.ai/code) по работе с кодом этого репозитория.

Coach's Shadow — Telegram-бот велоклуба (README и документация на русском): расписание тренировок, посты в чаты уровней за 24 часа до старта, запись «Пойду / Не пойду» с лимитом мест, напоминания за 2 часа, отмена и автозакрытие. Стек: Python 3.11+, aiogram 3, SQLAlchemy 2 async + aiosqlite, Alembic, pydantic-settings, pytest. Спецификация дизайна: `docs/superpowers/specs/2026-10-06-coachs-shadow-design.md`; планы реализации (в том числе ещё не реализованные) — в `docs/superpowers/plans/`.

## Команды

```bash
pip install -e ".[dev]"                  # установка (venv в .venv; на Windows — .venv\Scripts\python)
alembic upgrade head                     # создать/обновить схему БД
python -m bot.main                       # запуск бота (long polling + планировщик); нужен .env (см. .env.example)
python -m scripts.seed_demo              # демо-тренировка на завтра 19:00
python -m pytest -q                      # все тесты (сеть и токен не нужны)
python -m pytest tests/schedule -v       # одна папка
python -m pytest -k concurrent           # по имени
alembic revision --autogenerate -m "msg" # после изменения моделей
```

Линтер не настроен. pytest работает с `asyncio_mode = "auto"`.

## Архитектура

Полное описание (слои, модель данных, роли и доступ, жизненный цикл тренировки, очередь задач, запуск, чек-лист расширения) — в [`ARCHITECTURE.md`](ARCHITECTURE.md); перед изменением слоя прочитайте соответствующий раздел. При изменении поведения обновляйте его. Здесь его не пересказывайте.

Клавиатуры меню и `callback_data` (`menu:*`, `bind:*`, `ad:cancel`) живут в `bot/telegram/menu.py` (чистые построители, тестируются без Telegram); меню команд Telegram задаёт `BOT_COMMANDS` (все пункты главного меню как команды + `/whoami`).

Жёсткие правила (подробности — в указанных разделах):
- **aiogram импортируется только в `bot/telegram/`**; домен общается с Telegram через `Messenger` / `MembershipChecker` (`bot/core/messenger.py`), тесты используют фейки из `tests/fakes.py` — §2, §4.3, §12.
- **Фичи не импортируют сервисы друг друга**; изменения состояния, которые должны обновить посты или уведомить людей, идут через `bot/usecases.py`, и обработчики вызывают именно их — §4.2.
- **Права проверяются в сервисах**, а не берутся на веру из callback-данных, FSM или результатов picker — §4.2, §4.3.
- **Обработчики Telegram**: `take_form` до вызова сервиса, `restore_form` при `DomainError`, экраны правятся через `safe_edit`, роутер `trainer_forms` первым, каждый роутер заканчивается catch-all `<prefix>:` — §4.3.
- **Время хранится в UTC**; повторения вычисляются в `club_timezone` — §5.
- **Новые модели обязательно импортировать в `bot/registry.py`**, иначе Alembic autogenerate их не увидит — §5.
- **Обработчики задач должны быть идемпотентными**; отмена тренировки должна гасить её pending-задачи — §7, §9.
- **Логирование**: только stdlib `logging`, настраивается один раз в `setup_logging()` (`bot/core/logging_setup.py`); модули используют `log = logging.getLogger(__name__)` и не вызывают `basicConfig` — §10.
- **Конкурентность при лимите мест**: при правках `ParticipationService` тест в `tests/participation/` должен оставаться зелёным; проверка лимита и запись — один SQL-оператор, переходы состояний — условный `UPDATE` + проверка rowcount — §8.
