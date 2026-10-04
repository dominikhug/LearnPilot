from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # Environment variables win; the repo-root .env is a fallback for local runs.
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://learnpilot:learnpilot@db:5432/learnpilot"
    app_password: str = ""
    session_secret: str
    session_cookie_secure: bool = True
    session_max_age_days: int = 30

    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    max_document_tokens: int = 50_000
    max_upload_mb: int = 20
    daily_token_limit: int = 2_000_000

    static_dir: Path = REPO_ROOT / "frontend" / "dist"

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, url: str) -> str:
        # Railway provides postgresql:// (or postgres://); SQLAlchemy needs the driver named.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url


settings = Settings()
