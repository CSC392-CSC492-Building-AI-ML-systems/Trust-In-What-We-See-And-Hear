"""Optional environment settings."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Trust In What We See And Hear API"
    environment: str = "development"
    supabase_url: str | None = None
    supabase_publishable_key: SecretStr | None = None
    # Bypasses Row Level Security. Backend only; never send it to a client.
    supabase_secret_key: SecretStr | None = None
    # Recorded on each profile as the privacy policy version the user accepted.
    privacy_policy_version: str = "2026-11"


@lru_cache
def get_settings() -> Settings:
    return Settings()
