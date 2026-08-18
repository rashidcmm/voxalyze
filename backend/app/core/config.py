from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-to-a-long-random-string"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "dev" | anything else. Outside "dev", app/main.py's lifespan refuses to start
    # if jwt_secret is still DEFAULT_JWT_SECRET (that placeholder is public — it's
    # committed to this repo).
    env: str = "dev"

    # Database
    database_url: str = "postgresql+asyncpg://gdtrainer:gdtrainer@localhost:5432/gdtrainer"
    database_url_sync: str = "postgresql+psycopg2://gdtrainer:gdtrainer@localhost:5432/gdtrainer"

    # Redis / jobs
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    # Email (Brevo transactional API)
    brevo_api_key: str = ""
    brevo_sender_email: str = ""
    brevo_sender_name: str = "GD Trainer"
    frontend_base_url: str = "http://localhost:3000"

    # Storage
    storage_dir: str = "./storage"

    # Transcription (Day 2+)
    transcriber_provider: str = "local"
    whisper_model: str = "base.en"

    # Model layer (Day 4+)
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    embedding_model: str = "all-MiniLM-L6-v2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
