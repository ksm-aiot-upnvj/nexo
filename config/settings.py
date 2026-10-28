from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DISCORD_BOT_TOKEN: str = Field(default="", validation_alias="DISCORD_BOT_TOKEN")
    PORT: int = Field(default=8000, validation_alias="PORT")
    HOST: str = Field(default="0.0.0.0", validation_alias="HOST")
    DATABASE_URL: str = Field(
        default="******localhost:5432/nexo_db",
        validation_alias="DATABASE_URL",
    )
    GITHUB_SECRET: str = Field(default="", validation_alias="GITHUB_SECRET")

    PENGURUS_CHANNEL_ID: int = Field(default=0, validation_alias="PENGURUS_CHANNEL_ID")
    ANNOUNCEMENT_CHANNEL_ID: int = Field(
        default=0, validation_alias="ANNOUNCEMENT_CHANNEL_ID"
    )
    WEBHOOK_DEVLOGS_CHANNEL_ID: int = Field(
        default=0, validation_alias="WEBHOOK_DEVLOGS_CHANNEL_ID"
    )
    WEBHOOK_RELEASE_NOTES_CHANNEL_ID: int = Field(
        default=0, validation_alias="WEBHOOK_RELEASE_NOTES_CHANNEL_ID"
    )
    WELCOME_AND_RULES_CHANNEL_ID: int = Field(
        default=0, validation_alias="WELCOME_AND_RULES_CHANNEL_ID"
    )
    GENERATOR_CHANNEL_ID: int = Field(
        default=0, validation_alias="GENERATOR_CHANNEL_ID"
    )
    GENERATOR_STAGE_ID: int = Field(default=0, validation_alias="GENERATOR_STAGE_ID")
    CATEGORY_ID: int = Field(default=0, validation_alias="CATEGORY_ID")
    MEMBER_ROLE_ID: int = Field(default=0, validation_alias="MEMBER_ROLE_ID")
    PENGURUS_ROLE_ID: int = Field(default=0, validation_alias="PENGURUS_ROLE_ID")

    LLM_BASE_URL: str = Field(
        default="http://litellm-gateway:4000/v1",
        validation_alias="LLM_BASE_URL",
    )
    LLM_API_KEY: str = Field(default="", validation_alias="LLM_API_KEY")
    HF_TOKEN: str = Field(default="", validation_alias="HF_TOKEN")
    LLM_MODEL_NAME: str = Field(
        default="gemma-4-e2b-qat-q4_0-it",
        validation_alias="LLM_MODEL_NAME",
    )
    TOTAL_CONTEXT_WINDOW: int = Field(
        default=8192, validation_alias="TOTAL_CONTEXT_WINDOW"
    )
    MAX_GROUP_CONTEXT_CHARS: int = Field(
        default=10000, validation_alias="MAX_GROUP_CONTEXT_CHARS"
    )

    MCP_SERVER_URL: str = Field(
        default="http://127.0.0.1:8000", validation_alias="MCP_SERVER_URL"
    )
    GOOGLE_SERVICE_ACCOUNT_FILE: str = Field(
        default="credentials/service_account.json",
        validation_alias="GOOGLE_SERVICE_ACCOUNT_FILE",
    )
    GOOGLE_CALENDAR_ID: str | None = Field(
        default=None, validation_alias="GOOGLE_CALENDAR_ID"
    )

    PORTAINER_WEBHOOKS: str = Field(default="", validation_alias="PORTAINER_WEBHOOKS")
    WEBHOOK_DEVLOGS_CHANNEL: str = Field(
        default="", validation_alias="WEBHOOK_DEVLOGS_CHANNEL"
    )
    WEBHOOK_PORT: int = Field(default=8000, validation_alias="WEBHOOK_PORT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
