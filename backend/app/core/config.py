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
    # Limits for a voice reference sample. The size limit also caps every
    # request body (see app/core/body_limit.py). Chatterbox Turbo needs more
    # than 5 seconds of reference and uses up to the first 15, so 15 covers both
    # Chatterbox variants.
    voice_sample_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    voice_sample_min_seconds: float = Field(default=15.0, gt=0)
    voice_sample_max_seconds: float = Field(default=60.0, gt=0)
    # A recording quieter than this on average (RMS, dB relative to full scale)
    # is treated as silent. Speech is usually -30 to -15 dBFS; even a quiet
    # microphone with pauses stays well above -50.
    voice_sample_min_dbfs: float = Field(default=-50.0, lt=0)
    # Uploads are converted to mono WAV at this rate: Chatterbox loads its
    # reference audio at 24 kHz and resamples it as it needs.
    voice_sample_rate: int = Field(default=24_000, ge=8_000, le=48_000)
    # ffmpeg decodes uploaded audio. A name on PATH or a full path.
    ffmpeg_path: str = "ffmpeg"

    # The voice pipeline (voice-pipeline/BACKEND.md). Calls cannot start
    # without both. The WebSocket base address, e.g. wss://pipeline.example,
    # is where `ws_url` points the browser.
    pipeline_ws_url: str | None = None
    # Shared secret the pipeline sends to /api/v1/internal (PIPELINE_BACKEND_SECRET there).
    pipeline_backend_secret: SecretStr | None = None
    # How long the token in `ws_url` can be claimed after the call is created.
    call_claim_valid_seconds: int = Field(default=120, gt=0)
    # How long the signed link to the persona's voice sample works.
    call_voice_url_valid_seconds: int = Field(default=300, ge=60)
    # A call still in progress this long after it started never got a result
    # (the pipeline caps calls at 600 s, plus time for scoring), so it is failed.
    call_result_timeout_seconds: int = Field(default=15 * 60, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
