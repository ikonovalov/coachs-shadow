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
