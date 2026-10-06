import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import ExceptionTypeFilter

from bot.core.config import Config
from bot.core.db import make_engine, make_session_factory, utcnow
from bot.core.errors import DomainError
from bot.core.logging_setup import setup_logging
from bot.bootstrap import bootstrap
from bot.jobs.runner import scheduler_loop
from bot.telegram.errors import on_domain_error
from bot.telegram.handlers import admin, common, participation, picker, schedule, trainer, trainer_forms
from bot.telegram.membership import AiogramMembership
from bot.telegram.messenger import AiogramMessenger
from bot.telegram.middleware import AccessMiddleware, DbSessionMiddleware

log = logging.getLogger(__name__)


async def main() -> None:
    cfg = Config()
    setup_logging(cfg.log_level)
    engine = make_engine(cfg.database_url)
    session_factory = make_session_factory(engine)
    await bootstrap(session_factory, cfg, utcnow())

    bot = Bot(cfg.bot_token)
    messenger = AiogramMessenger(bot)
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(DbSessionMiddleware(session_factory, cfg.admin_ids))
    access = AccessMiddleware(AiogramMembership(bot), cfg.admin_ids)
    dp.message.middleware(access)
    dp.callback_query.middleware(access)
    dp.errors.register(on_domain_error, ExceptionTypeFilter(DomainError))
    # trainer_forms first: its outer middleware must drop the form state before common commands (/start) are handled
    dp.include_routers(
        trainer_forms.router, common.router, picker.router, admin.router, schedule.router, trainer.router,
        participation.router,
    )

    scheduler = asyncio.create_task(scheduler_loop(session_factory, messenger))
    log.info("bot started: admins=%d, timezone=%s", len(cfg.admin_ids), cfg.club_timezone)
    try:
        await dp.start_polling(bot, messenger=messenger)
    finally:
        log.info("bot stopping")
        scheduler.cancel()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
