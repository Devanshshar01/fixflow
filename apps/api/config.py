from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ──────────────────────────────────────────────────────────────
    # GitHub App
    # ──────────────────────────────────────────────────────────────

    github_app_id: Optional[str] = None
    github_client_id: Optional[str] = None
    github_client_secret: Optional[str] = None
    github_webhook_secret: Optional[str] = None

    # Local development
    github_app_private_key_path: Optional[str] = None

    # Production (Render)
    github_app_private_key: Optional[str] = None

    # ──────────────────────────────────────────────────────────────
    # Database
    # ──────────────────────────────────────────────────────────────

    database_url: str

    # ──────────────────────────────────────────────────────────────
    # AI
    # ──────────────────────────────────────────────────────────────

    gemini_api_key: Optional[str] = None
    ai_provider: str = "gemini"
    ollama_url: str = "http://localhost:11434"

    # ──────────────────────────────────────────────────────────────
    # App
    # ──────────────────────────────────────────────────────────────

    environment: str = "development"
    log_level: str = "INFO"
    max_log_bytes_per_step: int = 2_000_000

    # ──────────────────────────────────────────────────────────────
    # Validators
    # ──────────────────────────────────────────────────────────────

    @field_validator("github_app_private_key_path")
    @classmethod
    def validate_private_key_path(cls, v):
        if not v:
            return v

        path = Path(v)

        if not path.exists():
            raise ValueError(f"Private key not found at: {path.resolve()}")

        return v

    @field_validator("database_url")
    @classmethod
    def fix_postgres_scheme(cls, v: str):
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)

        if v.startswith("postgresql://") and "+asyncpg" not in v:
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)

        return v

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    @property
    def github_private_key(self) -> str:
        """
        Priority:
        1. Environment variable (Render)
        2. Local PEM file
        """

        if self.github_app_private_key:
            return self.github_app_private_key.replace("\\n", "\n")

        if self.github_app_private_key_path:
            return Path(self.github_app_private_key_path).read_text()

        raise ValueError(
            "No GitHub private key configured. "
            "Set GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH."
        )

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()