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
MENU_PAY = "menu:pay"
MENU_CLOSE = "menu:close"
BIND_PREFIX = "bind:"
ADD_LEVEL_CANCEL = "ad:cancel"

TEXT_SCHEDULE = "📅 Расписание"
TEXT_MY = "📋 Мои записи"
TEXT_PROFILE = "👤 Профиль"
TEXT_TRAINER = "🎓 Тренер"
TEXT_ADMIN = "⚙️ Админ"
TEXT_BACK = "⬅️ Главное меню"
TEXT_PAY = "💳 Оплата"
TEXT_CLOSE = "✖️ Закрыть"
TEXT_CANCEL = "✖️ Отмена"

# Команды в меню Telegram: те же пункты, что в главном меню, плюс /whoami. /add_level и /delete_level работают, но не рекламируются.
BOT_COMMANDS = [
    ("start", "Главное меню"),
    ("schedule", "Расписание"),
    ("my_bookings", "Мои записи"),
    ("profile", "Профиль"),
    ("trainer", "Меню тренера"),
    ("admin", "Меню администратора"),
    ("whoami", "Мой Telegram ID"),
]

def admin_menu(billing: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [("👥 Тренеры", "ad:trainers"), ("🛡 Админы", "ad:admins")],
        [("🏅 Уровни", "ad:levels"), ("💬 Чаты", "ch:list")],
        [("🚲 Парк инвентаря", "ad:park")],
    ]
    if billing:
        rows.append([("💰 Оплаты", "pay:menu")])
    rows += [[("🔗 Приглашение тренера", "ad:invite"), ("⚙️ Настройки", "ad:settings")], [(TEXT_BACK, MENU_BACK)]]
    return markup(*rows)


ADMIN_MENU = admin_menu()


TRAINER_MENU = markup(
    [("📋 Мои тренировки", "tr:list")],
    [("🔁 Мои серии", "tr:slist")],
    [("➕ Новая серия", "tr:new:series"), ("➕ Разовая", "tr:new:one")],
    [("💬 Мои чаты", "ch:list")],
    [(TEXT_BACK, MENU_BACK)],
)


BACK_ROW = [(TEXT_BACK, MENU_BACK)]


def main_menu(role: str, billing: bool = False) -> InlineKeyboardMarkup:
    rows = [[(TEXT_SCHEDULE, MENU_SCHEDULE)], [(TEXT_MY, MENU_MY)]]
    if billing:
        rows.append([(TEXT_PAY, MENU_PAY)])
    rows.append([(TEXT_PROFILE, MENU_PROFILE)])
    if role in (Role.TRAINER, Role.ADMIN):
        rows.append([(TEXT_TRAINER, MENU_TRAINER)])
    if role == Role.ADMIN:
        rows.append([(TEXT_ADMIN, MENU_ADMIN)])
    rows.append([(TEXT_CLOSE, MENU_CLOSE)])
    return markup(*rows)


def levels_keyboard(levels: list[Level]) -> InlineKeyboardMarkup:
    return markup(*[[(f"🔗 {level.name}", f"{BIND_PREFIX}{level.id}")] for level in levels])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return markup([(TEXT_CANCEL, ADD_LEVEL_CANCEL)])


def bot_commands(billing: bool = False) -> list[tuple[str, str]]:
    commands = list(BOT_COMMANDS)
    if billing:
        index = [command for command, _ in commands].index("my_bookings") + 1
        commands.insert(index, ("payments", "Оплата и абонементы"))
    return commands


PAY_MENU = markup(
    [("🧾 Должники", "pay:debt"), ("↩️ К возврату", "pay:rf")],
    [("🎟 Выдать абонемент", "pay:iss"), ("📑 Тарифы", "pay:pl")],
    [("💲 Цены", "pay:pr")],
    [("⬅️ Меню", "ad:menu")],
)
