from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    application_name: str = "Artemis"
    application_version: str = "0.1.0"
    environment: str = "development"

    host: str = "127.0.0.1"
    port: int = Field(
        default=8200,
        ge=1,
        le=65535,
    )

    debug: bool = False
    telemetry_enabled: bool = False
    external_access_enabled: bool = False

    odysseus_url: str = "http://127.0.0.1:7000"
    chroma_url: str = "http://127.0.0.1:8100"
    intel_notes_url: str = "http://127.0.0.1:8001"
    risk_tracker_url: str = "http://127.0.0.1:8002"
    market_lens_url: str = "http://127.0.0.1:8003"


@lru_cache
def get_settings() -> Settings:
    return Settings()