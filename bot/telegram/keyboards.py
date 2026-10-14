from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core.messenger import Keyboard


def to_markup(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    """Доменная клавиатура поста → InlineKeyboardMarkup (None остаётся None)."""
    if keyboard is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=b.text, callback_data=b.data, url=b.url) for b in row] for row in keyboard
        ]
    )
