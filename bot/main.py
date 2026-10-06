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
from bot.telegram.handlers import (
    admin, binding, chats, common, inventory, my, park, participation, picker, profile, schedule, trainer, trainer_forms,
)
from bot.telegram.membership import AiogramMembership
from bot.telegram.messenger import AiogramMessenger
from bot.telegram.middleware import AccessMiddleware, DbSessionMiddleware, clear_state_on_command

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
    # on the dispatcher itself, so it runs before any router: a command drops whatever form/picker/input was open
    dp.message.outer_middleware(clear_state_on_command)
    access = AccessMiddleware(AiogramMembership(bot), cfg.admin_ids)
    dp.message.middleware(access)
    dp.callback_query.middleware(access)
    dp.errors.register(on_domain_error, ExceptionTypeFilter(DomainError))
    # trainer_forms first: it handles "tr:new:*", which the trainer router's "tr:" catch-all would swallow;
    # inventory before trainer for the same reason ("tr:inv:*"); park before admin ("ad:park", "ad:pk*" vs "ad:").
    # Each router ends with a stateless catch-all for its own "<prefix>:" callbacks (expired buttons).
    dp.include_routers(
        trainer_forms.router, common.router, binding.router, picker.router, park.router, admin.router,
        chats.router, profile.router, my.router, inventory.router, schedule.router, trainer.router,
        participation.router,
    )

    scheduler = asyncio.create_task(scheduler_loop(session_factory, messenger))
    log.info("bot started: admins=%d, timezone=%s", len(cfg.admin_ids), cfg.club_timezone)
    try:
        await dp.start_polling(bot, messenger=messenger, admin_ids=cfg.admin_ids)
    finally:
        log.info("bot stopping")
        scheduler.cancel()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
