import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def test_upgrade_head_on_empty_db(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    lead_index_sql = con.execute("select sql from sqlite_master where name='uq_chat_one_lead'").fetchone()[0]
    con.close()
    assert {
        "users", "levels", "chats", "chat_trainers", "training_series", "trainings",
        "series_levels", "training_levels", "participations", "training_posts",
        "scheduled_jobs", "settings",
    } <= tables
    assert "WHERE" in lead_index_sql.upper()
