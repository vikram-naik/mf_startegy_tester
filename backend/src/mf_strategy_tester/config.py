from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated process configuration loaded from MFST_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="MFST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./data/research.db"
    raw_data_path: Path = Path("./data/raw")
    download_timeout_seconds: int = Field(default=30, ge=1, le=300)
    download_retry_attempts: int = Field(default=4, ge=1, le=10)
    download_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=30)
    max_download_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    def ensure_local_directories(self) -> None:
        """Create a SQLite parent directory without touching non-file database URLs."""
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            database_path = Path(self.database_url.removeprefix(prefix))
            if database_path.name != ":memory:":
                database_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_data_path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
