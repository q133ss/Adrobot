from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения, читаются из окружения / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Adrobot"
    debug: bool = False
    secret_key: str = "change-me-in-production"

    database_url: str = "sqlite:///./adrobot.db"
    auto_create_schema: bool = True

    keitaro_base_url: str = "http://127.0.0.1:8001"
    keitaro_api_key: str = "demo-api-key"
    keitaro_timeout: float = 20.0
    keitaro_verify_ssl: bool = True
    # In-process фейковый трекер: интерфейс работает без реального Keitaro.
    keitaro_demo: bool = False

    google_redirect_url: str = "https://www.google.com/"
    # reject: на Google уходит трафик вне списка гео (рабочий вариант).
    # accept: на Google уходит трафик из списка гео.
    default_geo_filter_mode: str = "reject"

    cache_ttl_seconds: int = 300
    max_api_log_body: int = 4000

    @field_validator("keitaro_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("default_geo_filter_mode")
    @classmethod
    def _validate_geo_mode(cls, value: str) -> str:
        if value not in {"accept", "reject"}:
            raise ValueError("DEFAULT_GEO_FILTER_MODE must be 'accept' or 'reject'")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
