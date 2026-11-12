import enum
from datetime import UTC, datetime
from uuid import UUID, uuid7

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class MessageRole(str, enum.Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "channel_id", name="uq_conversations_user_channel"),
        Index(
            "uq_conversations_user_channel_null",
            "user_id",
            unique=True,
            postgresql_where=text("channel_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # Rolling summary
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[Message]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role_enum"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(
        "Conversation", back_populates="messages"
    )


class ScheduledEvent(Base):
    __tablename__ = "scheduled_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    gcal_event_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Konfigurasi Kanal Broadcast
    broadcast_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    broadcast_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Metadata Acara
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    event_url: Mapped[str] = mapped_column(String(255), nullable=False)

    # Manajemen Interval (dalam menit)
    reminder_intervals: Mapped[list[int]] = mapped_column(
        JSON, default=list, nullable=False
    )
    reminders_sent: Mapped[list[int]] = mapped_column(
        JSON, default=list, nullable=False
    )

    # Konfigurasi Template, Klasifikasi & Target Role
    template_name: Mapped[str] = mapped_column(
        String(100), default="default_reminder.j2", nullable=False
    )
    target_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    classification_label: Mapped[str | None] = mapped_column(
        String(50), nullable=True, index=True
    )
    is_discord_event: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TokenUsageLog(Base):
    __tablename__ = "token_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class GitHubWebhookDelivery(Base):
    __tablename__ = "github_webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScheduledAnnouncement(Base):
    __tablename__ = "scheduled_announcements"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_embed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="General", nullable=False)
    deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    registration_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    guidebook_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reminders_sent: Mapped[list[int]] = mapped_column(
        JSON, default=list, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True, nullable=False
    )
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
