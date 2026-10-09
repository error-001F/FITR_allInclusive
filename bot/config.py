from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Не показывать значения из .env в текстах ошибок валидации.
        hide_input_in_errors=True,
    )

    bot_token: SecretStr
    database_path: Path = Path("data/bot.db")
    # Необязательные настройки мастера: без них бот работает, но без уведомлений и ссылки.
    admin_id: int | None = None
    master_username: str | None = None
    payment_details: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
