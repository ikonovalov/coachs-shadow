"""Клавиатуры меню и константы callback_data. Без обращений к БД и к Telegram API.

(Рендерер постов тренировок — отдельно, в keyboards.py.)"""
from aiogram.types import InlineKeyboardMarkup

from bot.core.permissions import Role
from bot.schedule.models import Level
from bot.telegram.ui import markup

MENU_SCHEDULE = "menu:schedule"
MENU_MY = "menu:my"
MENU_PROFILE = "menu:profile"
MENU_TRAINER = "menu:trainer"
MENU_ADMIN = "menu:admin"
MENU_BACK = "menu:back"
BIND_PREFIX = "bind:"
ADD_LEVEL_CANCEL = "ad:cancel"

TEXT_SCHEDULE = "📅 Расписание"
TEXT_MY = "📋 Мои записи"
TEXT_PROFILE = "👤 Профиль"
TEXT_TRAINER = "🎓 Тренер"
TEXT_ADMIN = "⚙️ Админ"
TEXT_BACK = "⬅️ Главное меню"
TEXT_CANCEL = "✖️ Отмена"

# Команды в меню Telegram. /schedule, /my, /profile, /trainer, /admin и /add_level работают, но не рекламируются:
# всё, кроме /add_level, есть в кнопках главного меню.
BOT_COMMANDS = [("start", "Главное меню"), ("whoami", "Мой Telegram ID")]

ADMIN_MENU = markup(
    [("👥 Тренеры", "ad:trainers"), ("🛡 Админы", "ad:admins")],
    [("🎚 Уровни", "ad:levels"), ("💬 Чаты", "ch:list")],
    [("🚲 Парк инвентаря", "ad:park")],
    [("🔗 Приглашение тренера", "ad:invite"), ("⚙️ Настройки", "ad:settings")],
    [(TEXT_BACK, MENU_BACK)],
)
TRAINER_MENU = markup(
    [("📋 Мои тренировки", "tr:list")],
    [("➕ Новая серия", "tr:new:series"), ("➕ Разовая", "tr:new:one")],
    [("💬 Мои чаты", "ch:list")],
    [(TEXT_BACK, MENU_BACK)],
)


def main_menu(role: str) -> InlineKeyboardMarkup:
    rows = [[(TEXT_SCHEDULE, MENU_SCHEDULE)], [(TEXT_MY, MENU_MY)], [(TEXT_PROFILE, MENU_PROFILE)]]
    if role in (Role.TRAINER, Role.ADMIN):
        rows.append([(TEXT_TRAINER, MENU_TRAINER)])
    if role == Role.ADMIN:
        rows.append([(TEXT_ADMIN, MENU_ADMIN)])
    return markup(*rows)


def levels_keyboard(levels: list[Level]) -> InlineKeyboardMarkup:
    return markup(*[[(f"🔗 {level.name}", f"{BIND_PREFIX}{level.id}")] for level in levels])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return markup([(TEXT_CANCEL, ADD_LEVEL_CANCEL)])
