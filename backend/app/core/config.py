"""Optional environment settings."""

from functools import lru_cache

from pydantic import Field, SecretStr
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
    # How long an accepted consent can be used before it expires unused. Each
    # consent is also single use. Placeholder until the team agrees a period.
    consent_valid_minutes: int = Field(default=24 * 60, gt=0)

    # Private Supabase Storage buckets for voice reference samples and any files
    # the voice pipeline derives from them. Only the backend can read them.
    voice_samples_bucket: str = "voice-samples"
    voice_models_bucket: str = "voice-models"
    # Limits for a voice reference sample. Placeholders until the voice
    # pipeline confirms what Chatterbox needs.
    voice_sample_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    voice_sample_min_seconds: float = Field(default=5.0, gt=0)
    voice_sample_max_seconds: float = Field(default=60.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
