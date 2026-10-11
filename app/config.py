from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    owner_telegram_id: int
    database_url: SecretStr
    migration_database_url: SecretStr | None = None
    timezone: str = "Europe/Warsaw"
    log_level: str = "INFO"
    slow_request_ms: int = Field(default=500, ge=0, le=60000)
    db_pool_size: int = Field(default=3, ge=1, le=20)
    db_max_overflow: int = Field(default=2, ge=0, le=20)
    web_host: str = "127.0.0.1"
    web_port: int = Field(default=8080, ge=1, le=65535)
    init_data_max_age_seconds: int = Field(default=3600, ge=60, le=86400)
    cloudflared_path: str = "cloudflared"
    reminder_first_minutes: int = Field(default=60, ge=1, le=10080)
    reminder_second_minutes: int = Field(default=180, ge=1, le=10080)
    reminder_later_minutes: int = Field(default=1440, ge=1, le=43200)
    reminder_poll_seconds: int = Field(default=60, ge=5, le=3600)

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def validate_postgres_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value():
            return None
        url = value.get_secret_value()
        if not url.startswith(("postgresql+asyncpg://", "postgresql://", "postgres://")):
            raise ValueError("Supabase/PostgreSQL connection URL is required")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
