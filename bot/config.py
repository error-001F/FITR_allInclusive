from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
