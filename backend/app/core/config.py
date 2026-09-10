"""Application configuration loaded from environment variables."""
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_NAME: str = "Intelligent Document Extraction Platform"
    ENVIRONMENT: str = "development"
    API_PREFIX: str = "/api/v1"

    # Database. SQLite locally; point DATABASE_URL at Postgres in production.
    DATABASE_URL: str = f"sqlite:///{(PROJECT_ROOT / 'documents.db').as_posix()}"

    # File handling
    UPLOAD_DIR: str = str(PROJECT_ROOT / "uploads")
    MAX_FILE_SIZE_MB: int = 15
    MAX_PAGES: int = 3
    ALLOWED_EXTENSIONS: List[str] = [".pdf", ".jpg", ".jpeg", ".png"]
    ALLOWED_MIME_TYPES: List[str] = [
        "application/pdf",
        "image/jpeg",
        "image/png",
    ]

    # OCR
    OCR_LANGUAGE: str = "eng"
    TESSERACT_CMD: Optional[str] = None
    OCR_MIN_TEXT_CHARS: int = 30
    PDF_RENDER_DPI: int = 300

    # LLM (optional, disabled unless a key is provided)
    LLM_PROVIDER: str = "none"
    LLM_API_KEY: Optional[str] = None
    LLM_MODEL: str = "claude-sonnet-5"
    LLM_TIMEOUT_SECONDS: int = 30

    # Financial validation tolerance
    VALIDATION_ABSOLUTE_TOLERANCE: float = 1.0
    VALIDATION_RELATIVE_TOLERANCE: float = 0.01

    # CORS
    CORS_ORIGINS: str = "*"

    # Logging
    LOG_LEVEL: str = "INFO"

    @property
    def cors_origin_list(self) -> List[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    return settings
