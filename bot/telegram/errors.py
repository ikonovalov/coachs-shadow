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
