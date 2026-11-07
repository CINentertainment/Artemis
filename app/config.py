import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


def get_default_data_directory() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")

    if local_app_data:
        application_root = Path(local_app_data)
    else:
        application_root = (
            Path.home()
            / "AppData"
            / "Local"
        )

    return (
        application_root
        / "CIN Entertainment"
        / "Artemis"
        / "data"
    )


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

    model_provider: str = "deterministic"

    model_name: str = (
        "artemis-deterministic"
    )

    model_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
    )

    model_max_output_tokens: int = Field(
        default=2048,
        ge=1,
        le=100000,
    )

    ollama_url: str = (
        "http://127.0.0.1:11434"
    )

    ollama_timeout_seconds: float = Field(
        default=120.0,
        ge=1.0,
        le=600.0,
    )

    data_directory: Path = Field(
        default_factory=get_default_data_directory
    )

    database_filename: str = "artemis.db"

    odysseus_url: str = (
        "http://127.0.0.1:7000"
    )

    chroma_url: str = (
        "http://127.0.0.1:8100"
    )

    intel_notes_url: str = (
        "http://127.0.0.1:8001"
    )

    risk_tracker_url: str = (
        "http://127.0.0.1:8002"
    )

    market_lens_url: str = (
        "http://127.0.0.1:8003"
    )

    @property
    def database_path(self) -> Path:
        return (
            self.data_directory
            / self.database_filename
        )

    @property
    def database_url(self) -> str:
        database_path = (
            self.database_path
            .resolve()
            .as_posix()
        )

        return f"sqlite:///{database_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()