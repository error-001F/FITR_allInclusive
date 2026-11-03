from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Не показывать значения из .env в текстах ошибок валидации.
        hide_input_in_errors=True,
        # Позволяет передавать admin_ids=[...] по имени поля (например, в тестах).
        populate_by_name=True,
    )

    bot_token: SecretStr
    database_path: Path = Path("data/bot.db")
    # Необязательные настройки мастеров: без них бот работает, но без уведомлений и ссылки.
    # В .env: ADMIN_IDS=111,222 (через запятую). Старое имя ADMIN_ID тоже принимается.
    # NoDecode: не пытаться читать значение как JSON — разбираем строку сами (см. ниже).
    admin_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list, validation_alias=AliasChoices("ADMIN_IDS", "ADMIN_ID")
    )
    master_username: str | None = None
    # Реквизиты для оплаты. Пустое значение — этот способ клиенту не предлагается.
    payment_card: str = ""
    payment_iban: str = ""
    payment_recipient: str = ""  # получатель (ФИО, банк) — показывается под реквизитами

    @field_validator("admin_ids", mode="before")
    @classmethod
    def split_admin_ids(cls, value: Any) -> Any:
        # "111, 222" → ["111", "222"]; дальше pydantic сам превратит их в числа
        # (или остановит запуск понятной ошибкой, если там не число).
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, int):
            return [value]
        return value


    def available_payment_methods(self) -> list[str]:
        """Способы оплаты в порядке PAYMENT_METHOD_TITLES: карта и IBAN — если заданы реквизиты."""
        details = {"card": self.payment_card, "iban": self.payment_iban}
        return [method for method in ("card", "iban", "cash") if method == "cash" or details[method]]

    def payment_value(self, method: str) -> str:
        """Номер карты или IBAN для способа оплаты; для наличных — пустая строка."""
        return {"card": self.payment_card, "iban": self.payment_iban}.get(method, "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
