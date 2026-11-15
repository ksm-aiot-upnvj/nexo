from pydantic import BaseModel, Field


class DiscordThreadSchema(BaseModel):
    name: str = Field(..., description="Thread name (max 100 chars)")
    reason: str = Field(..., description="Reason for creating thread")


class DiscordPollSchema(BaseModel):
    question: str = Field(..., description="Poll question")
    options: list[str] = Field(..., description="Poll choices (max 10 items)")
    allow_multiselect: bool = Field(default=False, description="Allow multiple choices")
    duration_hours: int = Field(default=24, description="Duration in hours (1-168)")


class EndDiscordPollSchema(BaseModel):
    message_id: int | None = Field(
        default=None,
        description="Target poll message ID (omit to auto-find in current channel)",
    )


class ClearMessagesSchema(BaseModel):
    limit: int = Field(
        default=100,
        ge=1,
        le=100,
        description="Number of messages to scan/delete (maximum 100)",
    )
    only_today: bool = Field(
        default=False, description="Only delete messages sent today"
    )
