"""Centralized application configuration.

Values are read from environment variables (or a local ``.env`` file) so the
same code runs unchanged across local, Docker, and production environments.
A fast framework shouldn't force a strange configuration story -- this is
just plain Pydantic settings.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Inventory API"
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/inventory"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    PRODUCT_CACHE_TTL_SECONDS: int = 300
    RATE_LIMIT_PER_MINUTE: int = 100

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
