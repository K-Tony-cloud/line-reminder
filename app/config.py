from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    line_channel_access_token: str
    line_channel_secret: str
    google_spreadsheet_id: str
    google_credentials_file: str = "credentials/service_account.json"
    google_backup_spreadsheet_id: str = ""
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    default_reminder_minutes: int = 60

    # extra="ignore" prevents Railway-injected env vars (RAILWAY_*, PORT, etc.)
    # from causing validation errors
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
