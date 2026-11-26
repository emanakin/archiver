from datetime import datetime
from functools import lru_cache
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./development.db"
    auth_database_url: str = ""
    auth_mode: Literal["legacy", "google"] = "legacy"
    google_oauth_client_file: str = ""
    bootstrap_owner_email: str = ""
    signup_open: bool = False
    signup_test_emails: str = ""
    internal_api_token: str = Field(min_length=24)
    app_origin: str = "http://localhost:8080"
    adapter_mode: Literal["mock", "read_only", "live"] = "read_only"
    worker_poll_seconds: int = Field(default=3, ge=1, le=60)
    dm_poll_interval_seconds: int = Field(default=900, ge=60)
    dm_spotify_refresh_minutes: int = Field(default=240, ge=60)
    dm_default_account_id: str = ""
    dm_default_from: AwareDatetime | None = None
    dm_auto_music: bool = False
    dm_caption_model: str = ""
    dm_caption_vision_model: str = ""
    dm_caption_sources: str = ""
    dm_caption_v2: bool = False
    dm_editorial_model: str = "qwen2.5:1.5b-instruct"
    dm_whisper_model_file: str = ""
    dm_caption_autopost: bool = False
    dm_reel_ingest: bool = False
    dm_reel_autopost: bool = False
    dm_spotify_playlist_id: str = Field(default="", pattern=r"^(?:[A-Za-z0-9]{22})?$")
    dm_self_receipts: bool = False
    session_store_path: str = "/var/lib/archive-desk/sessions"
    session_key_file: str = "/run/secrets/instagram_session_key"
    content_store_path: str = "/var/lib/archive-desk/content"
    content_storage_backend: Literal["local", "s3", "r2"] | None = None
    content_bucket: str = ""
    content_s3_endpoint: str | None = None
    content_s3_region: str = "us-east-1"
    content_s3_credentials_file: str | None = None
    content_r2_bucket: str = ""
    content_r2_endpoint: str = ""
    content_r2_credentials_file: str | None = None

    @field_validator("dm_default_from", mode="before")
    @classmethod
    def optional_cutover(cls, value):
        return value or None

    def dm_defaults_apply(self, account_id: str, message_at: datetime | None = None) -> bool:
        return (
            bool(self.dm_default_from)
            and account_id == self.dm_default_account_id
            and (message_at is None or message_at >= self.dm_default_from)
        )


@lru_cache
def settings():
    return Settings()
