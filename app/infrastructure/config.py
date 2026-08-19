from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.limits import DESCRIPTION_MAX


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    # без дефолта: забытый API_KEY иначе оставит сервис под ключом из репозитория
    api_key: str

    prefetch_count: int = 10

    #: Верхняя граница зашита в колонку payments.description: настройка
    #: сверх неё дала бы 500 на записи вместо 422 на валидации
    description_max_length: int = Field(default=DESCRIPTION_MAX, le=DESCRIPTION_MAX, gt=0)
    metadata_max_bytes: int = 8 * 1024

    webhook_timeout_seconds: float = 5.0
    webhook_max_response_bytes: int = 64 * 1024
    #: Хосты, которым разрешено быть внутри периметра, через запятую.
    #: Нужны демонстрационному приёмнику: внутри сети compose он приватный
    webhook_allowed_hosts: str = ""

    #: Демонстрационный приёмник webhook. Включается только для демо и e2e.
    enable_webhook_sink: bool = False
    #: Принудительный исход эмулятора шлюза для демонстрации. Literal, а не
    #: строка: всё, что не равно succeeded, означало бы отказ по всем платежам,
    #: и опечатка прошла бы незамеченной до самого демо
    gateway_force_outcome: Literal["succeeded", "failed"] | None = None

    @property
    def webhook_allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.webhook_allowed_hosts.split(",") if host.strip()]

    @field_validator("gateway_force_outcome", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: object) -> object:
        # compose подставляет пустую строку для незаданной переменной,
        # а пустая строка — не «succeeded» и не «failed»
        return None if value == "" else value


# значения приходят из окружения, а не из вызова — mypy об этом не знает
settings = Settings()  # type: ignore[call-arg]
