from aiogram.types import InlineKeyboardMarkup

from bot.core.messenger import Button
from bot.telegram.keyboards import to_markup


def test_none_stays_none():
    assert to_markup(None) is None


def test_empty_keyboard_becomes_empty_markup_that_removes_buttons():
    markup = to_markup([])
    assert isinstance(markup, InlineKeyboardMarkup) and markup.inline_keyboard == []


def test_callback_and_url_buttons():
    markup = to_markup([[Button("A", "join:1"), Button("B", url="https://t.me/x")]])
    a, b = markup.inline_keyboard[0]
    assert (a.text, a.callback_data, a.url) == ("A", "join:1", None)
    assert (b.text, b.callback_data, b.url) == ("B", None, "https://t.me/x")
