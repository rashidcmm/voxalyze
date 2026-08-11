from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://gdtrainer:gdtrainer@localhost:5432/gdtrainer"
    database_url_sync: str = "postgresql+psycopg2://gdtrainer:gdtrainer@localhost:5432/gdtrainer"

    # Redis / jobs
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    # Storage
    storage_dir: str = "./storage"

    # Transcription (Day 2+)
    transcriber_provider: str = "local"
    whisper_model: str = "base.en"

    # Model layer (Day 4+)
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    anthropic_api_key: str = ""
    # claude-opus-5 by default; override to a cheaper model (e.g. claude-haiku-4-5) if
    # per-session LLM cost matters more than judgment quality for argument scoring.
    anthropic_model: str = "claude-opus-5"
    embedding_model: str = "all-MiniLM-L6-v2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
