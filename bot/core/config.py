from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    admin_ids_raw: str = Field("", validation_alias="ADMIN_IDS")
    database_url: str = "sqlite+aiosqlite:///coachs_shadow.db"
    club_timezone: str = "Europe/Moscow"

    @property
    def admin_ids(self) -> list[int]:
        return [int(x) for x in self.admin_ids_raw.split(",") if x.strip()]
