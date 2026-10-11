"""Application configuration.

All settings come from environment variables (optionally loaded from a local
``.env`` file). Nothing here is required for the core demo: every value has a
safe default and the LLM integration is disabled unless explicitly configured.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "productpulse.db"

load_dotenv(PROJECT_ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRODUCTPULSE_", extra="ignore")

    # Storage
    database_url: str = Field(
        default=f"sqlite:///{DEFAULT_SQLITE_PATH}",
        description="SQLAlchemy URL. Use postgresql+psycopg2://... for durable hosted storage.",
    )

    # Upload limits
    max_upload_mb: float = Field(default=10.0, gt=0, le=200)
    max_import_rows: int = Field(default=20_000, gt=0, le=500_000)
    max_text_chars: int = Field(default=5_000, gt=10)

    # Analysis
    embedding_backend: Literal["auto", "sentence-transformers", "tfidf"] = "auto"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    max_analysis_rows: int = Field(default=6_000, gt=10)

    # Optional LLM summaries
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = ""
    llm_base_url: str = ""
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, value: str) -> str:
        value = value.strip()
        # Heroku/Render style URLs use the deprecated "postgres://" scheme.
        if value.startswith("postgres://"):
            value = "postgresql+psycopg2://" + value[len("postgres://") :]
        return value

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "none" and bool(self.llm_api_key)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def describe(self) -> dict[str, str]:
        """Non-secret summary for the Settings page and health check."""
        db = self.database_url
        if "@" in db:  # hide credentials
            scheme, rest = db.split("://", 1)
            db = f"{scheme}://***@{rest.split('@', 1)[1]}"
        return {
            "Database": db,
            "Embedding backend": self.embedding_backend,
            "Embedding model": self.embedding_model,
            "LLM provider": self.llm_provider if self.llm_enabled else "disabled",
            "Max upload (MB)": str(self.max_upload_mb),
            "Max import rows": str(self.max_import_rows),
            "Max analysis rows": str(self.max_analysis_rows),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
