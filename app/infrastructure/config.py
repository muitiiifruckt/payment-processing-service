from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    api_key: str = "local-dev-key"

    description_max_length: int = 512
    metadata_max_bytes: int = 8 * 1024

    webhook_timeout_seconds: float = 5.0
    webhook_max_response_bytes: int = 64 * 1024

    #: Демонстрационный приёмник webhook. В проде выключается.
    enable_webhook_sink: bool = True
    #: succeeded / failed — принудительный исход эмулятора шлюза для демонстрации
    gateway_force_outcome: str | None = None


settings = Settings()
