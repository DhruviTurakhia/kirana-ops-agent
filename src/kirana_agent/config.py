from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    telegram_bot_token: str = ""
    openai_model: str = "gpt-5.6-terra"
    allow_all_telegram_users: bool = False
    authorized_telegram_user_ids: Annotated[tuple[int, ...], NoDecode] = Field(
        default_factory=tuple
    )
    database_path: Path = Path("./data/kirana.sqlite3")
    agent_session_database_path: Path = Path("./data/agent_sessions.sqlite3")
    artifact_output_dir: Path = Path("./output")
    store_timezone: str = "Asia/Kolkata"
    log_level: str = "INFO"

    @field_validator("authorized_telegram_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, value: object) -> object:
        if value in (None, ""):
            return ()
        if isinstance(value, int):
            return (value,)
        if isinstance(value, str):
            return tuple(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    def ensure_runtime_secrets(self) -> None:
        missing = []
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    def ensure_telegram_access_policy(self) -> None:
        if not self.allow_all_telegram_users and not self.authorized_telegram_user_ids:
            raise RuntimeError(
                "AUTHORIZED_TELEGRAM_USER_IDS must contain at least one numeric Telegram "
                "user ID when ALLOW_ALL_TELEGRAM_USERS is false."
            )
