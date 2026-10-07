from bot.core.permissions import Role
from bot.schedule.models import Level
from bot.telegram import menu


def buttons(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


COMMON = [("📅 Расписание", "menu:schedule"), ("📋 Мои записи", "menu:my"), ("👤 Профиль", "menu:profile")]
CLOSE = [("✖️ Закрыть", "menu:close")]


def test_main_menu_by_role():
    assert buttons(menu.main_menu(Role.MEMBER)) == COMMON + CLOSE
    assert buttons(menu.main_menu(Role.TRAINER)) == COMMON + [("🎓 Тренер", "menu:trainer")] + CLOSE
    assert buttons(menu.main_menu(Role.ADMIN)) == (
        COMMON + [("🎓 Тренер", "menu:trainer"), ("⚙️ Админ", "menu:admin")] + CLOSE
    )


def test_admin_button_only_for_admin():
    for role in (Role.MEMBER, Role.TRAINER):
        assert "menu:admin" not in [data for _, data in buttons(menu.main_menu(role))]


def test_submenus_end_with_back_and_keep_existing_buttons():
    for submenu in (menu.ADMIN_MENU, menu.TRAINER_MENU):
        assert buttons(submenu)[-1] == ("⬅️ Главное меню", "menu:back")
    admin = buttons(menu.ADMIN_MENU)
    for button in [("🎚 Уровни", "ad:levels"), ("💬 Чаты", "ch:list"), ("🚲 Парк инвентаря", "ad:park"),
                   ("👥 Тренеры", "ad:trainers"), ("🛡 Админы", "ad:admins"),
                   ("🔗 Приглашение тренера", "ad:invite"), ("⚙️ Настройки", "ad:settings")]:
        assert button in admin
    trainer = buttons(menu.TRAINER_MENU)
    for button in [("📋 Мои тренировки", "tr:list"), ("🔁 Мои серии", "tr:slist"), ("➕ Новая серия", "tr:new:series"),
                   ("➕ Разовая", "tr:new:one"), ("💬 Мои чаты", "ch:list")]:
        assert button in trainer
    data = [d for _, d in trainer]
    assert data.index("tr:list") + 1 == data.index("tr:slist")


def test_levels_keyboard_one_button_per_level():
    levels = [Level(id=1, name="Новички"), Level(id=22, name="Про")]
    assert buttons(menu.levels_keyboard(levels)) == [("🔗 Новички", "bind:1"), ("🔗 Про", "bind:22")]
    assert buttons(menu.levels_keyboard([])) == []


def test_cancel_keyboard():
    assert buttons(menu.cancel_keyboard()) == [("✖️ Отмена", "ad:cancel")]


def test_callback_data_fits_telegram_limit():
    levels = [Level(id=10**9, name="х" * 50)]
    every = [menu.main_menu(r) for r in (Role.MEMBER, Role.TRAINER, Role.ADMIN)]
    every += [menu.ADMIN_MENU, menu.TRAINER_MENU, menu.levels_keyboard(levels), menu.cancel_keyboard()]
    for kb in every:
        for _, data in buttons(kb):
            assert len(data.encode()) <= 64


def test_menu_prefixes_do_not_collide_with_existing_ones():
    taken = ("join:", "decline:", "who:", "noop", "tr:", "nf:", "pk:", "ch:", "ad:", "my:", "pf:", "inv:")
    for data in (menu.MENU_SCHEDULE, menu.MENU_MY, menu.MENU_PROFILE, menu.MENU_TRAINER, menu.MENU_ADMIN,
                 menu.MENU_BACK, menu.MENU_CLOSE, menu.BIND_PREFIX):
        assert not data.startswith(taken)


def test_bot_commands_are_only_start_and_whoami():
    assert [command for command, _ in menu.BOT_COMMANDS] == ["start", "whoami"]
    for _, description in menu.BOT_COMMANDS:
        assert 0 < len(description) <= 256
