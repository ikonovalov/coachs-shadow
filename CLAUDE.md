# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Coach's Shadow is a Telegram bot for a cycling club (README and docs are in Russian): training schedule, posts published to level chats 24h before start, "Пойду / Не пойду" sign-up with capacity limit, 2h reminders, cancellation and auto-close. Stack: Python 3.11+, aiogram 3, SQLAlchemy 2 async + aiosqlite, Alembic, pydantic-settings, pytest. Design spec: `docs/superpowers/specs/2026-10-06-coachs-shadow-design.md`; implementation plans (including not-yet-built ones) are in `docs/superpowers/plans/`.

## Commands

```bash
pip install -e ".[dev]"                  # install (venv in .venv; on Windows use .venv\Scripts\python)
alembic upgrade head                     # create/update DB schema
python -m bot.main                       # run bot (long polling + scheduler); needs .env (see .env.example)
python -m scripts.seed_demo              # demo training for tomorrow 19:00
python -m pytest -q                      # all tests (no network/token needed)
python -m pytest tests/schedule -v       # one folder
python -m pytest -k concurrent           # by name
alembic revision --autogenerate -m "msg" # after model changes
```

No linter is configured. pytest runs with `asyncio_mode = "auto"`.

## Architecture

Feature packages under `bot/` (`users`, `schedule`, `participation`, `publishing`, `jobs`), each with `models.py` + `service.py`, over a shared `bot/core` (config, db, errors, settings, permissions, `Messenger` protocol). `tests/` mirrors `bot/`.

Rules that span files:
- **Only `bot/telegram/` imports aiogram.** Everything else talks to Telegram through the `Messenger` protocol (`bot/core/messenger.py`: `send`, `edit_post`, `notify`). Tests use `FakeMessenger` from `tests/fakes.py`. `ChatUnavailable` is the contract for a chat the bot can't write to.
- **Features don't depend on each other's services for cross-cutting actions** — those live in `bot/usecases.py` (e.g. `join_training` = participation join + refresh published posts; `cancel_trainings` = cancel + announce). Telegram handlers should call use cases for any state change that must also update posts.
- **Time is stored in UTC**; recurrence rules are evaluated in the `club_timezone` setting (stored in DB settings, seeded from config by `bootstrap`).
- **Scheduled work is a DB-backed job queue** (`bot/jobs`): `ScheduledJob` rows with kinds `generate`/`publish`/`remind_2h`/`close`. `scheduler_loop` ticks every 30s, `run_due_jobs` runs each job in its own session/transaction; failures roll back and the job is retried next tick. The `generate` job re-enqueues itself 24h later (`ensure_generate_job`). Cancelling a training marks its pending jobs done (`cancel_for_training`).
- **New models must be imported in `bot/registry.py`** or Alembic autogenerate won't see them.
- **Logging uses stdlib `logging` only** (no loguru/structlog). Logs go to stdout; configure once in a `setup_logging()` in `bot/core/`, called first in `bot/main.py`, with the level taken from config (`LOG_LEVEL`). Modules use `log = logging.getLogger(__name__)` and never call `basicConfig` or add handlers themselves. Noisy libraries (`sqlalchemy.engine`, `aiosqlite`) are set to WARNING.
- Concurrency of joins vs. capacity is pinned by a test in `tests/participation/` — keep it passing when touching `ParticipationService`.
