from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    # без дефолта: забытый API_KEY иначе оставит сервис под ключом из репозитория
    api_key: str

    description_max_length: int = 512
    metadata_max_bytes: int = 8 * 1024

    webhook_timeout_seconds: float = 5.0
    webhook_max_response_bytes: int = 64 * 1024

    #: Демонстрационный приёмник webhook. Включается только для демо и e2e.
    enable_webhook_sink: bool = False
    #: succeeded / failed — принудительный исход эмулятора шлюза для демонстрации
    gateway_force_outcome: str | None = None


# значения приходят из окружения, а не из вызова — mypy об этом не знает
settings = Settings()  # type: ignore[call-arg]
