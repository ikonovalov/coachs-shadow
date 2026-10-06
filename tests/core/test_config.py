from bot.core.config import Config


def test_admin_ids_are_parsed_from_comma_list(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.setenv("ADMIN_IDS", "1, 2 ,3")
    assert Config(_env_file=None).admin_ids == [1, 2, 3]


def test_defaults(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    cfg = Config(_env_file=None)
    assert cfg.admin_ids == []
    assert cfg.club_timezone == "Europe/Moscow"
    assert cfg.database_url.startswith("sqlite+aiosqlite")
